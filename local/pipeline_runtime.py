"""Host-side helpers used by `sandbox.py` for both the datagen flow and the Claude flow.

The two flows differ in whether Claude/Node tooling is layered on top, but they both:

- create a Hotdata sandbox via the typed SDK and capture workspace + sandbox ids,
- thread `HOTDATA_*` env vars + the api-key secret into a Dagger container,
- mount the same `container/runner.py` that imports `source.py`, runs a dlt pipeline against an in-memory DuckDB, and uploads each user-facing arrow→parquet table to the sandbox,
- parse the runner's last-stdout-line JSON summary.

This module owns those shared pieces plus the two image builders so the entry script reads as orchestration, not plumbing.
"""

# mypy: disable-error-code="no-untyped-def,arg-type"

from __future__ import annotations

import json
from typing import Any

import dagger
import hotdata
from dagger import dag
from hotdata.api_client import ApiClient
from hotdata.models.create_sandbox_request import CreateSandboxRequest
from hotdata.models.query_request import QueryRequest

# Pinned in one place so both `pyproject.toml` and the in-container `uv pip install`
# move together.
HOTDATA_PKG = "hotdata>=0.1.0,<0.2"

# Both flows share the same in-container layout: `source.py` lives in `WORKDIR`
# (where Claude works in the agent flow), and the trusted runner lands at
# `RUNNER_PATH` outside that workdir so an agent run can't see/edit it.
WORKDIR = "/workspace"
SOURCE_PATH = f"{WORKDIR}/source.py"
RUNNER_PATH = "/app/runner.py"

# Claude-flow specifics. The Claude Code CLI refuses bypassPermissions under
# root, so the Claude container runs as an unprivileged `agent` user with its
# own venv. The datagen flow doesn't need any of this.
PROMPT_PATH = f"{WORKDIR}/prompt.md"
VENV_PATH = f"{WORKDIR}/.venv"
AGENT_USER = "agent"
AGENT_UID = "1000"
AGENT_HOME = f"/home/{AGENT_USER}"
AGENT_OWNER = f"{AGENT_USER}:{AGENT_USER}"


class HotdataSession:
    """Workspace-scoped Hotdata API client with sandbox + verify operations.

    Used as a context manager: opens a bootstrap client to pick the active workspace, then a persistent workspace-scoped client. After `create_sandbox`, subsequent reads are scoped to that sandbox via `X-Session-Id` (typed `session_id=` on Configuration); writes still need a residual `X-Sandbox-Id` default header until the SDK ships a `sandbox_id=` kwarg.
    """

    def __init__(self, api_key: str, host: str | None = None):
        self._api_key = api_key
        self._host = host
        self._cfg: hotdata.Configuration
        self._client_cm: Any = None
        self.api_client: ApiClient
        self.workspace_id: str = ""
        self.workspace_name: str = ""
        self.sandbox_id: str = ""

    def __enter__(self) -> "HotdataSession":
        boot_cfg = hotdata.Configuration(api_key=self._api_key, host=self._host)
        with hotdata.ApiClient(boot_cfg) as boot:
            workspaces = hotdata.WorkspacesApi(boot).list_workspaces().workspaces or []
            if not workspaces:
                raise RuntimeError("No workspaces available for this API key.")
            ws = next((w for w in workspaces if w.active), workspaces[0])

        self.workspace_id = ws.public_id
        self.workspace_name = ws.name

        self._cfg = hotdata.Configuration(
            api_key=self._api_key,
            host=self._host,
            workspace_id=ws.public_id,
        )
        self._client_cm = hotdata.ApiClient(self._cfg)
        self.api_client = self._client_cm.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._client_cm is not None:
            self._client_cm.__exit__(*exc)

    @property
    def host(self) -> str:
        """Resolved host URL — env-var override if set, else the SDK default."""
        return self._host or hotdata.Configuration().host

    def create_sandbox(self, name: str) -> str:
        resp = hotdata.SandboxesApi(self.api_client).create_sandbox(
            CreateSandboxRequest(name=name)
        )
        self.sandbox_id = resp.sandbox.public_id
        self._cfg.session_id = self.sandbox_id
        # Residual until SDK adds `sandbox_id=` kwarg on Configuration (sibling
        # of `session_id=`); X-Sandbox-Id scopes dataset writes.
        self.api_client.set_default_header("X-Sandbox-Id", self.sandbox_id)
        return self.sandbox_id

    def preview(self, tables: list[str]) -> dict[str, list[list[object]]]:
        """Preview rows from each uploaded table to confirm the load."""
        query_api = hotdata.QueryApi(self.api_client)
        results: dict[str, list[list[object]]] = {}
        for table in sorted(set(tables)):
            sql = f"SELECT * FROM datasets.{self.sandbox_id}.{table} LIMIT 10"
            resp = query_api.query(QueryRequest(sql=sql))
            results[table] = [[*resp.columns]] + [list(r) for r in (resp.rows or [])]
        return results


