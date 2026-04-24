"""Host driver: run the DltDatagen Dagger module, upload parquet to a per-run Hotdata sandbox, verify with a query. Talks to the Hotdata HTTP API via the hotdata Python SDK instead of shelling out to the CLI."""

# mypy: disable-error-code="no-untyped-def,arg-type"

import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Annotated, Self

import dagger
import hotdata
from dagger import Directory, Doc, dag, function, object_type
from hotdata.api_client import ApiClient
from hotdata.models.create_dataset_request import CreateDatasetRequest
from hotdata.models.dataset_source import DatasetSource
from hotdata.models.query_request import QueryRequest
from hotdata.models.upload_dataset_source import UploadDatasetSource

PROJECT_ROOT = Path(__file__).resolve().parent
MODULE_PATH = PROJECT_ROOT / "dlt_datagen_module"
OUTPUT_ROOT = PROJECT_ROOT / "_dagger_output" / "dlt_datagen"
DLT_DATAGEN_OUTPUT_DIR = "/workspace/output"


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


def _raw_upload(api_client: ApiClient, data: bytes) -> str:
    """Upload raw bytes. The SDK's UploadsApi types the body as List[int] which is unusable for real files."""
    req = api_client.param_serialize(
        method="POST",
        resource_path="/v1/files",
        header_params={
            "Accept": "application/json",
            "Content-Type": "application/octet-stream",
        },
        body=data,
        auth_settings=["BearerAuth"],
    )
    resp = api_client.call_api(*req)
    resp.read()
    if resp.status >= 400:
        raise RuntimeError(f"POST /v1/files → {resp.status}: {resp.data!r}")
    return json.loads(resp.data)["id"]


@object_type
class Pipeline:
    run_id: str
    workspace_id: str = ""
    sandbox_id: str = ""

    @function
    def load_dlt_datagen(self) -> Directory:
        """Run the dlt datagen pipeline in a container and return the parquet output directory."""
        source = dag.host().directory(str(MODULE_PATH), include=["src/**"])
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
                    "dlt[filesystem,parquet]",
                    "pyarrow",
                ]
            )
            .with_mounted_directory("/app", source)
            .with_workdir("/app")
            .with_env_variable("DLT_DATAGEN_RUN_ID", self.run_id)
            .with_exec(["python", "src/dlt_datagen/load.py"])
            .directory(DLT_DATAGEN_OUTPUT_DIR)
        )


def upload_parquets(
    api_client: ApiClient,
    run_id: str,
    sandbox_id: str,
    export_dir: Annotated[
        Path, Doc("Host directory containing this run's exported parquet output")
    ],
) -> list[str]:
    """Upload every parquet under this run's dataset dir into the sandbox, returning the table names."""
    dataset_dir = export_dir / f"agent_{run_id}"
    parquet_files = sorted(
        p
        for p in dataset_dir.rglob("*.parquet")
        if not any(part.startswith("_dlt_") for part in p.parts)
    )
    if not parquet_files:
        raise RuntimeError(f"No parquet files under {dataset_dir}")

    datasets_api = hotdata.DatasetsApi(api_client)

    table_names: list[str] = []
    for pq in parquet_files:
        table_name = pq.parent.name
        label = f"agent_{run_id}_{table_name}"

        print(f"→ upload {pq.name} ({pq.stat().st_size} bytes)", file=sys.stderr)
        upload_id = _raw_upload(api_client, pq.read_bytes())
        print(f"  upload_id={upload_id}", file=sys.stderr)

        print(
            f"→ datasets.create label={label} table_name={table_name}",
            file=sys.stderr,
        )
        resp = datasets_api.create_dataset(
            CreateDatasetRequest(
                label=label,
                table_name=table_name,
                source=DatasetSource(
                    UploadDatasetSource(upload_id=upload_id, format="parquet")
                ),
            )
        )
        print(
            f"  dataset id={resp.id} schema={resp.schema_name} table={resp.table_name}",
            file=sys.stderr,
        )
        table_names.append(table_name)

    print(
        f"→ uploaded {len(parquet_files)} file(s) to sandbox {sandbox_id}",
        file=sys.stderr,
    )
    return table_names


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
    return "\n".join("\t".join("" if v is None else str(v) for v in r) for r in columns_plus_rows)


async def main() -> None:
    api_key_val = os.environ.get("HOTDATA_API_KEY")
    if not api_key_val:
        raise RuntimeError(
            "HOTDATA_API_KEY must be set (export HOTDATA_API_KEY=...) before running."
        )

    run_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(10)}"
    export_dir = OUTPUT_ROOT / run_id
    print(f"→ run {run_id} → {export_dir}", file=sys.stderr)

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
            pipeline = Pipeline(
                run_id=run_id, workspace_id=ws.public_id, sandbox_id=sandbox_id
            )
            export_dir.parent.mkdir(parents=True, exist_ok=True)
            await pipeline.load_dlt_datagen().export(str(export_dir))

        tables = upload_parquets(api_client, run_id, sandbox_id, export_dir)
        verify_results = verify(api_client, sandbox_id, tables)

    print("\n=== preview ===", flush=True)
    for table, rows in verify_results.items():
        print(f"[{table}]\n{_format_preview(rows)}\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
