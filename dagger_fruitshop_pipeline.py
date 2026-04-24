"""Host driver: run the Fruitshop Dagger module, upload parquet to a per-run Hotdata sandbox, verify with a query. Uses the Dagger Python SDK end to end — no subprocess."""

# mypy: disable-error-code="no-untyped-def,arg-type"

import asyncio
import json
import os
import shlex
import sys
from datetime import datetime
from pathlib import Path

import dagger
from dagger import dag

ECHO_ENV = (
    'echo "→ env: HOTDATA_WORKSPACE=${HOTDATA_WORKSPACE:-} '
    'HOTDATA_SANDBOX=${HOTDATA_SANDBOX:-}" >&2'
)


def exec_hotdata(ctr: dagger.Container, cmd: list[str]) -> dagger.Container:
    return ctr.with_exec(["sh", "-c", f"{ECHO_ENV} && exec {shlex.join(cmd)}"])

PROJECT_ROOT = Path(__file__).resolve().parent
MODULE_PATH = PROJECT_ROOT / "fruitshop_module"
OUTPUT_ROOT = PROJECT_ROOT / "_dagger_output" / "fruitshop"
FRUITSHOP_OUTPUT_DIR = "/workspace/output"


def fruitshop_load() -> dagger.Directory:
    source = dag.host().directory(str(MODULE_PATH), include=["src/**"])
    return (
        dag.container()
        .from_("ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
        .with_mounted_cache("/root/.cache/uv", dag.cache_volume("fruitshop-uv"))
        .with_env_variable("UV_LINK_MODE", "copy")
        .with_exec([
            "uv", "pip", "install", "--system",
            "dlt[filesystem,parquet]",
            "pyarrow",
        ])
        .with_mounted_directory("/app", source)
        .with_workdir("/app")
        .with_exec(["python", "src/fruitshop/load_shop.py"])
        .directory(FRUITSHOP_OUTPUT_DIR)
    )


HOTDATA_INSTALLER_URL = (
    "https://github.com/hotdata-dev/hotdata-cli/releases/latest/download/hotdata-cli-installer.sh"
)


def hotdata_base(api_key: dagger.Secret) -> dagger.Container:
    return (
        dag.container()
        .from_("debian:stable-slim")
        .with_exec(["sh", "-c", "apt-get update && apt-get install -y --no-install-recommends curl ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*"])
        .with_exec(["sh", "-c", f"curl -fsSL {HOTDATA_INSTALLER_URL} | sh"])
        .with_env_variable("PATH", "/root/.hotdata/cli:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        .with_secret_variable("HOTDATA_API_KEY", api_key)
    )


async def resolve_active_workspace(ctr: dagger.Container) -> str:
    stdout = await exec_hotdata(ctr, ["hotdata", "workspaces", "list", "--output", "json"]).stdout()
    workspaces = json.loads(stdout[stdout.index("["):])
    active = next((w for w in workspaces if w.get("active")), None) or workspaces[0]
    workspace_id = active["public_id"]
    print(f"→ using workspace {workspace_id} ({active.get('name')})", file=sys.stderr)
    return workspace_id


async def create_sandbox(ctr: dagger.Container, run_id: str) -> str:
    stdout = await exec_hotdata(ctr, [
        "hotdata", "sandbox", "new",
        "--name", f"fruitshop-{run_id}",
        "--output", "json",
    ]).stdout()
    payload = stdout[stdout.index("{"):]
    sandbox_id = json.loads(payload)["public_id"]
    print(f"→ created sandbox {sandbox_id}", file=sys.stderr)
    return sandbox_id


async def upload_parquets(ctr: dagger.Container, export_dir: Path, sandbox_id: str) -> list[str]:
    parquet_files = sorted(
        p for p in export_dir.rglob("*.parquet") if not any(part.startswith("_dlt_") for part in p.parts)
    )
    if not parquet_files:
        raise RuntimeError(f"No parquet files under {export_dir}")

    table_names: list[str] = []
    for pq in parquet_files:
        table_name = pq.parent.name
        load_id = pq.stem.rsplit(".", 1)[0]
        label = f"fruitshop_{table_name}_load_{load_id}"
        print(f"→ hotdata datasets create --label {label} --table-name {table_name}", file=sys.stderr)
        staged = (
            ctr
            .with_env_variable("HOTDATA_SANDBOX", sandbox_id)
            .with_mounted_file("/data.parquet", dag.host().file(str(pq)))
        )
        out = await exec_hotdata(staged, [
            "hotdata", "datasets", "create",
            "--label", label,
            "--table-name", table_name,
            "--file", "/data.parquet",
        ]).stdout()
        print(out, file=sys.stderr)
        table_names.append(table_name)

    print(f"→ uploaded {len(parquet_files)} file(s) to sandbox {sandbox_id}", file=sys.stderr)
    return table_names


async def verify_loaded(ctr: dagger.Container, table_names: list[str], sandbox_id: str) -> None:
    for table in sorted(set(table_names)):
        sql = f"SELECT COUNT(*) AS row_count FROM datasets.{sandbox_id}.{table}"
        print(f"→ hotdata query {sql!r}", file=sys.stderr)
        staged = ctr.with_env_variable("HOTDATA_SANDBOX", sandbox_id)
        out = await exec_hotdata(staged, ["hotdata", "query", sql]).stdout()
        print(out)


async def main() -> None:
    api_key_val = os.environ.get("HOTDATA_API_KEY")
    if not api_key_val:
        raise RuntimeError("HOTDATA_API_KEY must be set (export HOTDATA_API_KEY=...) before running.")

    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    export_dir = OUTPUT_ROOT / run_id
    print(f"→ run {run_id} → {export_dir}", file=sys.stderr)

    cfg = dagger.Config(log_output=sys.stderr)
    async with dagger.connection(cfg):
        api_key = dag.set_secret("hotdata_api_key", api_key_val)
        ctr = hotdata_base(api_key)

        workspace_id = await resolve_active_workspace(ctr)
        ctr = ctr.with_env_variable("HOTDATA_WORKSPACE", workspace_id)

        sandbox_id = await create_sandbox(ctr, run_id)

        output = fruitshop_load()
        export_dir.parent.mkdir(parents=True, exist_ok=True)
        await output.export(str(export_dir))

        tables = await upload_parquets(ctr, export_dir, sandbox_id)
        await verify_loaded(ctr, tables, sandbox_id)


if __name__ == "__main__":
    asyncio.run(main())