def with_source(
    container: dagger.Container,
    source_file: dagger.File,
    *,
    owner: str | None = None,
) -> dagger.Container:
    """Copy `source.py` into the container at `SOURCE_PATH` (writable).

    `with_file` is used instead of `with_mounted_file` so the Claude flow can
    edit the file in place during its exec.
    """
    kwargs: dict[str, Any] = {}
    if owner is not None:
        kwargs["owner"] = owner
    return container.with_file(SOURCE_PATH, source_file, **kwargs)


def add_hotdata_env(
    container: dagger.Container,
    *,
    api_key_secret: dagger.Secret,
    host: str,
    workspace_id: str,
    sandbox_id: str,
    run_id: str,
) -> dagger.Container:
    """Thread the env vars + secret the in-container runner reads."""
    return (
        container.with_env_variable("HOTDATA_API_URL", host)
        .with_env_variable("HOTDATA_WORKSPACE_ID", workspace_id)
        .with_env_variable("HOTDATA_SANDBOX_ID", sandbox_id)
        .with_env_variable("DLT_RUN_ID", run_id)
        .with_secret_variable("HOTDATA_API_KEY", api_key_secret)
    )


def mount_runner(
    container: dagger.Container,
    runner_file: dagger.File,
    *,
    owner: str | None = None,
) -> dagger.Container:
    """Mount the trusted runner at `RUNNER_PATH` and exec it.

    Call this on the *post-agent* container in the Claude flow so the runner
    isn't visible during Claude's exec.
    """
    kwargs: dict[str, Any] = {}
    if owner is not None:
        kwargs["owner"] = owner
    return container.with_mounted_file(
        RUNNER_PATH, runner_file, **kwargs
    ).with_exec(["python", RUNNER_PATH])


def parse_runner_summary(stdout: str) -> dict[str, Any]:
    """Runner's last stdout line is JSON: {pipeline_name, run_id, tables, datasets}."""
    last = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if not last.startswith("{"):
        raise RuntimeError(
            f"runner did not emit JSON summary; last stdout line: {last!r}"
        )
    return json.loads(last)


def host_file(relpath: str) -> dagger.File:
    """Resolve a path relative to this module's directory as a `dagger.File`."""
    from pathlib import Path

    here = Path(__file__).resolve().parent
    return dag.host().file(str(here / relpath))


