"""Container half of the pipeline runtime — runs INSIDE Dagger.

See `runtime/__init__.py` for why this is split from `runtime/host.py`
(short version: trust boundary — the agent gets a writable `source.py`
but must never see this file). The host mounts this script at `/app/runner.py`
on the *post-agent* container layer; by the time it exists, Claude has
already exited.

Contract with /workspace/source.py:
- Must export `source()` — a `@dlt.source(name=...)`-decorated callable. The
  decorator's `name` doubles as both pipeline_name and dataset_name; the
  resource(s) inside should already be configured with any per-page /
  add_limit / pagination knobs.

Read main() top-to-bottom:
  1. RunnerConfig.from_env()      — what the host handed us
  2. _load_user_source()          — import + validate /workspace/source.py
  3. _run_pipeline()              — dlt → Hotdata destination
  5. _emit_result()               — one JSON line to stdout (host parses this)

Everything chatty goes to stderr; only the final JSON goes to stdout.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import dlt
from hotdata_dlt_destination import hotdata_destination

WORKSPACE = "/workspace"


# ─── 1. inputs from the host ────────────────────────────────────────────────


@dataclass(frozen=True)
class RunnerConfig:
    """Everything the host passes in via env — Hotdata creds + pipeline metadata."""

    api_key: str
    host: str
    workspace: str
    database_name: str
    run_id: str

    @classmethod
    def from_env(cls) -> "RunnerConfig":
        return cls(
            api_key=os.environ["HOTDATA_API_KEY"],
            host=os.environ["HOTDATA_API_URL"],
            workspace=os.environ["HOTDATA_WORKSPACE"],
            database_name=os.environ["HOTDATA_DATABASE"],
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


# ─── 3. dlt run against the Hotdata destination ───────────────────────────


def _run_pipeline(src: Any, cfg: RunnerConfig) -> list[str]:
    """Run dlt into Hotdata; return the user-facing tables."""
    pipeline_name = src.name
    print(
        f"→ pipeline_name={pipeline_name} run_id={cfg.run_id} "
        f"database={cfg.database_name}",
        file=sys.stderr,
    )
    pipe = dlt.pipeline(
        pipeline_name=pipeline_name,
        destination=hotdata_destination(
            database_name=cfg.database_name,
            declared_tables=list(src.selected_resources.keys()),
            api_key=cfg.api_key,
            workspace_id=cfg.workspace,
            api_base_url=cfg.host,
        ),
        dataset_name=pipeline_name,
    )
    info = pipe.run(src)
    print(f"→ dlt load info: {info}", file=sys.stderr)

    user_tables = sorted(
        name
        for name in pipe.default_schema.tables.keys()
        if not name.startswith("_dlt_")
    )
    if not user_tables:
        raise RuntimeError(
            f"pipeline '{pipeline_name}' produced no user-facing tables (only _dlt_*)"
        )
    print(f"→ user tables: {user_tables}", file=sys.stderr)
    return user_tables


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
    tables = _run_pipeline(src, cfg)
    _emit_result(pipeline_name=src.name, run_id=cfg.run_id, tables=tables, datasets=[])


if __name__ == "__main__":
    main()
