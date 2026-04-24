"""dlt load for fruitshop purchases. Runs inside the Dagger-spawned container, writes parquet to /workspace/output."""

import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import dlt

OUTPUT_DIR = Path("/workspace/output")


@dlt.resource(primary_key="id")
def purchases():
    random.seed(42)
    start_date = datetime(2018, 10, 1)
    yield [
        {
            "id": i + 1,
            "customer_id": random.randint(1, 10),
            "inventory_id": random.randint(1, 6),
            "quantity": random.randint(1, 5),
            "date": (start_date + timedelta(days=random.randint(0, 13))).strftime(
                "%Y-%m-%d"
            ),
        }
        for i in range(10)
    ]


@dlt.resource(primary_key="id")
def customers():
    yield [
        {"id": 1, "name": "Alice", "city": "Berlin"},
        {"id": 2, "name": "Bob", "city": "Lisbon"},
        {"id": 3, "name": "Carol", "city": "Taipei"},
    ]


def load_shop() -> None:
    run_id = os.environ["FRUITSHOP_RUN_ID"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    p = dlt.pipeline(
        pipeline_name=f"agent_{run_id}",
        destination=dlt.destinations.filesystem(bucket_url=OUTPUT_DIR.as_uri()),
        dataset_name=f"agent_{run_id}",
    )
    p.run([purchases(), customers()], loader_file_format="parquet")


if __name__ == "__main__":
    load_shop()
