"""Host driver: run the DltDatagen Dagger module against an in-memory DuckDB inside the container, upload arrow→parquet bytes directly to a per-run Hotdata sandbox from inside the container (no parquet on disk), verify with a query from the host."""

# mypy: disable-error-code="no-untyped-def,arg-type"

import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import dagger
import hotdata
from dagger import dag, function, object_type
from hotdata.api_client import ApiClient
from hotdata.models.query_request import QueryRequest

PROJECT_ROOT = Path(__file__).resolve().parent
DATAGEN_SOURCE = PROJECT_ROOT / "source.py"
HOTDATA_SDK_GIT = "git+https://github.com/hotdata-dev/sdk-python"
CONTAINER_ENTRY = PROJECT_ROOT / "dlt_agent_container_entry.py"


class HotdataSession:
    """Workspace-scoped Hotdata API client with helpers for endpoints the generated SDK doesn't expose, plus sandbox + verify operations.

    Used as a context manager: opens a bootstrap client to pick the active workspace, then a persistent workspace-scoped client. After `create_sandbox`, all subsequent calls are scoped to that sandbox via `X-Sandbox-Id` / `X-Session-Id`.
    """

    def __init__(self, api_key: str, host: str):
        self._api_key = api_key
        self._host = host
        self._client_cm: Any = None
        self.api_client: ApiClient
        self.workspace_id: str = ""
        self.sandbox_id: str = ""

    def __enter__(self) -> "HotdataSession":
        cfg = hotdata.Configuration(access_token=self._api_key, host=self._host)
        with hotdata.ApiClient(cfg) as boot:
            workspaces = hotdata.WorkspacesApi(boot).list_workspaces().workspaces or []
            if not workspaces:
                raise RuntimeError("No workspaces available for this API key.")
            ws = next((w for w in workspaces if w.active), workspaces[0])

        self.workspace_id = ws.public_id
        print(f"→ using workspace {ws.public_id} ({ws.name})", file=sys.stderr)

        # The generated SDK does not wire X-Workspace-Id into per-operation
        # auth_settings, so we attach it as a default header on a long-lived client.
        self._client_cm = hotdata.ApiClient(
            hotdata.Configuration(access_token=self._api_key, host=self._host)
        )
        self.api_client = self._client_cm.__enter__()
        self.api_client.set_default_header("X-Workspace-Id", ws.public_id)
        return self

    def __exit__(self, *exc) -> None:
        if self._client_cm is not None:
            self._client_cm.__exit__(*exc)

    def _raw_json(self, method: str, path: str, body: object | None = None) -> dict:
        """Call an endpoint the generated SDK does not expose yet, using the SDK's auth + base URL."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = self.api_client.param_serialize(
            method=method,
            resource_path=path,
            header_params=headers,
            body=body,
            auth_settings=["BearerAuth"],
        )
        resp = self.api_client.call_api(*req)
        resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"{method} {path} → {resp.status}: {resp.data!r}")
        return json.loads(resp.data) if resp.data else {}

    def create_sandbox(self, name: str) -> str:
        body = self._raw_json("POST", "/v1/sandboxes", {"name": name})
        sandbox = body.get("sandbox", body)
        self.sandbox_id = sandbox["public_id"]
        # X-Sandbox-Id scopes dataset writes; X-Session-Id scopes query reads.
        self.api_client.set_default_header("X-Sandbox-Id", self.sandbox_id)
        self.api_client.set_default_header("X-Session-Id", self.sandbox_id)
        print(f"→ created sandbox {self.sandbox_id}", file=sys.stderr)
        return self.sandbox_id

    def preview(self, tables: list[str]) -> dict[str, list[list[object]]]:
        """Preview rows from each uploaded table to confirm the load."""
        query_api = hotdata.QueryApi(self.api_client)
        results: dict[str, list[list[object]]] = {}
        for table in sorted(set(tables)):
            sql = f"SELECT * FROM datasets.{self.sandbox_id}.{table} LIMIT 10"
            print(f"→ query {sql!r}", file=sys.stderr)
            resp = query_api.query(QueryRequest(sql=sql))
            results[table] = [[*resp.columns]] + [list(r) for r in (resp.rows or [])]
        return results


@object_type
class Pipeline:
    run_id: str
    workspace_id: str = ""
    sandbox_id: str = ""

    @function
    def run_in_container(
        self,
        api_key: dagger.Secret,
        api_url: str,
    ) -> dagger.Container:
        """Build the container that runs dlt in-memory and uploads straight to the Hotdata API."""
        # Mount source.py and entry.py side-by-side at /app so the entry's
        # `from source import datagen_source` resolves via the script-dir
        # entry on sys.path — no PYTHONPATH or sys.path manipulation needed.
        datagen_source_file = dag.host().file(str(DATAGEN_SOURCE))
        entry_file = dag.host().file(str(CONTAINER_ENTRY))
        return (
            dag.container()
            .from_("ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
            .with_mounted_cache("/root/.cache/uv", dag.cache_volume("dlt-datagen-uv"))
            .with_env_variable("UV_LINK_MODE", "copy")
            # uv needs git to install hotdata from a GitHub source.
            .with_exec(
                [
                    "sh",
                    "-c",
                    "apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*",
                ]
            )
            .with_exec(
                [
                    "uv",
                    "pip",
                    "install",
                    "--system",
                    "dlt[duckdb]",
                    "duckdb",
                    "pyarrow",
                    HOTDATA_SDK_GIT,
                ]
            )
            .with_mounted_file("/app/source.py", datagen_source_file)
            .with_mounted_file("/app/entry.py", entry_file)
            .with_workdir("/app")
            .with_env_variable("DLT_DATAGEN_RUN_ID", self.run_id)
            .with_env_variable("HOTDATA_API_URL", api_url)
            .with_env_variable("HOTDATA_WORKSPACE_ID", self.workspace_id)
            .with_env_variable("HOTDATA_SANDBOX_ID", self.sandbox_id)
            .with_secret_variable("HOTDATA_API_KEY", api_key)
            .with_exec(["python", "/app/entry.py"])
        )

    @staticmethod
    def parse_tables(stdout: str) -> list[str]:
        """Container's last stdout line is a JSON object with the created table names."""
        last = stdout.strip().splitlines()[-1]
        return list(json.loads(last)["tables"])


