"""Probe whether dlt can land data in an in-memory DuckDB and be read back
without ever touching parquet on disk.

Run:  uv run python test_dlt_in_memory.py
"""

from __future__ import annotations

import duckdb
import dlt


@dlt.resource(name="events")
def events_resource():
    yield [
        {"id": 1, "kind": "click", "value": 10},
        {"id": 2, "kind": "view", "value": 20},
        {"id": 3, "kind": "click", "value": 30},
    ]


def main() -> None:
    db = duckdb.connect(":memory:")

    pipe = dlt.pipeline(
        pipeline_name="in_mem_probe",
        destination=dlt.destinations.duckdb(db),
        dataset_name="probe",
    )
    info = pipe.run(events_resource())
    print("load info:", info)

    print("\n--- Option A: query the shared DuckDB handle directly ---")
    rows = db.execute("select * from probe.events order by id").arrow()
    print(rows)

    print("\n--- Option B: pipeline.dataset() API ---")
    try:
        ds = pipe.dataset()
        events = ds.events  # attribute-style table access

        arrow_tbl = events.arrow()
        print("arrow():", type(arrow_tbl).__name__, "rows=", arrow_tbl.num_rows)

        df = events.df()
        print("df():", type(df).__name__, "shape=", df.shape)

        print("iter_arrow(chunk_size=2):")
        for i, chunk in enumerate(events.iter_arrow(chunk_size=2)):
            print(f"  chunk {i}: rows={chunk.num_rows}")
    except Exception as e:
        print(f"dataset() API failed: {type(e).__name__}: {e}")
        print("Falling back to direct DuckDB only — Option A above is the path.")


if __name__ == "__main__":
    main()
