"""Runs inside the Dagger container: dlt → in-memory DuckDB → arrow → parquet bytes → Hotdata API uploads + dataset creates. Never writes parquet to disk. Prints one JSON line to stdout with the created table names; all other output goes to stderr."""

from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass

import dlt
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

import hotdata
from hotdata.api_client import ApiClient
from hotdata.models.create_dataset_request import CreateDatasetRequest
from hotdata.models.dataset_source import DatasetSource
from hotdata.models.upload_dataset_source import UploadDatasetSource

from source import datagen_source

TABLES = ("purchases", "customers")


@dataclass
class Uploader:
    """Sends parquet bytes to the Hotdata API and registers them as datasets in the active sandbox."""

    api_client: ApiClient

    @staticmethod
    def _arrow_to_parquet_bytes(arrow_tbl: pa.Table) -> bytes:
        buf = io.BytesIO()
        pq.write_table(arrow_tbl, buf)
        return buf.getvalue()

    def _post_file(self, data: bytes) -> str:
        # The SDK's UploadsApi types the body as List[int] which is unusable for binary uploads.
        req = self.api_client.param_serialize(
            method="POST",
            resource_path="/v1/files",
            header_params={
                "Accept": "application/json",
                "Content-Type": "application/octet-stream",
            },
            body=data,
            auth_settings=["BearerAuth"],
        )
        resp = self.api_client.call_api(*req)
        resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"POST /v1/files → {resp.status}: {resp.data!r}")
        return json.loads(resp.data)["id"]

    def land(self, arrow_tbl: pa.Table, table_name: str, label: str) -> None:
        payload = self._arrow_to_parquet_bytes(arrow_tbl)
        print(
            f"→ upload {table_name} rows={arrow_tbl.num_rows} bytes={len(payload)}",
            file=sys.stderr,
        )
        upload_id = self._post_file(payload)
        print(f"  upload_id={upload_id}", file=sys.stderr)

        resp = hotdata.DatasetsApi(self.api_client).create_dataset(
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
    info = pipe.run(datagen_source())
    print(f"dlt load info: {info}", file=sys.stderr)

    cfg = hotdata.Configuration(access_token=api_key, host=host)
    with hotdata.ApiClient(cfg) as api_client:
        api_client.set_default_header("X-Workspace-Id", workspace_id)
        api_client.set_default_header("X-Sandbox-Id", sandbox_id)
        api_client.set_default_header("X-Session-Id", sandbox_id)

        uploader = Uploader(api_client)
        ds = pipe.dataset()
        for table_name in TABLES:
            uploader.land(
                arrow_tbl=getattr(ds, table_name).arrow(),
                table_name=table_name,
                label=f"agent_{run_id}_{table_name}",
            )

    print(json.dumps({"tables": list(TABLES)}))


if __name__ == "__main__":
    main()
