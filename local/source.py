"""dlt source for synthetic purchases and customers. Agents may modify this file to change the generated data."""

import random
from datetime import datetime, timedelta

import dlt


@dlt.source
def datagen_source():
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

    return [purchases, customers]
