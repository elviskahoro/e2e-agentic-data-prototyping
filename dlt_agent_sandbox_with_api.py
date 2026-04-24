"""Host driver: run the DltDatagen Dagger module against an in-memory DuckDB inside the container, upload arrow→parquet bytes directly to a per-run Hotdata sandbox from inside the container (no parquet on disk), verify with a query from the host."""

# mypy: disable-error-code="no-untyped-def,arg-type"

import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

import dagger
import hotdata
from dagger import dag, function, object_type
from hotdata.api_client import ApiClient
from hotdata.models.query_request import QueryRequest

PROJECT_ROOT = Path(__file__).resolve().parent
MODULE_PATH = PROJECT_ROOT / "dlt_datagen_module"
HOTDATA_SDK_PATH = PROJECT_ROOT.parent / "sdk-python"
CONTAINER_ENTRY = PROJECT_ROOT / "dlt_agent_container_entry.py"


def _pick_active_workspace(api_client: ApiClient) -> hotdata.WorkspaceListItem:
    resp = hotdata.WorkspacesApi(api_client).list_workspaces()
    workspaces = resp.workspaces or []
    if not workspaces:
        raise RuntimeError("No workspaces available for this API key.")
    return next((w for w in workspaces if w.active), workspaces[0])


def _raw_json_call(
    api_client: ApiClient,
    method: str,
    path: str,
    body: object | None = None,
) -> dict:
    """Call an endpoint the generated SDK does not expose yet, using the SDK's auth + base URL."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = api_client.param_serialize(
        method=method,
        resource_path=path,
        header_params=headers,
        body=body,
        auth_settings=["BearerAuth"],
    )
    resp = api_client.call_api(*req)
    resp.read()
    if resp.status >= 400:
        raise RuntimeError(f"{method} {path} → {resp.status}: {resp.data!r}")
    return json.loads(resp.data) if resp.data else {}


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
        datagen_src = dag.host().directory(str(MODULE_PATH), include=["src/**"])
        entry_file = dag.host().file(str(CONTAINER_ENTRY))
        # Only the files pip needs to install the editable SDK as a regular package.
        hotdata_sdk = dag.host().directory(
            str(HOTDATA_SDK_PATH),
            include=[
                "hotdata/**",
                "pyproject.toml",
                "setup.py",
                "setup.cfg",
                "README.md",
                "requirements.txt",
            ],
        )
        return (
            dag.container()
            .from_("ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
            .with_mounted_cache("/root/.cache/uv", dag.cache_volume("dlt-datagen-uv"))
            .with_env_variable("UV_LINK_MODE", "copy")
            .with_mounted_directory("/hotdata_sdk", hotdata_sdk)
            .with_exec(
                [
                    "uv",
                    "pip",
                    "install",
                    "--system",
                    "dlt[duckdb]",
                    "duckdb",
                    "pyarrow",
                    "/hotdata_sdk",
                ]
            )
            .with_mounted_directory("/app", datagen_src)
            .with_mounted_file("/app/entry.py", entry_file)
            .with_workdir("/app")
            .with_env_variable("DLT_DATAGEN_RUN_ID", self.run_id)
            .with_env_variable("HOTDATA_API_URL", api_url)
            .with_env_variable("HOTDATA_WORKSPACE_ID", self.workspace_id)
            .with_env_variable("HOTDATA_SANDBOX_ID", self.sandbox_id)
            .with_secret_variable("HOTDATA_API_KEY", api_key)
            .with_exec(["python", "/app/entry.py"])
        )


def _parse_tables_from_stdout(stdout: str) -> list[str]:
    """Container's last stdout line is a JSON object with the created table names."""
    last = stdout.strip().splitlines()[-1]
    return list(json.loads(last)["tables"])


def verify(
    api_client: ApiClient,
    sandbox_id: str,
    tables: list[str],
) -> dict[str, list[list[object]]]:
    """Preview rows from each uploaded table to confirm the load."""
    query_api = hotdata.QueryApi(api_client)
    results: dict[str, list[list[object]]] = {}
    for table in sorted(set(tables)):
        sql = f"SELECT * FROM datasets.{sandbox_id}.{table} LIMIT 10"
        print(f"→ query {sql!r}", file=sys.stderr)
        resp = query_api.query(QueryRequest(sql=sql))
        results[table] = [[*resp.columns]] + [list(r) for r in (resp.rows or [])]
    return results


def _format_preview(columns_plus_rows: list[list[object]]) -> str:
    return "\n".join(
        "\t".join("" if v is None else str(v) for v in r) for r in columns_plus_rows
    )


async def main() -> None:
    api_key_val = os.environ.get("HOTDATA_API_KEY")
    if not api_key_val:
        raise RuntimeError(
            "HOTDATA_API_KEY must be set (export HOTDATA_API_KEY=...) before running."
        )

    run_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(10)}"
    print(f"→ run {run_id}", file=sys.stderr)

    # The SDK's default host is app.hotdata.dev, but several endpoints (e.g.
    # /v1/files) are only routed on api.hotdata.dev — matching the CLI default.
    host = os.environ.get("HOTDATA_API_URL", "https://api.hotdata.dev")

    # First pass: pick workspace with an un-scoped client.
    bootstrap_cfg = hotdata.Configuration(access_token=api_key_val, host=host)
    with hotdata.ApiClient(bootstrap_cfg) as boot_client:
        ws = _pick_active_workspace(boot_client)
    print(f"→ using workspace {ws.public_id} ({ws.name})", file=sys.stderr)

    # Second pass: workspace-scoped client, creates a sandbox and pins X-Sandbox-Id
    # for the remainder of the run. The generated SDK does not wire X-Workspace-Id
    # into per-operation auth_settings, so we attach it as a default header.
    cfg = hotdata.Configuration(access_token=api_key_val, host=host)
    with hotdata.ApiClient(cfg) as api_client:
        api_client.set_default_header("X-Workspace-Id", ws.public_id)
        sandbox_body = _raw_json_call(
            api_client,
            "POST",
            "/v1/sandboxes",
            {"name": f"agent_{run_id}"},
        )
        sandbox = sandbox_body.get("sandbox", sandbox_body)
        sandbox_id = sandbox["public_id"]
        print(f"→ created sandbox {sandbox_id}", file=sys.stderr)
        # Both headers are needed: X-Sandbox-Id scopes dataset writes to the
        # sandbox, X-Session-Id scopes query reads to the same session.
        api_client.set_default_header("X-Sandbox-Id", sandbox_id)
        api_client.set_default_header("X-Session-Id", sandbox_id)

        cfg_dagger = dagger.Config(log_output=sys.stderr)
        async with dagger.connection(cfg_dagger):
            api_key_secret = dag.set_secret("hotdata-api-key", api_key_val)
            pipeline = Pipeline(
                run_id=run_id, workspace_id=ws.public_id, sandbox_id=sandbox_id
            )
            stdout = await pipeline.run_in_container(api_key_secret, host).stdout()

        tables = _parse_tables_from_stdout(stdout)
        print(f"→ container uploaded tables: {tables}", file=sys.stderr)
        verify_results = verify(api_client, sandbox_id, tables)

    print("\n=== preview ===", flush=True)
    for table, rows in verify_results.items():
        print(f"[{table}]\n{_format_preview(rows)}\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
