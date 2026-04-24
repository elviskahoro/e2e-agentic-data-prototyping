"""Host driver: run the DltDatagen Dagger module, upload parquet to a per-run Hotdata sandbox, verify with a query. Uses the Dagger Python SDK end to end — no subprocess."""

# mypy: disable-error-code="no-untyped-def,arg-type"

import asyncio
import json
import os
import secrets
import shlex
import sys
import time
from pathlib import Path
from typing import Annotated, Self

import dagger
from dagger import Container, Directory, Doc, Secret, dag, function, object_type

PROJECT_ROOT = Path(__file__).resolve().parent
MODULE_PATH = PROJECT_ROOT / "dlt_datagen_module"
OUTPUT_ROOT = PROJECT_ROOT / "_dagger_output" / "dlt_datagen"
DLT_DATAGEN_OUTPUT_DIR = "/workspace/output"

HOTDATA_INSTALLER_URL = "https://github.com/hotdata-dev/hotdata-cli/releases/latest/download/hotdata-cli-installer.sh"

ECHO_ENV = (
    'echo "→ env: HOTDATA_WORKSPACE=${HOTDATA_WORKSPACE:-} '
    'HOTDATA_SANDBOX=${HOTDATA_SANDBOX:-}" >&2'
)


def _exec(ctr: Container, cmd: list[str]) -> Container:
    return ctr.with_exec(["sh", "-c", f"{ECHO_ENV} && exec {shlex.join(cmd)}"])


