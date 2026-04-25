"""Host driver: same demo as dlt_agent_sandbox_with_api.py, but the container
runs on Modal Sandboxes and the Hotdata SDK is installed from GitHub."""

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import modal
import hotdata
from hotdata.api_client import ApiClient
from hotdata.models.query_request import QueryRequest

SANDBOX_DIR = Path(__file__).resolve().parent
APP_NAME = "dlt-datagen-demo"
SANDBOX_TIMEOUT_SECONDS = 600
HOTDATA_SDK_REF = "d3806b6a5d49"


class HotdataSession:
    """Workspace-scoped Hotdata API client with helpers for endpoints the generated SDK doesn't expose, plus sandbox + verify operations.

    Used as a context manager: opens a bootstrap client to pick the active workspace, then a persistent workspace-scoped client. After `create_sandbox`, all subsequent calls are scoped to that sandbox via `X-Sandbox-Id` / `X-Session-Id`.
    """

    def __init__(self, api_key: str, host: str):
        self._api_key = api_key
        self._host = host
        self._client_cm: Any = None
        self.api_client: ApiClient
        self.workspace_id: str = ""
        self.sandbox_id: str = ""

    def __enter__(self) -> "HotdataSession":
        cfg = hotdata.Configuration(access_token=self._api_key, host=self._host)
        with hotdata.ApiClient(cfg) as boot:
            workspaces = hotdata.WorkspacesApi(boot).list_workspaces().workspaces or []
            if not workspaces:
                raise RuntimeError("No workspaces available for this API key.")
            ws = next((w for w in workspaces if w.active), workspaces[0])

        self.workspace_id = ws.public_id
        print(f"→ using workspace {ws.public_id} ({ws.name})", file=sys.stderr)

        # The generated SDK does not wire X-Workspace-Id into per-operation
        # auth_settings, so we attach it as a default header on a long-lived client.
        self._client_cm = hotdata.ApiClient(
            hotdata.Configuration(access_token=self._api_key, host=self._host)
        )
        self.api_client = self._client_cm.__enter__()
        self.api_client.set_default_header("X-Workspace-Id", ws.public_id)
        return self

    def __exit__(self, *exc) -> None:
        if self._client_cm is not None:
            self._client_cm.__exit__(*exc)

    def _raw_json(self, method: str, path: str, body: object | None = None) -> dict:
        """Call an endpoint the generated SDK does not expose yet, using the SDK's auth + base URL."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = self.api_client.param_serialize(
            method=method,
            resource_path=path,
            header_params=headers,
            body=body,
            auth_settings=["BearerAuth"],
        )
        resp = self.api_client.call_api(*req)
        resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"{method} {path} → {resp.status}: {resp.data!r}")
        return json.loads(resp.data) if resp.data else {}

    def create_sandbox(self, name: str) -> str:
        body = self._raw_json("POST", "/v1/sandboxes", {"name": name})
        sandbox = body.get("sandbox", body)
        self.sandbox_id = sandbox["public_id"]
        # X-Sandbox-Id scopes dataset writes; X-Session-Id scopes query reads.
        self.api_client.set_default_header("X-Sandbox-Id", self.sandbox_id)
        self.api_client.set_default_header("X-Session-Id", self.sandbox_id)
        print(f"→ created sandbox {self.sandbox_id}", file=sys.stderr)
        return self.sandbox_id

    def preview(self, tables: list[str]) -> dict[str, list[list[object]]]:
        """Preview rows from each uploaded table to confirm the load."""
        query_api = hotdata.QueryApi(self.api_client)
        results: dict[str, list[list[object]]] = {}
        for table in sorted(set(tables)):
            sql = f"SELECT * FROM datasets.{self.sandbox_id}.{table} LIMIT 10"
            print(f"→ query {sql!r}", file=sys.stderr)
            resp = query_api.query(QueryRequest(sql=sql))
            results[table] = [[*resp.columns]] + [list(r) for r in (resp.rows or [])]
        return results


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
    .add_local_dir(
        str(SANDBOX_DIR / "dlt_datagen_module" / "src" / "dlt_datagen"),
        "/app/dlt_datagen",
        ignore=["__pycache__"],
        copy=False,
    )
    .add_local_file(
        str(SANDBOX_DIR / "dlt_datagen_module" / "src" / "dlt_datagen" / "load.py"),
        "/app/source.py",
        copy=False,
    )
    .add_local_file(
        str(SANDBOX_DIR / "dlt_agent_container_entry.py"),
        "/app/entry.py",
        copy=False,
    )
)


def parse_tables(stdout: str) -> list[str]:
    """Container's last stdout line is JSON: {"tables": [...]}."""
    return list(json.loads(stdout.strip().splitlines()[-1])["tables"])


def run_in_modal_sandbox(
    *,
    api_key: str,
    api_url: str,
    workspace_id: str,
    hotdata_sandbox_id: str,
    run_id: str,
) -> str:
    """Spawn one Modal Sandbox, run the entry script, return stdout. Raises on non-zero exit."""
    print("→ building/spinning up Modal sandbox...", file=sys.stderr)
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    secret = modal.Secret.from_dict(
        {
            "HOTDATA_API_KEY": api_key,
            "HOTDATA_API_URL": api_url,
            "HOTDATA_WORKSPACE_ID": workspace_id,
            "HOTDATA_SANDBOX_ID": hotdata_sandbox_id,
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
        # Mirror container stderr to host stderr so the user sees the dlt
        # progress logs (Dagger streams them by default via log_output=sys.stderr).
        if stderr:
            sys.stderr.write(stderr)
        if modal_sb.returncode != 0:
            raise RuntimeError(
                f"Modal sandbox exited with code {modal_sb.returncode}. "
                f"Last stdout line: {stdout.strip().splitlines()[-1] if stdout.strip() else '(empty)'}"
            )
        return stdout
    finally:
        # Belt-and-braces: wait() should have ended the container, but if we
        # raised before wait() (KeyboardInterrupt) we still want to free the
        # remote slot. terminate() is a no-op on an already-finished sandbox.
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

    with HotdataSession(api_key, host) as session:
        hotdata_sandbox_id = session.create_sandbox(f"agent_{run_id}")
        stdout = run_in_modal_sandbox(
            api_key=api_key,
            api_url=host,
            workspace_id=session.workspace_id,
            hotdata_sandbox_id=hotdata_sandbox_id,
            run_id=run_id,
        )
        tables = parse_tables(stdout)
        print(f"→ container uploaded tables: {tables}", file=sys.stderr)
        previews = session.preview(tables)

    print("\n=== preview ===", flush=True)
    for table, rows in previews.items():
        print(f"[{table}]", flush=True)
        for row in rows:
            print("\t".join("" if v is None else str(v) for v in row), flush=True)
        print(flush=True)


if __name__ == "__main__":
    main()
