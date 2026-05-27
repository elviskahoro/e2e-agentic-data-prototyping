"""Ibis examples against the freshly loaded Hotdata managed database.

After the dlt runner uploads tables to the sandbox, we connect with the
`hotdata-ibis` backend and run a few Python-expression queries — no raw SQL.
Outputs are pandas DataFrames, printed alongside the existing SQL preview so
you can compare the two query surfaces side-by-side.

See https://github.com/hotdata-dev/hotdata-ibis for the backend.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime import HotdataSession


def run_examples(
    *,
    session: HotdataSession,
    database_name: str,
    tables: list[str],
    row_limit: int = 5,
) -> None:
    """Run a handful of ibis queries against the loaded tables and print results.

    Failures here are non-fatal — the SQL preview already proved the upload
    landed; ibis is a bonus surface for the demo.
    """
    try:
        import ibis  # noqa: F401
    except ImportError:
        print("→ ibis not installed; skipping ibis examples", file=sys.stderr)
        return

    try:
        con = session.ibis_connect(database_name)
    except Exception as exc:  # pragma: no cover - demo, surface and continue
        print(f"→ ibis connect failed: {exc!r}", file=sys.stderr)
        return

    sys.stdout.write("\n=== Ibis examples (Python expressions, pandas results) ===\n")
    for table_name in sorted(set(tables)):
        sys.stdout.write(f"\n[{table_name}]\n")

        try:
            t = con.table(table_name, database=("default", "public"))
        except Exception as exc:
            sys.stdout.write(f"  (skipped: {exc!r})\n")
            continue

        # 1. Head — pure ibis expression, executed to pandas.
        sys.stdout.write(f"# t.limit({row_limit}).execute()\n")
        try:
            sys.stdout.write(_format_df(t.limit(row_limit).execute()) + "\n")
        except Exception as exc:
            sys.stdout.write(f"  head failed: {exc!r}\n")

        # 2. Row count via aggregation.
        sys.stdout.write("# t.count().execute()\n")
        try:
            sys.stdout.write(f"  rows = {int(t.count().execute())}\n")
        except Exception as exc:
            sys.stdout.write(f"  count failed: {exc!r}\n")

        # 3. Per-column null-count summary — works on any schema.
        sys.stdout.write("# t.aggregate([t[c].isnull().sum().name(c) ...])\n")
        try:
            null_counts = t.aggregate(
                [t[c].isnull().sum().name(c) for c in t.columns]
            ).execute()
            sys.stdout.write(_format_df(null_counts) + "\n")
        except Exception as exc:
            sys.stdout.write(f"  null-counts failed: {exc!r}\n")

    sys.stdout.flush()


def _format_df(df) -> str:
    """Render a small DataFrame without the leading row index."""
    try:
        return df.to_string(index=False)
    except Exception:
        return repr(df)