@object_type
class Pipeline:
    ctr: Container
    run_id: str
    workspace_id: str = ""
    sandbox_id: str = ""

    @classmethod
    async def create(
        cls,
        api_key: Annotated[Secret, Doc("Hotdata API key secret for CLI auth")],
        run_id: Annotated[str, Doc("Unique identifier for this pipeline run")],
    ) -> Self:
        """Build the base Hotdata CLI container and resolve the active workspace."""
        ctr = (
            dag.container()
            .from_("debian:stable-slim")
            .with_exec(
                [
                    "sh",
                    "-c",
                    "apt-get update && apt-get install -y --no-install-recommends "
                    "curl ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*",
                ]
            )
            .with_exec(["sh", "-c", f"curl -fsSL {HOTDATA_INSTALLER_URL} | sh"])
            .with_env_variable(
                "PATH",
                "/root/.hotdata/cli:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            )
            .with_secret_variable("HOTDATA_API_KEY", api_key)
        )

        stdout = await _exec(
            ctr, ["hotdata", "workspaces", "list", "--output", "json"]
        ).stdout()
        workspaces = json.loads(stdout[stdout.index("[") :])
        active = next((w for w in workspaces if w.get("active")), None) or workspaces[0]
        workspace_id = active["public_id"]
        print(
            f"→ using workspace {workspace_id} ({active.get('name')})", file=sys.stderr
        )

        ctr = ctr.with_env_variable("HOTDATA_WORKSPACE", workspace_id)
        return cls(ctr=ctr, run_id=run_id, workspace_id=workspace_id)

    @function
    async def create_sandbox(self) -> Self:
        """Create a per-run Hotdata sandbox and attach it to the container env. Sandbox name matches the dlt dataset name."""
        stdout = await _exec(
            self.ctr,
            [
                "hotdata",
                "sandbox",
                "new",
                "--name",
                f"agent_{self.run_id}",
                "--output",
                "json",
            ],
        ).stdout()
        sandbox_id = json.loads(stdout[stdout.index("{") :])["public_id"]
        print(f"→ created sandbox {sandbox_id}", file=sys.stderr)

        self.sandbox_id = sandbox_id
        self.ctr = self.ctr.with_env_variable("HOTDATA_SANDBOX", sandbox_id)
        return self

    @function
    def load_dlt_datagen(self) -> Directory:
        """Run the dlt datagen pipeline in a container and return the parquet output directory."""
        source = dag.host().directory(str(MODULE_PATH), include=["src/**"])
        return (
            dag.container()
            .from_("ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
            .with_mounted_cache("/root/.cache/uv", dag.cache_volume("dlt-datagen-uv"))
            .with_env_variable("UV_LINK_MODE", "copy")
            .with_exec(
                [
                    "uv",
                    "pip",
                    "install",
                    "--system",
                    "dlt[filesystem,parquet]",
                    "pyarrow",
                ]
            )
            .with_mounted_directory("/app", source)
            .with_workdir("/app")
            .with_env_variable("DLT_DATAGEN_RUN_ID", self.run_id)
            .with_exec(["python", "src/dlt_datagen/load.py"])
            .directory(DLT_DATAGEN_OUTPUT_DIR)
        )

    @function
    async def upload_parquets(
        self,
        export_dir: Annotated[
            Path, Doc("Host directory containing this run's exported parquet output")
        ],
    ) -> list[str]:
        """Upload every parquet under this run's dataset dir into the sandbox, returning the table names."""
        dataset_dir = export_dir / f"agent_{self.run_id}"
        parquet_files = sorted(
            p
            for p in dataset_dir.rglob("*.parquet")
            if not any(part.startswith("_dlt_") for part in p.parts)
        )
        if not parquet_files:
            raise RuntimeError(f"No parquet files under {dataset_dir}")

        table_names: list[str] = []
        for pq in parquet_files:
            table_name = pq.parent.name
            label = f"agent_{self.run_id}_{table_name}"
            print(
                f"→ hotdata datasets create --label {label} --table-name {table_name}",
                file=sys.stderr,
            )
            staged = self.ctr.with_mounted_file(
                "/data.parquet", dag.host().file(str(pq))
            )
            out = await _exec(
                staged,
                [
                    "hotdata",
                    "datasets",
                    "create",
                    "--label",
                    label,
                    "--table-name",
                    table_name,
                    "--file",
                    "/data.parquet",
                ],
            ).stdout()
            print(out, file=sys.stderr)
            table_names.append(table_name)

        print(
            f"→ uploaded {len(parquet_files)} file(s) to sandbox {self.sandbox_id}",
            file=sys.stderr,
        )
        return table_names

    @function
    async def verify(
        self,
        tables: Annotated[list[str], Doc("Table names to preview with a SELECT query")],
    ) -> dict[str, str]:
        """Preview rows from each uploaded table to confirm the load."""
        results: dict[str, str] = {}
        for table in sorted(set(tables)):
            sql = f"SELECT * FROM datasets.{self.sandbox_id}.{table} LIMIT 10"
            print(f"→ hotdata query {sql!r}", file=sys.stderr)
            out = await _exec(self.ctr, ["hotdata", "query", sql]).stdout()
            results[table] = out.rstrip()
        return results


async def main() -> None:
    api_key_val = os.environ.get("HOTDATA_API_KEY")
    if not api_key_val:
        raise RuntimeError(
            "HOTDATA_API_KEY must be set (export HOTDATA_API_KEY=...) before running."
        )

    run_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(10)}"
    export_dir = OUTPUT_ROOT / run_id
    print(f"→ run {run_id} → {export_dir}", file=sys.stderr)

    cfg = dagger.Config(log_output=sys.stderr)
    async with dagger.connection(cfg):
        api_key = dag.set_secret("hotdata_api_key", api_key_val)
        pipeline = await Pipeline.create(api_key=api_key, run_id=run_id)
        pipeline = await pipeline.create_sandbox()

        export_dir.parent.mkdir(parents=True, exist_ok=True)
        await pipeline.load_dlt_datagen().export(str(export_dir))

        tables = await pipeline.upload_parquets(export_dir)
        verify_results = await pipeline.verify(tables)

    print("\n=== preview ===", flush=True)
    for table, out in verify_results.items():
        print(f"[{table}]\n{out}\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
