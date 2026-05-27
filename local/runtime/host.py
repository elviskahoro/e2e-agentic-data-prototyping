"""Host half of the pipeline runtime.

See `runtime/__init__.py` for why this is split from `runtime/runner.py`
(short version: trust boundary — the agent must not be able to read the
runner). This module runs on the host: it builds Dagger images, opens the
Hotdata sandbox, threads env vars + secrets into the container, mounts the
runner on the *post-agent* layer, and parses the runner's JSON summary.

Helpers are grouped by the entity they belong to: `Source` (the user-facing
dlt script), `Runner` (the host-side view of the trusted in-container script),
`HotdataSession` (workspace + sandbox-scoped API client), and
`DatagenImage` / `ClaudeImage` (the two image builders).
"""

# mypy: disable-error-code="no-untyped-def,arg-type"

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import dagger
import hotdata
from dagger import dag
from hotdata.api_client import ApiClient
from hotdata.models.create_sandbox_request import CreateSandboxRequest
from hotdata.models.query_request import QueryRequest
from hotdata_runtime.client import HotdataClient as RuntimeHotdataClient

# Pinned in one place so both `pyproject.toml` and the in-container `uv pip install`
# move together.
HOTDATA_PKG = "hotdata>=0.2.2,<0.3"
HOTDATA_DLT_DESTINATION_PKG = "hotdata-dlt-destination>=0.3.0,<0.4"

# Shared in-container layout: `source.py` lives in `WORKDIR` (where Claude works in
# the agent flow); the trusted runner lands at `Runner.PATH` outside that workdir
# so an agent run can't see/edit it.
WORKDIR = "/workspace"

# Demo root on the host — `Path(__file__).parent` is `runtime/`, so go up one.
DEMO_ROOT = Path(__file__).resolve().parent.parent


class Source:
    """The user-facing dlt source script. Lives at `Source.PATH` inside `WORKDIR`, writable so the Claude flow can edit it in place."""

    PATH = f"{WORKDIR}/source.py"

    @staticmethod
    def copy_into(
        container: dagger.Container,
        source_file: dagger.File,
        *,
        owner: str | None = None,
    ) -> dagger.Container:
        """Copy `source.py` into the container at `Source.PATH` (writable).

        `with_file` is used instead of `with_mounted_file` so the Claude flow can
        edit the file in place during its exec.
        """
        kwargs: dict[str, Any] = {}
        if owner is not None:
            kwargs["owner"] = owner
        return container.with_file(Source.PATH, source_file, **kwargs)


class Runner:
    """Host-side view of the trusted in-container runner (`runtime/runner.py`).

    The runner imports `source.py`, runs a dlt pipeline against an in-memory
    DuckDB, and uploads each user-facing arrow→parquet table to the sandbox.
    The agent must never see it — see the package docstring for the trust
    boundary that motivates the split.
    """

    PATH = "/app/runner.py"
    HOST_PATH = "runtime/runner.py"

    @staticmethod
    def host_file() -> dagger.File:
        """The runner script as a `dagger.File`, resolved from the demo root."""
        return host_file(Runner.HOST_PATH)

    @staticmethod
    def mount_and_exec(
        container: dagger.Container,
        runner_file: dagger.File,
        *,
        owner: str | None = None,
    ) -> dagger.Container:
        """Mount the trusted runner at `Runner.PATH` and exec it.

        Call this on the *post-agent* container in the Claude flow so the runner
        isn't visible during Claude's exec.
        """
        kwargs: dict[str, Any] = {}
        if owner is not None:
            kwargs["owner"] = owner
        return container.with_mounted_file(
            Runner.PATH, runner_file, **kwargs
        ).with_exec(["python", Runner.PATH])

    @staticmethod
    def parse_summary(stdout: str) -> dict[str, Any]:
        """Runner's last stdout line is JSON: {pipeline_name, run_id, tables, datasets}."""
        last = stdout.strip().splitlines()[-1] if stdout.strip() else ""
        if not last.startswith("{"):
            raise RuntimeError(
                f"runner did not emit JSON summary; last stdout line: {last!r}"
            )
        return json.loads(last)


