# local-runtime demo

Single entry, two flows.

## Datagen flow (default)

```sh
uv run python sandbox.py
```

Builds a minimal Dagger container, copies `source.py` in, and runs the trusted
`container/runner.py` against a fresh per-run Hotdata sandbox. Previews the
uploaded tables on the host.

## Claude flow

```sh
uv run python sandbox.py --with-prompt prompt.md
```

Layers Claude Code + Node + dlt-ai toolkits on top, execs Claude with the prompt
so it can modify `source.py`, then mounts the runner on the post-Claude layer
(invisible during Claude's exec) and uploads whatever Claude left behind. Persists
the chat transcript to `data/<run_id>.{ext}` and prints copy-pasteable `hotdata`
CLI commands for the resulting sandbox.

## Required env

- `HOTDATA_API_KEY` — both flows.
- `ANTHROPIC_API_KEY` — Claude flow only.
- `GITHUB_TOKEN` — optional, Claude flow only (raises GitHub API rate limit).
- `HOTDATA_API_URL` — optional override (defaults to the SDK's `api.hotdata.dev`).

## Layout

- `sandbox.py` — single entry, dispatches on `--with-prompt`.
- `pipeline_runtime.py` — host-side helpers: `HotdataSession`, env-threading, container builders, runner mount.
- `source.py` — canonical dlt source. Contract: `PIPELINE_NAME: str` + `source()` callable. Claude may rewrite this file.
- `container/runner.py` — trusted in-container runner: imports `/workspace/source.py`, runs the dlt pipeline against an in-memory DuckDB, uploads each user-facing table to the sandbox.
