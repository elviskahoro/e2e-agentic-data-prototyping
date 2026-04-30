"""Trusted in-container runner shared by both host scripts (datagen + Claude).

Mounted at `/app/runner.py` after the agent step (or directly in datagen runs)
so it's invisible during Claude's exec.

Contract with /workspace/source.py:
- Must export `PIPELINE_NAME: str` (used as both pipeline_name and dataset_name).
- Must export `source()` returning a dlt source or resource (already configured
  with any per-page / add_limit / pagination knobs).

Flow:
  1. Import source from /workspace/source.py.
  2. Run a dlt pipeline against an in-memory DuckDB.
  3. Introspect user-facing tables (everything not prefixed `_dlt_`).
  4. For each table: arrow → parquet bytes → UploadsApi.upload_file → DatasetsApi.create_dataset
     with label `agent_<run_id>_<table>`. Configuration scopes writes/reads to the per-run sandbox.
  5. Print one JSON line to stdout: {"pipeline_name", "run_id", "tables", "datasets"}.
     Everything else goes to stderr.
"""

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

WORKSPACE = "/workspace"


@dataclass
class Uploader:
    api_client: ApiClient

    @staticmethod
    def _arrow_to_parquet_bytes(arrow_tbl: pa.Table) -> bytes:
        buf = io.BytesIO()
        pq.write_table(arrow_tbl, buf)
        return buf.getvalue()

    def land(
        self, arrow_tbl: pa.Table, table_name: str, label: str
    ) -> dict[str, str]:
        payload = self._arrow_to_parquet_bytes(arrow_tbl)
        print(
            f"→ upload {table_name} rows={arrow_tbl.num_rows} bytes={len(payload)}",
            file=sys.stderr,
        )
        upload = hotdata.UploadsApi(self.api_client).upload_file(
            body=payload, streaming=False
        )
        resp = hotdata.DatasetsApi(self.api_client).create_dataset(
            CreateDatasetRequest(
                label=label,
                table_name=table_name,
                source=DatasetSource(
                    UploadDatasetSource(upload_id=upload.id, format="parquet")
                ),
            )
        )
        print(
            f"  dataset id={resp.id} schema={resp.schema_name} table={resp.table_name}",
            file=sys.stderr,
        )
        return {
            "table": table_name,
            "label": label,
            "dataset_id": resp.id,
            "schema_name": resp.schema_name,
            "table_name": resp.table_name,
        }


def main() -> None:
    api_key = os.environ["HOTDATA_API_KEY"]
    host = os.environ["HOTDATA_API_URL"]
    workspace_id = os.environ["HOTDATA_WORKSPACE_ID"]
    sandbox_id = os.environ["HOTDATA_SANDBOX_ID"]
    run_id = os.environ["DLT_RUN_ID"]

    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    try:
        import source as user_source  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            f"could not import {WORKSPACE}/source.py: {e}"
        ) from e

    pipeline_name = getattr(user_source, "PIPELINE_NAME", None)
    if not isinstance(pipeline_name, str) or not pipeline_name:
        raise RuntimeError(
            "source.py must define PIPELINE_NAME: str at module scope"
        )
    if not callable(getattr(user_source, "source", None)):
        raise RuntimeError("source.py must define a callable `source()` factory")

    print(f"→ pipeline_name={pipeline_name} run_id={run_id}", file=sys.stderr)
    # Diagnostic: confirm GITHUB_TOKEN reaches the runner intact (length + prefix only).
    gh_tok = os.environ.get("GITHUB_TOKEN", "")
    print(
        f"→ GITHUB_TOKEN present={bool(gh_tok)} "
        f"len={len(gh_tok)} prefix={gh_tok[:8]!r} "
        f"trailing_ws={gh_tok != gh_tok.strip()!r}",
        file=sys.stderr,
    )

    db = duckdb.connect(":memory:")
    pipe = dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=dlt.destinations.duckdb(db),
        dataset_name=pipeline_name,
    )
    info = pipe.run(user_source.source())
    print(f"→ dlt load info: {info}", file=sys.stderr)

    schema = pipe.default_schema
    user_tables = sorted(
        name for name in schema.tables.keys() if not name.startswith("_dlt_")
    )
    if not user_tables:
        raise RuntimeError(
            f"pipeline '{pipeline_name}' produced no user-facing tables (only _dlt_*)"
        )
    print(f"→ user tables: {user_tables}", file=sys.stderr)

    cfg = hotdata.Configuration(
        api_key=api_key,
        host=host,
        workspace_id=workspace_id,
        session_id=sandbox_id,
    )
    with hotdata.ApiClient(cfg) as api_client:
        # Residual until SDK adds `sandbox_id=` kwarg on Configuration; scopes
        # dataset writes via X-Sandbox-Id (reads use session_id above).
        api_client.set_default_header("X-Sandbox-Id", sandbox_id)

        uploader = Uploader(api_client)
        ds = pipe.dataset()
        datasets: list[dict[str, str]] = []
        for table_name in user_tables:
            arrow_tbl = getattr(ds, table_name).arrow()
            datasets.append(
                uploader.land(
                    arrow_tbl=arrow_tbl,
                    table_name=table_name,
                    label=f"agent_{run_id}_{table_name}",
                )
            )

    print(
        json.dumps(
            {
                "pipeline_name": pipeline_name,
                "run_id": run_id,
                "tables": user_tables,
                "datasets": datasets,
            }
        )
    )


if __name__ == "__main__":
    main()
