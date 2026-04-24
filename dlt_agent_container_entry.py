"""Runs inside the Dagger container: dlt → in-memory DuckDB → arrow → parquet bytes → Hotdata API uploads + dataset creates. Never writes parquet to disk. Prints one JSON line to stdout with the created table names; all other output goes to stderr."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import dlt
import duckdb
import pyarrow.parquet as pq

import hotdata
from hotdata.api_client import ApiClient
from hotdata.models.create_dataset_request import CreateDatasetRequest
from hotdata.models.dataset_source import DatasetSource
from hotdata.models.upload_dataset_source import UploadDatasetSource


def _load_datagen():
    """Load dlt_datagen/load.py directly by path — dlt_datagen/__init__.py imports the Dagger SDK, which isn't installed (or needed) inside the container."""
    load_path = Path("/app/src/dlt_datagen/load.py")
    spec = importlib.util.spec_from_file_location("dlt_datagen_load", load_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dlt_datagen_load"] = mod
    spec.loader.exec_module(mod)
    return mod


_datagen = _load_datagen()
purchases = _datagen.purchases
customers = _datagen.customers


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


def _arrow_to_parquet_bytes(arrow_tbl) -> bytes:
    buf = io.BytesIO()
    pq.write_table(arrow_tbl, buf)
    return buf.getvalue()


def main() -> None:
    api_key = os.environ["HOTDATA_API_KEY"]
    host = os.environ["HOTDATA_API_URL"]
    workspace_id = os.environ["HOTDATA_WORKSPACE_ID"]
    sandbox_id = os.environ["HOTDATA_SANDBOX_ID"]
    run_id = os.environ["DLT_DATAGEN_RUN_ID"]

    db = duckdb.connect(":memory:")
    dataset_name = f"agent_{run_id}"
    pipe = dlt.pipeline(
        pipeline_name=dataset_name,
        destination=dlt.destinations.duckdb(db),
        dataset_name=dataset_name,
    )
    info = pipe.run([purchases(), customers()])
    print(f"dlt load info: {info}", file=sys.stderr)

    cfg = hotdata.Configuration(access_token=api_key, host=host)
    created: list[str] = []
    with hotdata.ApiClient(cfg) as api_client:
        api_client.set_default_header("X-Workspace-Id", workspace_id)
        api_client.set_default_header("X-Sandbox-Id", sandbox_id)
        api_client.set_default_header("X-Session-Id", sandbox_id)
        datasets_api = hotdata.DatasetsApi(api_client)

        ds = pipe.dataset()
        for table_name in ("purchases", "customers"):
            arrow_tbl = getattr(ds, table_name).arrow()
            payload = _arrow_to_parquet_bytes(arrow_tbl)
            print(
                f"→ upload {table_name} rows={arrow_tbl.num_rows} bytes={len(payload)}",
                file=sys.stderr,
            )
            upload_id = _raw_upload(api_client, payload)
            print(f"  upload_id={upload_id}", file=sys.stderr)

            label = f"agent_{run_id}_{table_name}"
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
            created.append(table_name)

    print(json.dumps({"tables": created}))


if __name__ == "__main__":
    main()
