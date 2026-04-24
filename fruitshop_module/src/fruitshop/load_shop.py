"""dlt load for fruitshop purchases. Runs inside the Dagger-spawned container, writes parquet to /workspace/output."""

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


def load_shop() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    p = dlt.pipeline(
        pipeline_name="fruitshop_parquet",
        destination=dlt.destinations.filesystem(bucket_url=OUTPUT_DIR.as_uri()),
        dataset_name="fruitshop_data",
    )
    p.run(purchases(), loader_file_format="parquet")


if __name__ == "__main__":
    load_shop()
