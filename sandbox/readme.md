# DLT Agent Modal Sandbox Demo

A self-contained demo that runs dlt data generation in a Modal Sandbox and uploads the results to Hotdata. This is the Modal port of the Dagger-based demo in `../local/`.

## Quickstart

### Prerequisites

1. **Hotdata API key** — set `HOTDATA_API_KEY` in your shell environment before running.
   ```bash
   export HOTDATA_API_KEY="your-api-key-here"
   ```

2. **Modal CLI authentication** — one-time setup:
   ```bash
   modal token new
   ```
   This creates a local token file that Modal uses for authentication.

3. **uv** — Python package manager ([uv docs](https://docs.astral.sh/uv/)).

### Run the demo

```bash
cd sandbox
uv sync
uv run python dlt_agent_sandbox_with_modal.py
```

### Expected output

First run: **slow** (2–5 minutes). Modal builds the image with pip layer (`dlt`, `duckdb`, `pyarrow`, Hotdata SDK).
- Expect a pause with message `→ building/spinning up Modal sandbox...` while dependencies install.
- You'll see dlt load logs streamed to stderr from inside the container.
- Final stdout is a preview table showing the generated `purchases` and `customers` rows.

Subsequent runs: **fast** (under 1 minute). The pip layer is cached; only container startup + dlt work.

## Updating the Hotdata SDK

The SDK is pinned in `pyproject.toml`:
```toml
"hotdata @ git+https://github.com/hotdata-dev/sdk-python@d3806b6a5d49",
```

**To bump the SDK:**
1. Choose a new ref: `"main"`, a short commit SHA, or a tag like `"v0.x.y"`.
2. Update the ref in `pyproject.toml`.
3. Run `uv sync` to fetch the new SDK from GitHub.
4. Run `uv run python dlt_agent_sandbox_with_modal.py` to trigger a Modal image rebuild.

## Architecture

- **dlt_agent_sandbox_with_modal.py** — Host driver. Creates a Hotdata sandbox, spawns a Modal Sandbox to run dlt in-memory, uploads the results, and fetches a preview.
- **dlt_agent_container_entry.py** — Container entry point. Runs dlt, pipes parquet bytes to Hotdata API, prints JSON summary to stdout.
- **source.py** — Synthetic data source defined as a dlt source. Modify this to change the generated data.
- **pyproject.toml** — Dependencies. Host installs `modal` and `hotdata` from GitHub.

## Details

For rationale, design decisions, and troubleshooting, see `design/spec.md`.
