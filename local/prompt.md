# Task

Write a dlt source for the top 10 starred repositories of GitHub user `elviskahoro` to `/workspace/source.py`. **Only edit `source.py`.** A host-supplied runner (`/app/runner.py`) handles pipeline execution and Hotdata upload after you exit — do **not** write `pipeline.py`, do **not** call `pipeline.run()`, do **not** invoke `dlt pipeline run` or the `debug-pipeline` skill's run step.

## Contract for `source.py`

The runner imports your module and reads two names from it:

1. `PIPELINE_NAME: str` — module-level string constant. Use a stable slug like `"github_starred"`. The runner uses this as both `pipeline_name` and `dataset_name` when it constructs the `dlt.pipeline(...)`.
2. `source()` — a callable returning a configured dlt source or resource. The runner does `pipe.run(source())`.

Example skeleton:

```python
import dlt
from dlt.sources.rest_api import rest_api_source

PIPELINE_NAME = "github_starred"

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

- `/workspace/source.py` defines `PIPELINE_NAME` (str) and `source()` (callable).
- `source()` returns a dlt source/resource capped at one HTTP request and ≤10 rows.
- You exit cleanly. The runner runs after you and is responsible for `pipeline.run()` + uploading to the Hotdata sandbox.
