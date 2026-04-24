"""Fruitshop Dagger module: exposes `load` which runs the dlt pipeline in a container and returns the parquet output Directory."""

import dagger
from dagger import dag, function, object_type

OUTPUT_DIR = "/workspace/output"


@object_type
class Fruitshop:
    @function
    def load(self) -> dagger.Directory:
        """Run the fruitshop dlt pipeline in a container and return the parquet output directory."""
        return (
            dag.container()
            .from_("ghcr.io/astral-sh/uv:python3.13-bookworm-slim")
            .with_mounted_cache("/root/.cache/uv", dag.cache_volume("fruitshop-uv"))
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
            .with_mounted_directory("/app", dag.current_module().source())
            .with_workdir("/app")
            .with_exec(["python", "src/fruitshop/load_shop.py"])
            .directory(OUTPUT_DIR)
        )