def build_datagen_container(source_file: dagger.File) -> dagger.Container:
    """Minimal image: dlt[duckdb] + duckdb + pyarrow + hotdata, with `source.py` copied into `WORKDIR`. The runner runs as root since there's no agent involved."""
    return (
        dag.container()
        .from_("ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
        .with_mounted_cache("/root/.cache/uv", dag.cache_volume("dlt-datagen-uv"))
        .with_env_variable("UV_LINK_MODE", "copy")
        .with_exec(
            [
                "uv",
                "pip",
                "install",
                "--system",
                "dlt[duckdb]",
                "duckdb",
                "pyarrow",
                HOTDATA_PKG,
            ]
        )
        .with_workdir(WORKDIR)
        .with_(lambda c: with_source(c, source_file))
    )


def build_claude_container(
    *,
    prompt_file: dagger.File,
    source_file: dagger.File,
    anthropic_secret: dagger.Secret,
    github_secret: dagger.Secret | None,
    model: str,
    effort: str,
    output_format: str,
) -> tuple[dagger.Container, str]:
    """Image with Claude Code + Node + dlt[workspace] + dlt-ai toolkits, plus a writable `source.py` and the user's prompt mounted at `PROMPT_PATH`.

    Returns `(container, claude_cmd)` — the caller threads hotdata env via `add_hotdata_env`, execs `claude_cmd`, then mounts the runner on the post-Claude layer with `mount_runner(..., owner=AGENT_OWNER)`.
    """
    venv_path_env = (
        f"{VENV_PATH}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    claude_cmd = (
        f'exec claude -p "$(cat {PROMPT_PATH})" '
        f"--model {model} "
        f"--effort {effort} "
        f"--output-format {output_format} "
        "--permission-mode bypassPermissions "
        + ("--verbose" if output_format == "stream-json" else "")
    ).strip()

    base = (
        dag.container()
        .from_("ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
        .with_mounted_cache("/root/.npm", dag.cache_volume("dlt-claude-npm"))
        .with_env_variable("UV_LINK_MODE", "copy")
        .with_exec(
            [
                "sh",
                "-c",
                "apt-get update && apt-get install -y --no-install-recommends "
                "git curl ca-certificates gnupg && rm -rf /var/lib/apt/lists/*",
            ]
        )
        .with_exec(
            [
                "sh",
                "-c",
                "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - "
                "&& apt-get install -y --no-install-recommends nodejs "
                "&& rm -rf /var/lib/apt/lists/*",
            ]
        )
        .with_exec(["npm", "install", "-g", "@anthropic-ai/claude-code"])
        .with_exec(
            [
                "sh",
                "-c",
                f"useradd -m -u {AGENT_UID} {AGENT_USER} "
                f"&& mkdir -p {WORKDIR} /app "
                f"&& chown -R {AGENT_OWNER} {WORKDIR} /app {AGENT_HOME}",
            ]
        )
        .with_user(AGENT_USER)
        .with_env_variable("HOME", AGENT_HOME)
        .with_workdir(WORKDIR)
        .with_mounted_cache(
            f"{AGENT_HOME}/.cache/uv",
            dag.cache_volume("dlt-claude-uv-agent"),
            owner=AGENT_OWNER,
        )
        .with_exec(["uv", "venv", VENV_PATH])
        .with_env_variable("VIRTUAL_ENV", VENV_PATH)
        .with_env_variable("PATH", venv_path_env)
        .with_exec(
            [
                "uv",
                "pip",
                "install",
                "dlt[workspace]",
                "duckdb",
                "pyarrow",
                HOTDATA_PKG,
            ]
        )
        .with_exec(["dlt", "ai", "init", "--agent=claude"])
        .with_exec(["dlt", "ai", "toolkit", "list"])
        .with_exec(["dlt", "ai", "toolkit", "rest-api-pipeline", "install"])
        .with_exec(["dlt", "ai", "toolkit", "data-exploration", "install"])
        .with_mounted_file(PROMPT_PATH, prompt_file, owner=AGENT_OWNER)
        .with_(lambda c: with_source(c, source_file, owner=AGENT_OWNER))
        .with_secret_variable("ANTHROPIC_API_KEY", anthropic_secret)
    )
    if github_secret is not None:
        base = base.with_secret_variable("GITHUB_TOKEN", github_secret)
    return base, claude_cmd
