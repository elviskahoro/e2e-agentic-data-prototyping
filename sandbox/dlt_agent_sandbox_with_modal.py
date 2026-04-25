"""Host driver: same demo as dlt_agent_sandbox_with_api.py, but the container
runs on Modal Sandboxes and the Hotdata SDK is installed from GitHub."""

import json
import os
import secrets
import sys
import time
from pathlib import Path

import modal
import hotdata
from hotdata.models.query_request import QueryRequest

SANDBOX_DIR = Path(__file__).resolve().parent
APP_NAME = "dlt-datagen-demo"
SANDBOX_TIMEOUT_SECONDS = 600


image = (
    modal.Image.from_registry(
        "ghcr.io/astral-sh/uv:python3.13-bookworm-slim",
        add_python=None,
    )
    .run_commands("apt-get update && apt-get install -y git")
    .run_commands(
        "git clone --depth 1 https://github.com/hotdata-dev/sdk-python.git /tmp/sdk"
    )
    .run_commands(
        "UV_LINK_MODE=copy uv pip install --system 'dlt[duckdb]' duckdb pyarrow '/tmp/sdk'"
    )
    .workdir("/app")
    .add_local_file(
        str(SANDBOX_DIR / "source.py"),
        "/app/source.py",
        copy=False,
    )
    .add_local_file(
        str(SANDBOX_DIR / "dlt_agent_container_entry.py"),
        "/app/entry.py",
        copy=False,
    )
)


def run_in_modal_sandbox(
    *,
    api_key: str,
    api_url: str,
    run_id: str,
) -> tuple[str, str]:
    """Spawn one Modal Sandbox, run the entry script (which creates sandbox + uploads). Returns (sandbox_id, stdout)."""
    print("→ building/spinning up Modal sandbox...", file=sys.stderr)
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    secret = modal.Secret.from_dict(
        {
            "HOTDATA_API_KEY": api_key,
            "HOTDATA_API_URL": api_url,
            "DLT_DATAGEN_RUN_ID": run_id,
        }
    )
    modal_sb = modal.Sandbox.create(
        "python",
        "/app/entry.py",
        image=image,
        app=app,
        secrets=[secret],
        timeout=SANDBOX_TIMEOUT_SECONDS,
    )
    try:
        modal_sb.wait()
        stdout = modal_sb.stdout.read()
        stderr = modal_sb.stderr.read()
        if stderr:
            sys.stderr.write(stderr)
        if modal_sb.returncode != 0:
            raise RuntimeError(
                f"Modal sandbox exited with code {modal_sb.returncode}. "
                f"Last stdout line: {stdout.strip().splitlines()[-1] if stdout.strip() else '(empty)'}"
            )
        return stdout, app
    finally:
        try:
            modal_sb.terminate()
        except Exception:
            pass


def main() -> None:
    api_key = os.environ.get("HOTDATA_API_KEY")
    if not api_key:
        raise RuntimeError("HOTDATA_API_KEY must be set before running.")
    run_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(10)}"
    print(f"→ run {run_id}", file=sys.stderr)
    host = os.environ.get("HOTDATA_API_URL", "https://api.hotdata.dev")

    stdout, _ = run_in_modal_sandbox(
        api_key=api_key,
        api_url=host,
        run_id=run_id,
    )

    # Parse output to extract sandbox_id and tables
    lines = stdout.strip().splitlines()
    result = json.loads(lines[-1])
    sandbox_id = result["sandbox_id"]
    tables = result["tables"]

    print(f"→ sandbox {sandbox_id} uploaded tables: {tables}", file=sys.stderr)

    # Preview from within the sandbox context
    cfg = hotdata.Configuration(access_token=api_key, host=host)
    with hotdata.ApiClient(cfg) as api_client:
        # Get active workspace
        workspaces = hotdata.WorkspacesApi(api_client).list_workspaces().workspaces or []
        ws = next((w for w in workspaces if w.active), workspaces[0])
        api_client.set_default_header("X-Workspace-Id", ws.public_id)
        api_client.set_default_header("X-Sandbox-Id", sandbox_id)
        api_client.set_default_header("X-Session-Id", sandbox_id)

        query_api = hotdata.QueryApi(api_client)
        previews: dict[str, list[list[object]]] = {}
        for table in sorted(set(tables)):
            sql = f"SELECT * FROM datasets.{sandbox_id}.{table} LIMIT 10"
            print(f"→ query {sql!r}", file=sys.stderr)
            resp = query_api.query(QueryRequest(sql=sql))
            previews[table] = [[*resp.columns]] + [list(r) for r in (resp.rows or [])]

    print("\n=== preview ===", flush=True)
    for table, rows in previews.items():
        print(f"[{table}]", flush=True)
        for row in rows:
            print("\t".join("" if v is None else str(v) for v in row), flush=True)
        print(flush=True)


if __name__ == "__main__":
    main()
