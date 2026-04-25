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

The SDK is pinned to a git ref in two places. **They must match:**

1. **In the host driver** — `dlt_agent_sandbox_with_modal.py`, line ~20:
   ```python
   HOTDATA_SDK_REF = "main"
   ```

2. **In the host dependencies** — `sandbox/pyproject.toml`:
   ```toml
   "hotdata @ git+https://github.com/hotdata-dev/sdk-python@main",
   ```

**To bump the SDK:**
1. Choose a new ref: `"main"`, a short commit SHA, or a tag like `"v0.x.y"`.
2. Update **both** lines above to the same ref.
3. Run `uv sync` to fetch the new SDK from GitHub (this validates the ref).
4. Run `uv run python dlt_agent_sandbox_with_modal.py` to trigger an image rebuild.

The **host install** happens at `uv sync` time and won't automatically pick up changes to the Python constant in the driver — you must update `pyproject.toml` too.

**Why two places?** Modal caches image layers by command string. A mismatch between the host and container SDK versions can cause subtle bugs (host code uses features the container SDK doesn't have, or vice versa). Pinning both to the same ref string ensures they diverge only when you intentionally change them together.

## Architecture

- **dlt_agent_sandbox_with_modal.py** — Host driver. Creates a Hotdata sandbox, spawns a Modal Sandbox to run dlt in-memory, uploads the results, and fetches a preview.
- **dlt_agent_container_entry.py** — Container entry point (unchanged from Dagger version). Runs dlt, pipes parquet bytes to Hotdata API, prints JSON summary to stdout.
- **dlt_datagen_module/src/dlt_datagen/** — Synthetic data source. Agents can modify `load.py` to change generated data.
- **pyproject.toml** (both host and module) — Dependencies. Host installs `modal` and `hotdata` from GitHub; the module is pip-installable but runtime deps are baked into the image.

## Details

For rationale, design decisions, and troubleshooting, see `design/spec.md`.