class HotdataSession:
    """Workspace-scoped Hotdata API client with sandbox + verify operations.

    Used as a context manager: opens a bootstrap client to pick the active workspace, then a persistent workspace-scoped client. The sandbox is still created for run grouping and for the existing workspace preview flow, but the dlt destination now writes into a managed database directly.
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

    def inject_env(
        self,
        container: dagger.Container,
        *,
        api_key_secret: dagger.Secret,
        database_name: str,
        run_id: str,
    ) -> dagger.Container:
        """Thread the env vars + secret the in-container runner reads.

        Reads `host`, `workspace_id`, and `sandbox_id` off `self`; safe to call
        after `__exit__` since these attributes persist.
        """
        return (
            container.with_env_variable("HOTDATA_API_URL", self.host)
            .with_env_variable("HOTDATA_WORKSPACE", self.workspace_id)
            .with_env_variable("HOTDATA_DATABASE", database_name)
            .with_env_variable("DLT_RUN_ID", run_id)
            .with_secret_variable("HOTDATA_API_KEY", api_key_secret)
        )

    def ibis_connect(self, database_name: str):
        """Return an `ibis.hotdata` connection bound to the managed database created for this sandbox.

        Resolves the managed database id via the runtime client and passes it to
        `ibis.hotdata.connect` so `con.table(name, database=("default", "public"))`
        works without further wiring.
        """
        import ibis  # noqa: PLC0415  -- optional dep for the demo's ibis preview

        runtime_client = RuntimeHotdataClient(
            self._api_key, self.workspace_id, host=self.host
        )
        try:
            db = runtime_client.resolve_managed_database(database_name)
        finally:
            runtime_client.close()

        return ibis.hotdata.connect(
            api_url=self.host,
            token=self._api_key,
            workspace_id=self.workspace_id,
            session_id=self.sandbox_id or None,
            database_id=db.id,
        )

    def preview(
        self,
        *,
        database_name: str,
        tables: list[str],
        max_columns: int | None = None,
        limit: int = 10,
    ) -> dict[str, list[list[object]]]:
        """Preview rows from each uploaded table to confirm the load.

        When `max_columns` is set, a `SELECT * LIMIT 0` schema peek picks the first
        N columns and the preview pulls only those — keeps wide tables on-screen.
        Index 0 of each row list is the column header.
        """
        query_api = hotdata.QueryApi(self.api_client)
        runtime_client = RuntimeHotdataClient(
            self._api_key,
            self.workspace_id,
            host=self.host,
        )
        results: dict[str, list[list[object]]] = {}
        try:
            db = runtime_client.resolve_managed_database(database_name)
            for table in sorted(set(tables)):
                qualified = f'"default"."public"."{table}"'
                if max_columns is not None:
                    schema_resp = query_api.query(
                        QueryRequest(sql=f"SELECT * FROM {qualified} LIMIT 0"),
                        x_database_id=db.id,
                    )
                    cols = list(schema_resp.columns)[:max_columns]
                    projection = ", ".join(
                        f'"{c.replace(chr(34), chr(34) * 2)}"' for c in cols
                    )
                else:
                    projection = "*"
                sql = f"SELECT {projection} FROM {qualified} LIMIT {limit}"
                resp = query_api.query(QueryRequest(sql=sql), x_database_id=db.id)
                results[table] = [[*resp.columns]] + [list(r) for r in (resp.rows or [])]
        finally:
            runtime_client.close()
        return results


def host_file(relpath: str) -> dagger.File:
    """Resolve a path relative to the demo root as a `dagger.File`."""
    return dag.host().file(str(DEMO_ROOT / relpath))


class DatagenImage:
    """Minimal image for the datagen flow: dlt + the Hotdata dlt destination, with `source.py` copied into `WORKDIR`. The runner runs as root since there's no agent involved."""

    @staticmethod
    def build(source_file: dagger.File) -> dagger.Container:
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
                    "dlt",
                    HOTDATA_PKG,
                    HOTDATA_DLT_DESTINATION_PKG,
                ]
            )
            .with_workdir(WORKDIR)
            .with_(lambda c: Source.copy_into(c, source_file))
        )


