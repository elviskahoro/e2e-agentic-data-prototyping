"""Single entry: run the demo pipeline in a Dagger container.

Default flow — `uv run python sandbox.py`:
    Build a minimal container, copy the canonical `source.py` in, and run the trusted
    `runtime/runner.py` against a fresh per-run Hotdata sandbox. Previews the
    uploaded tables on the host.

Claude flow — `uv run python sandbox.py --with-prompt <prompt.md>`:
    Layer Claude Code + Node + dlt-ai toolkits on top, exec Claude with the prompt so
    it can modify `source.py` (mounted writable at /workspace/source.py), then mount
    the trusted runner on the *post-Claude* layer so the agent never sees it. Persists
    the chat transcript to `data/<run_id>.{ext}` and prints copy-pasteable `hotdata`
    CLI commands for the resulting sandbox.

Both flows share sandbox creation, env-threading, and the runner contract via
the `runtime` package — see `runtime/__init__.py` for why the runtime is split
into a host half and an in-container half.
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

from ibis_examples import run_examples as run_ibis_examples
from runtime import (
    ClaudeImage,
    DatagenImage,
    HotdataSession,
    Runner,
    host_file,
)

os.environ.setdefault("DAGGER_NO_NAG", "1")

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_EFFORT = "low"
PREVIEW_COLS = 5

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
        nargs="?",
        default=None,
        const=Path("prompt.md"),
        metavar="PROMPT_FILE",
        help="Path to a prompt file. If set, switches from the default datagen flow to the Claude flow (modifies source.py via the agent before the runner uploads). Bare flag defaults to prompt.md.",
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


class BaseFlow:
    """Shared skeleton: open Hotdata session, build & run the trusted runner, preview tables.

    Subclasses provide their image-specific build (and optional agent exec) via
    `_build_runner_container`, and may append output via `_print_extras`.
    """

    RUN_ID_BYTES = 10
    AGENT_OWNER: str | None = None

    def __init__(
        self,
        *,
        api_key: str,
        host_override: str | None,
        name_override: str | None,
    ) -> None:
        self.api_key = api_key
        self.host_override = host_override
        self.run_id = (
            f"{int(time.time() * 1000):012x}{secrets.token_hex(self.RUN_ID_BYTES)}"
        )
        self.sandbox_name = self._derive_sandbox_name(name_override)

    def _derive_sandbox_name(self, override: str | None) -> str:
        return slugify(override) if override else f"agent_{self.run_id}"

    async def run(self) -> None:
        self._log_startup()

        with HotdataSession(self.api_key, self.host_override) as session:
            sandbox_id = session.create_sandbox(self.sandbox_name)
            print(
                f"→ workspace={session.workspace_id} sandbox={sandbox_id}",
                file=sys.stderr,
            )

            async with dagger.connection(dagger.Config(log_output=sys.stderr)):
                runner_container = await self._build_runner_container(session)
                runner_stdout = await runner_container.stdout()

            summary = Runner.parse_summary(runner_stdout)
            # The runner reports the managed database it created by *id*, not by
            # the human-readable sandbox name: the Hotdata API doesn't round-trip
            # a database's description, so only the id resolves. Use it for every
            # post-run lookup below.
            database_id = summary["database_id"]
            print(
                f"→ runner uploaded {len(summary['tables'])} tables "
                f"from pipeline '{summary['pipeline_name']}' "
                f"into database {database_id}",
                file=sys.stderr,
            )
            previews = session.preview(
                database_name=database_id,
                tables=summary["tables"],
                max_columns=PREVIEW_COLS,
            )
            workspace_id = session.workspace_id
            workspace_name = session.workspace_name

            # Bonus surface: query the same tables through hotdata-ibis with
            # Python expressions instead of raw SQL. Runs inside the session so
            # the connection still has a live API client / sandbox header.
            run_ibis_examples(
                session=session,
                database_name=database_id,
                tables=summary["tables"],
            )

        self._print_output(
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            sandbox_id=sandbox_id,
            database_id=database_id,
            summary=summary,
            previews=previews,
        )

    def _log_startup(self) -> None:
        print(f"→ run {self.run_id}", file=sys.stderr)

    async def _build_runner_container(self, session: HotdataSession):
        hotdata_secret = dag.set_secret("hotdata-api-key", self.api_key)
        agent_container = await self._build_agent_container(session, hotdata_secret)
        return Runner.mount_and_exec(
            agent_container,
            Runner.host_file(),
            owner=self.AGENT_OWNER,
        )

    async def _build_agent_container(
        self, session: HotdataSession, hotdata_secret: dagger.Secret
    ):
        """Return the env-injected container the runner will be mounted on. Subclass-specific image build (and optional agent exec) happens here."""
        raise NotImplementedError

    def _print_output(
        self,
        *,
        workspace_id: str,
        workspace_name: str,
        sandbox_id: str,
        database_id: str,
        summary: dict,
        previews: dict[str, list[list[object]]],
    ) -> None:
        sys.stdout.write("\n=== Hotdata sandbox ===\n")
        sys.stdout.write(f"workspace: {workspace_name} ({workspace_id})\n")
        sys.stdout.write(f"sandbox:   {self.sandbox_name} ({sandbox_id})\n")
        sys.stdout.write(f"database:  {database_id}\n")
        sys.stdout.write(f"pipeline:  {summary['pipeline_name']}\n")

        sys.stdout.write(
            "\n=== Query the loaded data (run each query separately) ===\n"
        )
        # Scope each query to the managed database by id (`-d`); inside that scope
        # tables live under the built-in `default.public` catalog.
        for table in summary["tables"]:
            cols = ", ".join(str(c) for c in previews[table][0][:PREVIEW_COLS])
            sys.stdout.write("# ---\n")
            sys.stdout.write(
                f'hotdata query "SELECT {cols} FROM default.public.{table} LIMIT 10" '
                f"-d {database_id}\n"
            )

        sys.stdout.write("\n=== Preview ===\n")
        for table, rows in previews.items():
            sys.stdout.write(f"[{table}]\n{self._format_preview(rows)}\n\n")

        self._print_extras()
        sys.stdout.flush()

    def _print_extras(self) -> None:
        return None

    @staticmethod
    def _format_preview(columns_plus_rows: list[list[object]]) -> str:
        return "\n".join(
            "\t".join("" if v is None else str(v) for v in r) for r in columns_plus_rows
        )


class DatagenFlow(BaseFlow):
    """Default flow: build a minimal container and run the trusted runner against a fresh sandbox. No agent in the loop."""

    async def _build_agent_container(
        self, session: HotdataSession, hotdata_secret: dagger.Secret
    ):
        container = DatagenImage.build(host_file("source.py"))
        return session.inject_env(
            container,
            api_key_secret=hotdata_secret,
            database_name=self.sandbox_name,
            run_id=self.run_id,
        )


class ClaudeFlow(BaseFlow):
    """Claude flow: layer Claude+Node on the base image, exec Claude over `source.py`, then mount the trusted runner on the post-Claude layer so the agent never sees it."""

    RUN_ID_BYTES = 6
    AGENT_OWNER = ClaudeImage.AGENT_OWNER

    def __init__(
        self,
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
        self.anthropic_key = anthropic_key
        self.github_token = os.environ.get("GITHUB_TOKEN", "").strip()
        self.prompt_path = prompt_path
        self.model = model
        self.effort = effort
        self.output_format = output_format
        super().__init__(
            api_key=api_key, host_override=host_override, name_override=name_override
        )
        self.transcript_path = (
            DATA_DIR / f"{self.run_id}.{TRANSCRIPT_EXT[output_format]}"
        )

    def _derive_sandbox_name(self, override: str | None) -> str:
        slug = slugify(override) if override else slugify(self.prompt_path.stem)
        return f"{slug}_{self.run_id[-8:]}"

    def _log_startup(self) -> None:
        super()._log_startup()
        print(f"→ prompt: {self.prompt_path}", file=sys.stderr)
        print(f"→ model:  {self.model} (effort={self.effort})", file=sys.stderr)
        if self.github_token:
            print(
                "→ GITHUB_TOKEN found on host — passing through as secret",
                file=sys.stderr,
            )
        else:
            print(
                "→ GITHUB_TOKEN not set — GitHub API calls will be unauthenticated (60 req/hr)",
                file=sys.stderr,
            )

    async def _build_agent_container(
        self, session: HotdataSession, hotdata_secret: dagger.Secret
    ):
        anthropic_secret = dag.set_secret("anthropic-api-key", self.anthropic_key)
        github_secret = (
            dag.set_secret("github-token", self.github_token)
            if self.github_token
            else None
        )

        base, claude_cmd = ClaudeImage.build(
            prompt_file=dag.host().file(str(self.prompt_path)),
            source_file=host_file("source.py"),
            anthropic_secret=anthropic_secret,
            github_secret=github_secret,
            model=self.model,
            effort=self.effort,
            output_format=self.output_format,
        )
        base = session.inject_env(
            base,
            api_key_secret=hotdata_secret,
            database_name=self.sandbox_name,
            run_id=self.run_id,
        )

        # Claude runs first; the runner is mounted only on the post-Claude layer
        # (in BaseFlow) so the agent can't read it during its exec. Persist the
        # transcript before the runner runs so it survives a runner failure.
        # `redirect_stdout` keeps the stream-json out of Dagger's progress log —
        # the transcript materializes as a single file when the exec finishes.
        after_claude = base.with_exec(
            ["sh", "-c", claude_cmd], redirect_stdout="/tmp/claude.jsonl"
        )
        claude_stdout = await after_claude.file("/tmp/claude.jsonl").contents()
        self._write_transcript(claude_stdout)
        return after_claude

    def _write_transcript(self, claude_stdout: str) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        text = claude_stdout if claude_stdout.endswith("\n") else claude_stdout + "\n"
        self.transcript_path.write_text(text)
        print(f"→ wrote claude transcript to {self.transcript_path}", file=sys.stderr)

    def _print_extras(self) -> None:
        sys.stdout.write("\n=== Replay the chat as markdown ===\n")
        sys.stdout.write(
            f"{TRANSCRIPT_TO_MARKDOWN_SCRIPT} {self.transcript_path} | glow -\n"
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
        await ClaudeFlow(
            api_key=api_key,
            host_override=host_override,
            name_override=args.name,
            prompt_path=prompt_path,
            model=args.model,
            effort=args.effort,
            output_format=args.output_format,
        ).run()
    else:
        await DatagenFlow(
            api_key=api_key,
            host_override=host_override,
            name_override=args.name,
        ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
