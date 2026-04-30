"""Single entry: run the demo pipeline in a Dagger container.

Default flow — `uv run python sandbox.py`:
    Build a minimal container, copy the canonical `source.py` in, and run the trusted
    `container/runner.py` against a fresh per-run Hotdata sandbox. Previews the
    uploaded tables on the host.

Claude flow — `uv run python sandbox.py --with-prompt <prompt.md>`:
    Layer Claude Code + Node + dlt-ai toolkits on top, exec Claude with the prompt so
    it can modify `source.py` (mounted writable at /workspace/source.py), then mount
    the trusted runner on the *post-Claude* layer so the agent never sees it. Persists
    the chat transcript to `data/<run_id>.{ext}` and prints copy-pasteable `hotdata`
    CLI commands for the resulting sandbox.

Both flows share sandbox creation, env-threading, and the runner contract via
`pipeline_runtime`.
"""

# mypy: disable-error-code="no-untyped-def,arg-type"

import argparse
import asyncio
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Sequence

import dagger
from dagger import dag

from pipeline_runtime import (
    AGENT_OWNER,
    HotdataSession,
    add_hotdata_env,
    build_claude_container,
    build_datagen_container,
    host_file,
    mount_runner,
    parse_runner_summary,
)

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_EFFORT = "low"

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
TRANSCRIPT_TO_MARKDOWN_SCRIPT = SCRIPT_DIR / "transcript_to_markdown.sh"
TRANSCRIPT_EXT = {"stream-json": "jsonl", "json": "json", "text": "txt"}


