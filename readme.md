# Agentic Data Pipelining — Sandbox Demos

Prototype for a **fully agentic data pipelining experience**: an agent writes a `dlt` source, spins up a containerized runtime, runs the pipeline against an in-memory DuckDB, and lands the resulting tables in a per-run Hotdata sandbox — all without leaving artifacts on the host. The same pipeline shape is workshopped against two container runtimes so we can feel the difference between **local** and **cloud/remote** execution.

## The four pieces

| Tool         | Role in the demo                                                                          |
| ------------ | ----------------------------------------------------------------------------------------- |
| **dlt**      | The pipeline itself. `source.py` is a swappable payload — the bit an agent would author.  |
| **Hotdata**  | The destination. Each run creates a fresh Hotdata sandbox; parquet bytes are uploaded straight to `/v1/files` and registered as datasets. The host then runs a verification query against the freshly-loaded tables. |
| **Dagger**   | **Local** container runtime. Builds and runs the pipeline image on the developer's machine. See `local/`. |
| **Modal**    | **Cloud / remote** container runtime. Same pipeline, executed on Modal Sandboxes — what the agent would use when the host machine isn't the right place to run the work. See `sandbox/`. |

The split is the whole point: Dagger and Modal are deliberately interchangeable here so we can compare the local-iteration story against the remote-execution story side by side.

## Subfolders

### `local/` — Dagger driver (local container)

Host driver `dlt_agent_sandbox_with_api.py` opens a Hotdata session, creates a sandbox, then uses Dagger to build a Python 3.13 image, install `dlt[duckdb]` + `pyarrow` + the Hotdata SDK, mount `source.py` and `dlt_agent_container_entry.py` at `/app`, and exec the entry script. The container runs `dlt` against an in-memory DuckDB, converts each table to parquet bytes in-memory, and POSTs them to the Hotdata API. The host then queries the sandbox to print a preview.

Run:
```bash
cd local && uv sync && uv run python dlt_agent_sandbox_with_api.py
```

### `sandbox/` — Modal driver (remote container)

Same demo, ported to Modal Sandboxes. The host driver `dlt_agent_sandbox_with_modal.py` builds a Modal image (registry base + pip layer + source files added via `add_local_file`), spawns a single `modal.Sandbox`, waits for it to exit, then queries the resulting Hotdata sandbox for the preview. The container entry script creates the Hotdata sandbox itself and writes its id to stdout, so the host doesn't need to thread it through.

Run:
```bash
cd sandbox && uv sync && uv run python dlt_agent_sandbox_with_modal.py
```

`sandbox/design/` holds the port spec, plan, and original prompt — useful as a worked example of "take this Dagger demo and make it run on Modal."

## What's shared between the two

- **`source.py`** — identical dlt source defining synthetic `purchases` and `customers` tables. This is the swappable payload an agent would replace.
- **`dlt_agent_container_entry.py`** — runs inside the container: dlt → in-memory DuckDB → arrow → parquet bytes → Hotdata API. Never writes parquet to disk. Prints exactly one JSON line on stdout (table names, plus sandbox id in the Modal version).
- **Hotdata SDK pinned to a GitHub ref** — installed via `git+https://github.com/hotdata-dev/sdk-python` in both runtimes.

## Prereqs

- `HOTDATA_API_KEY` exported in your shell (both demos).
- `uv` for Python package management.
- **Dagger demo** (`local/`): a working Docker / OrbStack runtime — Dagger pulls the base image and runs the container locally.
- **Modal demo** (`sandbox/`): one-time `modal token new` for CLI auth.
