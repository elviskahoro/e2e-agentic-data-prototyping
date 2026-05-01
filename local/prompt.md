# Task

Write a dlt source for the top 10 starred repositories of GitHub user `elviskahoro` to `/workspace/source.py`. **Only edit `source.py`.** A host-supplied runner (`/app/runner.py`) handles pipeline execution and Hotdata upload after you exit — do **not** write `pipeline.py`, do **not** call `pipeline.run()`, do **not** invoke `dlt pipeline run` or the `debug-pipeline` skill's run step.

## Contract for `source.py`

The runner imports your module and reads one name from it:

- `source()` — a `@dlt.source(name=...)`-decorated callable. The decorator's `name` doubles as both `pipeline_name` and `dataset_name` when the runner constructs `dlt.pipeline(...)`. Use a stable slug like `"github_starred"`. The runner does `pipe.run(source())`.

Example skeleton:

```python
import dlt
from dlt.sources.rest_api import rest_api_source

@dlt.source(name="github_starred")
def source():
    return rest_api_source({...})  # configured per the rules below
```

## Endpoint

`GET https://api.github.com/users/elviskahoro/starred?per_page=10`

- **Hard cap: exactly one HTTP request, returning at most 10 rows.** Configure `per_page=10` and `.add_limit(1)` (one *page*, not one row — `add_limit` counts pages for paginated REST resources). Use `single_page` paginator or otherwise stop after the first page.
- If the env var `GITHUB_TOKEN` is set, send `Authorization: Bearer $GITHUB_TOKEN`. If unset, send no auth header.
- Do **not** add manual page loops, do **not** retry on 403/rate-limit responses — fail fast.

## How to do it

Use the dlt skills installed in this workspace to figure out the right shape (`find-source`, `create-rest-api-pipeline`). You may *read* their generated `pipeline.py` for reference, but extract only the source/resource definition into `source.py`. The runner is the single execution point.

## Done when

- `/workspace/source.py` defines `source()` decorated with `@dlt.source(name="...")`.
- `source()` returns a dlt source/resource capped at one HTTP request and ≤10 rows.
- You exit cleanly. The runner runs after you and is responsible for `pipeline.run()` + uploading to the Hotdata sandbox.
