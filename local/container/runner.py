"""Trusted in-container runner shared by both host scripts (datagen + Claude).

Mounted at `/app/runner.py` after the agent step (or directly in datagen runs)
so it's invisible during Claude's exec.

Contract with /workspace/source.py:
- Must export `source()` — a `@dlt.source(name=...)`-decorated callable. The
  decorator's `name` doubles as both pipeline_name and dataset_name; the
  resource(s) inside should already be configured with any per-page /
  add_limit / pagination knobs.

Demo flow — read main() top-to-bottom:
  1. RunnerConfig.from_env()      — what the host handed us
  2. _load_user_source()          — import + validate /workspace/source.py
  3. _run_pipeline()              — dlt → in-memory DuckDB
  4. _land_tables()                — arrow → parquet → UploadsApi → DatasetsApi
  5. _emit_result()                — one JSON line to stdout (host parses this)

Everything chatty goes to stderr; only the final JSON goes to stdout.
"""

from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

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


# ─── 1. inputs from the host ────────────────────────────────────────────────


@dataclass(frozen=True)
class RunnerConfig:
    """Everything the host passes in via env — Hotdata creds + per-run sandbox."""

    api_key: str
    host: str
    workspace_id: str
    sandbox_id: str
    run_id: str

    @classmethod
    def from_env(cls) -> "RunnerConfig":
        return cls(
            api_key=os.environ["HOTDATA_API_KEY"],
            host=os.environ["HOTDATA_API_URL"],
            workspace_id=os.environ["HOTDATA_WORKSPACE_ID"],
            sandbox_id=os.environ["HOTDATA_SANDBOX_ID"],
            run_id=os.environ["DLT_RUN_ID"],
        )


# ─── 2. user code (untrusted; lives in /workspace) ─────────────────────────


def _load_user_source() -> Any:
    """Import /workspace/source.py and return the instantiated dlt source.

    The source's `.name` (set via `@dlt.source(name=...)`) doubles as
    pipeline_name and dataset_name — one symbol, no out-of-band constants.
    """
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    try:
        import source as user_source  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(f"could not import {WORKSPACE}/source.py: {e}") from e

    factory = getattr(user_source, "source", None)
    if not callable(factory):
        raise RuntimeError("source.py must define a callable `source()` factory")

    src = factory()
    if not getattr(src, "name", None):
        raise RuntimeError(
            "source() must return a @dlt.source(name=...)-decorated source — "
            "the decorator's name doubles as pipeline_name + dataset_name"
        )

    _log_secret_passthrough()
    return src


def _log_secret_passthrough() -> None:
    """Confirm GITHUB_TOKEN reached the runner intact (length + prefix only)."""
    tok = os.environ.get("GITHUB_TOKEN", "")
    print(
        f"→ GITHUB_TOKEN present={bool(tok)} "
        f"len={len(tok)} prefix={tok[:8]!r} "
        f"trailing_ws={tok != tok.strip()!r}",
        file=sys.stderr,
    )


# ─── 3. dlt run against an in-memory DuckDB ────────────────────────────────


def _run_pipeline(src: Any, *, run_id: str) -> tuple[Any, list[str]]:
    """Run dlt into an in-memory DuckDB; return the pipeline + user-facing tables."""
    pipeline_name = src.name
    print(f"→ pipeline_name={pipeline_name} run_id={run_id}", file=sys.stderr)

    db = duckdb.connect(":memory:")
    pipe = dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=dlt.destinations.duckdb(db),
        dataset_name=pipeline_name,
    )
    info = pipe.run(src)
    print(f"→ dlt load info: {info}", file=sys.stderr)

    user_tables = sorted(
        name for name in pipe.default_schema.tables.keys()
        if not name.startswith("_dlt_")
    )
    if not user_tables:
        raise RuntimeError(
            f"pipeline '{pipeline_name}' produced no user-facing tables (only _dlt_*)"
        )
    print(f"→ user tables: {user_tables}", file=sys.stderr)
    return pipe, user_tables


# ─── 4. land each table in the per-run Hotdata sandbox ─────────────────────


def _land_tables(
    cfg: RunnerConfig, pipe: Any, tables: list[str]
) -> list[dict[str, str]]:
    """For each user table: arrow → parquet → upload → create_dataset (sandbox-scoped)."""
    sdk_cfg = hotdata.Configuration(
        api_key=cfg.api_key,
        host=cfg.host,
        workspace_id=cfg.workspace_id,
        session_id=cfg.sandbox_id,
    )
    datasets: list[dict[str, str]] = []
    with hotdata.ApiClient(sdk_cfg) as api_client:
        # Residual until SDK adds `sandbox_id=` on Configuration; scopes
        # dataset writes via X-Sandbox-Id (reads use session_id above).
        api_client.set_default_header("X-Sandbox-Id", cfg.sandbox_id)

        uploader = Uploader(api_client)
        ds = pipe.dataset()
        for table_name in tables:
            datasets.append(
                uploader.land(
                    arrow_tbl=getattr(ds, table_name).arrow(),
                    table_name=table_name,
                    label=f"agent_{cfg.run_id}_{table_name}",
                )
            )
    return datasets


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


# ─── 5. result handoff back to the host ────────────────────────────────────


def _emit_result(
    *,
    pipeline_name: str,
    run_id: str,
    tables: list[str],
    datasets: list[dict[str, str]],
) -> None:
    """The single line of stdout the host parses."""
    print(
        json.dumps(
            {
                "pipeline_name": pipeline_name,
                "run_id": run_id,
                "tables": tables,
                "datasets": datasets,
            }
        )
    )


# ─── entry point ───────────────────────────────────────────────────────────


def main() -> None:
    cfg = RunnerConfig.from_env()
    src = _load_user_source()
    pipe, tables = _run_pipeline(src, run_id=cfg.run_id)
    datasets = _land_tables(cfg, pipe, tables)
    _emit_result(
        pipeline_name=src.name,
        run_id=cfg.run_id,
        tables=tables,
        datasets=datasets,
    )


if __name__ == "__main__":
    main()