class ClaudeImage:
    """Image for the Claude flow: Claude Code + Node + dlt[workspace] + dlt-ai toolkits, plus the Hotdata dlt destination, a writable `source.py`, and the user's prompt mounted at `ClaudeImage.PROMPT_PATH`.

    The Claude Code CLI refuses bypassPermissions under root, so this image runs
    as an unprivileged `agent` user with its own venv. The datagen flow doesn't
    need any of this.
    """

    PROMPT_PATH = f"{WORKDIR}/prompt.md"
    VENV_PATH = f"{WORKDIR}/.venv"
    AGENT_USER = "agent"
    AGENT_UID = "1000"
    AGENT_HOME = f"/home/{AGENT_USER}"
    AGENT_OWNER = f"{AGENT_USER}:{AGENT_USER}"

    @staticmethod
    def build(
        *,
        prompt_file: dagger.File,
        source_file: dagger.File,
        anthropic_secret: dagger.Secret,
        github_secret: dagger.Secret | None,
        model: str,
        effort: str,
        output_format: str,
    ) -> tuple[dagger.Container, str]:
        """Returns `(container, claude_cmd)` — the caller threads hotdata env via `HotdataSession.inject_env`, execs `claude_cmd`, then mounts the runner on the post-Claude layer with `Runner.mount_and_exec(..., owner=ClaudeImage.AGENT_OWNER)`."""
        venv_path_env = f"{ClaudeImage.VENV_PATH}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        claude_cmd = (
            f'exec claude -p "$(cat {ClaudeImage.PROMPT_PATH})" '
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
                    f"useradd -m -u {ClaudeImage.AGENT_UID} {ClaudeImage.AGENT_USER} "
                    f"&& mkdir -p {WORKDIR} /app "
                    f"&& chown -R {ClaudeImage.AGENT_OWNER} {WORKDIR} /app {ClaudeImage.AGENT_HOME}",
                ]
            )
            .with_user(ClaudeImage.AGENT_USER)
            .with_env_variable("HOME", ClaudeImage.AGENT_HOME)
            .with_workdir(WORKDIR)
            .with_mounted_cache(
                f"{ClaudeImage.AGENT_HOME}/.cache/uv",
                dag.cache_volume("dlt-claude-uv-agent"),
                owner=ClaudeImage.AGENT_OWNER,
            )
            .with_exec(["uv", "venv", ClaudeImage.VENV_PATH])
            .with_env_variable("VIRTUAL_ENV", ClaudeImage.VENV_PATH)
            .with_env_variable("PATH", venv_path_env)
            .with_exec(
                [
                    "uv",
                    "pip",
                    "install",
                    "dlt[workspace]",
                    HOTDATA_PKG,
                    HOTDATA_DLT_DESTINATION_PKG,
                ]
            )
            .with_exec(["dlt", "ai", "init", "--agent=claude"])
            .with_exec(["dlt", "ai", "toolkit", "list"])
            .with_exec(["dlt", "ai", "toolkit", "rest-api-pipeline", "install"])
            .with_exec(["dlt", "ai", "toolkit", "data-exploration", "install"])
            .with_mounted_file(
                ClaudeImage.PROMPT_PATH, prompt_file, owner=ClaudeImage.AGENT_OWNER
            )
            .with_(
                lambda c: Source.copy_into(
                    c, source_file, owner=ClaudeImage.AGENT_OWNER
                )
            )
            .with_secret_variable("ANTHROPIC_API_KEY", anthropic_secret)
        )
        if github_secret is not None:
            base = base.with_secret_variable("GITHUB_TOKEN", github_secret)
        return base, claude_cmd