def _format_preview(columns_plus_rows: list[list[object]]) -> str:
    return "\n".join(
        "\t".join("" if v is None else str(v) for v in r) for r in columns_plus_rows
    )


async def main() -> None:
    api_key = os.environ.get("HOTDATA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "HOTDATA_API_KEY must be set (export HOTDATA_API_KEY=...) before running."
        )

    run_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(10)}"
    print(f"→ run {run_id}", file=sys.stderr)

    # The SDK's default host is app.hotdata.dev, but several endpoints (e.g.
    # /v1/files) are only routed on api.hotdata.dev — matching the CLI default.
    host = os.environ.get("HOTDATA_API_URL", "https://api.hotdata.dev")

    with HotdataSession(api_key, host) as session:
        sandbox_id = session.create_sandbox(f"agent_{run_id}")

        async with dagger.connection(dagger.Config(log_output=sys.stderr)):
            api_key_secret = dag.set_secret("hotdata-api-key", api_key)
            pipeline = Pipeline(
                run_id=run_id,
                workspace_id=session.workspace_id,
                sandbox_id=sandbox_id,
            )
            stdout = await pipeline.run_in_container(api_key_secret, host).stdout()

        tables = Pipeline.parse_tables(stdout)
        print(f"→ container uploaded tables: {tables}", file=sys.stderr)
        previews = session.preview(tables)

    print("\n=== preview ===", flush=True)
    for table, rows in previews.items():
        print(f"[{table}]\n{_format_preview(rows)}\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
