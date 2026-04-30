"""Pipeline runtime — split into two halves on purpose.

Why two files? Because the runtime runs in two processes, on opposite sides
of a trust boundary:

  ┌──────────────────────┐         ┌──────────────────────────┐
  │  host.py             │  exec   │  runner.py               │
  │  (your laptop)       │ ──────▶ │  (inside Dagger)         │
  │                      │         │                          │
  │  • builds images     │         │  • imports source.py     │
  │  • opens Hotdata     │         │  • runs the dlt pipeline │
  │    sandbox           │         │  • uploads tables to     │
  │  • execs runner.py   │         │    the Hotdata sandbox   │
  │  • parses its JSON   │         │  • prints one JSON line  │
  └──────────────────────┘         └──────────────────────────┘

They cannot be merged: different processes, different dep sets (Dagger SDK
vs dlt + duckdb + Hotdata SDK), and — most importantly — `runner.py` MUST
not be visible to the agent.

The trust boundary
──────────────────
In the Claude flow, the agent is given a writable `source.py` to edit. If
it could also read `runner.py`, it could exfiltrate `HOTDATA_API_KEY`,
upload arbitrary data, or replace the runner outright. So the host mounts
`runner.py` at `/app/runner.py` only on the *post-Claude* container layer.
By the time the runner exists in the filesystem, Claude has already exited.

The contract
────────────
Host → Runner (env vars, set by `HotdataSession.inject_env`):
    HOTDATA_API_URL, HOTDATA_WORKSPACE_ID, HOTDATA_SANDBOX_ID,
    HOTDATA_API_KEY (secret), DLT_RUN_ID
  + `/workspace/source.py` — writable, may have been edited by Claude.

Runner → Host: a single JSON line on stdout
    {"pipeline_name", "run_id", "tables", "datasets"}
  Everything else (progress, dlt logs) goes to stderr.
"""

from runtime.host import (
    ClaudeImage,
    DatagenImage,
    HotdataSession,
    Runner,
    Source,
    host_file,
)

__all__ = [
    "ClaudeImage",
    "DatagenImage",
    "HotdataSession",
    "Runner",
    "Source",
    "host_file",
]
