# Agentic Data Pipelining — Sandbox Demos

Prototype for a **fully agentic data pipelining experience**: an agent writes a `dlt` source, a host driver builds a container, runs the pipeline against an in-memory DuckDB, and lands the resulting tables in a per-run Hotdata sandbox. No artifacts on the host, no DuckDB file, no parquet on disk — bytes only ever live in RAM and on the wire.

The point of two subfolders is to put the same pipeline shape against two container runtimes — **local** (Dagger) and **cloud / remote** (Modal) — so we can feel the difference between local iteration and remote execution.

## Status — read this first

| Path        | Runtime  | Status                                                                                  |
| ----------- | -------- | --------------------------------------------------------------------------------------- |
| `local/`    | Dagger   | **Done.** Two flows: a hand-written datagen source, plus a Claude-driven flow that lets an agent author `source.py` inside the container before a trusted runner uploads. |
| `sandbox/`  | Modal    | **Work in progress.** Older single-flow port. Doesn't yet match the `local/` refactor — no trusted-runner / agent split, no Claude flow, no transcript persistence. |

**Start with `local/`.** Treat `sandbox/` as a reference for what the Modal port looked like before the refactor — it still runs end-to-end, but the architecture has diverged and bringing it up to parity is on the TODO list.

## If you're picking this up cold (or you're Claude Code)

Read `local/readme.md`, then `local/sandbox.py` (single entry, two flows dispatched on `--with-prompt`), then `local/pipeline_runtime.py` (host-side helpers grouped by entity: `Source`, `Runner`, `HotdataSession`, `DatagenImage`, `ClaudeImage`). The in-container side is `local/container/runner.py` and the swappable payload is `local/source.py`.

Before running anything:

- `export HOTDATA_API_KEY=...` (both flows).
- For the Claude flow only: `export ANTHROPIC_API_KEY=...`. Optionally `export GITHUB_TOKEN=...` (raises GitHub API rate limit from 60 → 5000 req/hr).
- A working Docker / OrbStack runtime — Dagger pulls the base image and runs the container locally.
- `uv` for Python package management.

Then:

```bash
cd local
uv sync

# Datagen flow — runs the hand-written source.py with synthetic purchases + customers.
uv run python sandbox.py

# Claude flow — Claude rewrites /workspace/source.py inside the container, then the
# trusted runner uploads whatever it left behind. Default prompt asks for a dlt
# source over the top 10 starred GitHub repos for `elviskahoro`.
uv run python sandbox.py --with-prompt prompt.md
```

Both flows print copy-pasteable `hotdata query` commands at the end so you can poke at the loaded tables. The Claude flow additionally persists the chat transcript to `local/data/<run_id>.jsonl` (replay it as markdown via `transcript_to_markdown.sh <path> | glow -`).

## What's actually happening (local/)

`local/sandbox.py` opens a Hotdata session, creates a fresh per-run sandbox, builds a Dagger container, and execs a trusted in-container `runner.py`. The runner imports `source.py`, runs the dlt pipeline against an **in-memory DuckDB**, converts each user-facing table to parquet bytes in a `BytesIO` buffer, and POSTs them straight to the Hotdata API. The host then queries the sandbox to print a preview.

The two flows differ only in what's layered on top before the runner runs:

- **Datagen flow** — minimal image (dlt + duckdb + pyarrow + Hotdata SDK). Runner runs as root. No agent in the loop.
- **Claude flow** — datagen image *plus* Node + Claude Code + the dlt-ai toolkits (`rest-api-pipeline`, `data-exploration`). Claude is execed first, against `prompt.md`, and may rewrite `/workspace/source.py`. **The runner is mounted only on the post-Claude container layer**, so the agent never has filesystem access to it during its exec. Runs as an unprivileged `agent` user (Claude Code refuses `bypassPermissions` under root).

The agent ↔ runner split is the security story: Claude has free rein over `/workspace/source.py`, but the upload code (which holds `HOTDATA_API_KEY`) lives at `/app/runner.py` on a layer Claude never sees.

## Contract for `source.py`

The runner reads exactly one symbol from `source.py`:

- `source()` — a `@dlt.source(name=...)`-decorated callable returning a configured dlt source. The decorator's `name` doubles as both `pipeline_name` and `dataset_name` — one symbol, no out-of-band constants. The runner does `pipe.run(source())` and discovers the user-facing tables from `pipe.default_schema.tables`.

Agents rewriting `source.py` only need to honor that contract; they don't write a `pipeline.py` and don't call `pipeline.run()`.

## Layout

```
local/
  sandbox.py             # single entry, dispatches on --with-prompt
  pipeline_runtime.py    # host-side: Source, Runner, HotdataSession, DatagenImage, ClaudeImage
  source.py              # canonical dlt source — the swappable payload
  prompt.md              # example Claude task (top-10 starred repos)
  container/runner.py    # trusted in-container runner; mounted at /app/runner.py
  transcript_to_markdown.sh
  data/                  # chat transcripts (gitignored)

sandbox/                 # Modal port — older, WIP, see Status table above
```

In-container layout (Claude flow):

- `/workspace/source.py` — writable, agent-editable.
- `/workspace/prompt.md` — the user's prompt.
- `/app/runner.py` — trusted runner, mounted *after* Claude exits.

## TODO

- Bring `sandbox/` (Modal) up to parity with the `local/` refactor — trusted-runner split, Claude flow, transcript persistence, single-entry CLI.
- Replace `prompt.md` with a higher-leverage author → review → test loop: dispatch sub-agents (Modal Sandboxes + OpenAI's Agent SDK, or equivalent) so one writes the source, another reviews it, and a third validates by running the pipeline and inspecting the loaded tables.