def slugify(s: str, max_length: int = 40) -> str:
    """Lowercase + collapse non-alphanumerics to underscores. Used to derive a sandbox name from the prompt filename."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return (cleaned[:max_length] or "agent").strip("_")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--with-prompt",
        type=Path,
        default=None,
        metavar="PROMPT_FILE",
        help="Path to a prompt file. If set, switches from the default datagen flow to the Claude flow (modifies source.py via the agent before the runner uploads).",
    )
    p.add_argument(
        "--name",
        default=None,
        help="Override the Hotdata sandbox name slug (defaults: 'agent_<run_id>' for datagen, slug-of-prompt-stem for Claude).",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL),
        help=f"Claude model alias or full id (default: {DEFAULT_MODEL}). Only used with --with-prompt.",
    )
    p.add_argument(
        "--effort",
        default=os.environ.get("CLAUDE_EFFORT", DEFAULT_EFFORT),
        choices=["low", "medium", "high", "xhigh", "max"],
        help=f"Claude --effort level (default: {DEFAULT_EFFORT}). Only used with --with-prompt.",
    )
    p.add_argument(
        "--output-format",
        default="stream-json",
        choices=["text", "json", "stream-json"],
        help="Claude --output-format value (default: stream-json). Only used with --with-prompt.",
    )
    return p.parse_args(argv)


def _format_preview(columns_plus_rows: list[list[object]]) -> str:
    return "\n".join(
        "\t".join("" if v is None else str(v) for v in r) for r in columns_plus_rows
    )


def print_hotdata_query_commands(
    workspace_id: str,
    workspace_name: str,
    sandbox_id: str,
    sandbox_name: str,
    pipeline_name: str,
    tables: list[str],
    transcript_path: Path,
) -> None:
    sys.stdout.write("\n=== Hotdata sandbox ===\n")
    sys.stdout.write(f"workspace: {workspace_name} ({workspace_id})\n")
    sys.stdout.write(f"sandbox:   {sandbox_name} ({sandbox_id})\n")
    sys.stdout.write(f"pipeline:  {pipeline_name}\n")
    sys.stdout.write("\n=== Query the loaded data (run each query separately) ===\n")
    sys.stdout.write(
        f"HOTDATA_SANDBOX={sandbox_id} hotdata datasets list -w {workspace_id}\n"
    )
    for table in tables:
        sys.stdout.write("# ---\n")
        sys.stdout.write(
            f'HOTDATA_SANDBOX={sandbox_id} hotdata query -w {workspace_id} '
            f'"SELECT * FROM datasets.{sandbox_id}.{table} LIMIT 10"\n'
        )
    sys.stdout.write("\n=== Replay the chat as markdown ===\n")
    sys.stdout.write(
        f"{TRANSCRIPT_TO_MARKDOWN_SCRIPT} {transcript_path} | glow -\n"
    )
    sys.stdout.flush()


async def run_datagen(
    *,
    api_key: str,
    host_override: str | None,
    name_override: str | None,
) -> None:
    run_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(10)}"
    sandbox_name = slugify(name_override) if name_override else f"agent_{run_id}"
    print(f"→ run {run_id}", file=sys.stderr)

    with HotdataSession(api_key, host_override) as session:
        sandbox_id = session.create_sandbox(sandbox_name)
        print(
            f"→ workspace={session.workspace_id} sandbox={sandbox_id}",
            file=sys.stderr,
        )

        async with dagger.connection(dagger.Config(log_output=sys.stderr)):
            api_key_secret = dag.set_secret("hotdata-api-key", api_key)
            container = build_datagen_container(host_file("source.py"))
            container = add_hotdata_env(
                container,
                api_key_secret=api_key_secret,
                host=session.host,
                workspace_id=session.workspace_id,
                sandbox_id=sandbox_id,
                run_id=run_id,
            )
            container = mount_runner(container, host_file("container/runner.py"))
            stdout = await container.stdout()

        summary = parse_runner_summary(stdout)
        print(
            f"→ runner uploaded {len(summary['tables'])} tables "
            f"from pipeline '{summary['pipeline_name']}'",
            file=sys.stderr,
        )
        previews = session.preview(summary["tables"])

    print("\n=== preview ===", flush=True)
    for table, rows in previews.items():
        print(f"[{table}]\n{_format_preview(rows)}\n", flush=True)


async def run_claude(
    *,
    api_key: str,
    host_override: str | None,
    name_override: str | None,
    prompt_path: Path,
    model: str,
    effort: str,
    output_format: str,
) -> None:
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not anthropic_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY must be set (export ANTHROPIC_API_KEY=...) when --with-prompt is used."
        )
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()

    run_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(6)}"
    name_slug = slugify(name_override) if name_override else slugify(prompt_path.stem)
    sandbox_name = f"{name_slug}_{run_id[-8:]}"

    print(f"→ prompt: {prompt_path}", file=sys.stderr)
    print(f"→ model:  {model} (effort={effort})", file=sys.stderr)
    print(f"→ creating hotdata sandbox {sandbox_name}", file=sys.stderr)
    if github_token:
        print("→ GITHUB_TOKEN found on host — passing through as secret", file=sys.stderr)
    else:
        print(
            "→ GITHUB_TOKEN not set — GitHub API calls will be unauthenticated (60 req/hr)",
            file=sys.stderr,
        )

    with HotdataSession(api_key, host_override) as session:
        sandbox_id = session.create_sandbox(sandbox_name)
        workspace_id = session.workspace_id
        workspace_name = session.workspace_name
        host = session.host
        print(f"→ workspace={workspace_id} sandbox={sandbox_id}", file=sys.stderr)

    async with dagger.connection(dagger.Config(log_output=sys.stderr)):
        anthropic_secret = dag.set_secret("anthropic-api-key", anthropic_key)
        hotdata_secret = dag.set_secret("hotdata-api-key", api_key)
        github_secret = (
            dag.set_secret("github-token", github_token) if github_token else None
        )

        base, claude_cmd = build_claude_container(
            prompt_file=dag.host().file(str(prompt_path)),
            source_file=host_file("source.py"),
            anthropic_secret=anthropic_secret,
            github_secret=github_secret,
            model=model,
            effort=effort,
            output_format=output_format,
        )
        base = add_hotdata_env(
            base,
            api_key_secret=hotdata_secret,
            host=host,
            workspace_id=workspace_id,
            sandbox_id=sandbox_id,
            run_id=run_id,
        )

        # Claude runs first; runner.py is mounted only on the post-Claude layer
        # so the agent can't read it during its exec.
        after_claude = base.with_exec(["sh", "-c", claude_cmd])
        after_runner = mount_runner(
            after_claude, host_file("container/runner.py"), owner=AGENT_OWNER
        )

        claude_stdout = await after_claude.stdout()

        # Persist the transcript before the runner runs — if the runner fails
        # we still want the transcript on disk for debugging.
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        transcript_path = DATA_DIR / f"{run_id}.{TRANSCRIPT_EXT[output_format]}"
        transcript_path.write_text(
            claude_stdout if claude_stdout.endswith("\n") else claude_stdout + "\n"
        )
        print(f"→ wrote claude transcript to {transcript_path}", file=sys.stderr)

        runner_stdout = await after_runner.stdout()

    summary = parse_runner_summary(runner_stdout)
    print(
        f"→ runner uploaded {len(summary['tables'])} tables "
        f"from pipeline '{summary['pipeline_name']}'",
        file=sys.stderr,
    )

    print_hotdata_query_commands(
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        sandbox_id=sandbox_id,
        sandbox_name=sandbox_name,
        pipeline_name=summary["pipeline_name"],
        tables=summary["tables"],
        transcript_path=transcript_path,
    )


async def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)

    api_key = os.environ.get("HOTDATA_API_KEY", "").strip()
    if not api_key:
        print(
            "HOTDATA_API_KEY must be set (export HOTDATA_API_KEY=...) before running.",
            file=sys.stderr,
        )
        return 2
    host_override = os.environ.get("HOTDATA_API_URL")

    if args.with_prompt is not None:
        prompt_path = args.with_prompt.expanduser().resolve()
        if not prompt_path.is_file():
            print(f"Prompt file not found: {prompt_path}", file=sys.stderr)
            return 2
        await run_claude(
            api_key=api_key,
            host_override=host_override,
            name_override=args.name,
            prompt_path=prompt_path,
            model=args.model,
            effort=args.effort,
            output_format=args.output_format,
        )
    else:
        await run_datagen(
            api_key=api_key,
            host_override=host_override,
            name_override=args.name,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
