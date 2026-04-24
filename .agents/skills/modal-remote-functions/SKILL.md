---
name: modal-remote-functions
description: Use when calling deployed Modal functions from a CLI or external service without needing the Modal app running locally
---

# Modal Remote Functions

## Overview

Modal functions deployed to the cloud can be invoked from external clients (CLI, web apps, services) using **HTTP endpoints** instead of `.remote()` calls. This eliminates the need for the Modal app context on the client side—the CLI becomes a simple HTTP consumer.

**Core principle:** Co-locate `@modal.fastapi_endpoint()` wrappers with workflow functions, pass Pydantic BaseModels directly to endpoints (FastAPI handles validation), and call endpoints via HTTP requests.

## When to Use

- Building a CLI that calls deployed Modal functions
- External service needs to trigger Modal workflows
- Client doesn't have Modal SDK installed or should avoid it
- Avoiding authentication/token setup in client environment
- Need stable, version-independent API for Modal functions

## The Architecture Pattern

### 1. Endpoint Takes BaseModel Directly

**File:** `src/attio/people.py`

```python
import modal
from src.app import app, image
from libs.attio.models import PersonSearchInput  # Pydantic BaseModel

# Modal workflow function (existing)
@app.function(image=image)
def attio_search_people(name: str = None, email: str = None, attio_api_key: str = "") -> list[PersonSearchResult]:
    os.environ["ATTIO_API_KEY"] = attio_api_key
    try:
        return search_people(name=name, email=email)
    finally:
        os.environ.pop("ATTIO_API_KEY", None)

# HTTP endpoint takes BaseModel, FastAPI validates JSON automatically
@app.function(image=image)
@modal.fastapi_endpoint(method="POST", docs=True)
def http_attio_search_people(query: PersonSearchInput) -> list[dict]:
    """HTTP endpoint for attio_search_people."""
    results = attio_search_people.remote(
        name=query.name,
        email=query.email,
        attio_api_key=query.attio_api_key,
    )
    return [r.model_dump() for r in results]
```

**Why this pattern:**
- FastAPI automatically validates JSON body against `PersonSearchInput`
- No manual field parsing needed
- Single source of truth: shared BaseModel in libs
- Endpoint and workflow co-located in same file

### 2. CLI Imports Model, Posts JSON

**File:** `cli/attio/people.py`

```python
import httpx
from libs.attio.models import PersonSearchInput  # Import BaseModel from libs

MODAL_ENDPOINT = "https://username--app-name.modal.run"

@app.command()
def search(name: str = None, email: str = None, attio_api_key: str = ""):
    """Search for people in Attio."""
    query = PersonSearchInput(
        name=name,
        email=email,
        attio_api_key=attio_api_key,
    )
    response = httpx.post(
        f"{MODAL_ENDPOINT}/http_attio_search_people",
        json=query.model_dump(),
    )
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2))
```

**Benefits:**
- No Modal SDK in CLI—just httpx + Pydantic models
- CLI and endpoint share exact same BaseModel structure
- FastAPI validates incoming JSON against model automatically
- Type-safe: validation happens before function receives data

## Quick Reference

| Layer | Pattern | Purpose |
|-------|---------|---------|
| Workflow | `@app.function(image=image)` | Actual Modal function, runs in cloud |
| Endpoint | `@app.function()` + `@modal.fastapi_endpoint(method="POST")` | HTTP wrapper, takes BaseModel, calls `.remote()` on workflow |
| CLI | `PersonInput(...)` + `httpx.post(json=model.model_dump())` | HTTP client, imports and instantiates BaseModel |

## Common Mistakes

### ❌ Endpoint Takes Individual Fields

**Problem:**
```python
@modal.fastapi_endpoint(method="POST")
def http_endpoint(name: str = None, email: str = None, api_key: str = ""):
    # Manual field extraction, duplicates model structure
    pass
```

**Why:** Duplicates the BaseModel definition in endpoint signature, harder to maintain.

**Fix:** Accept BaseModel directly:
```python
@modal.fastapi_endpoint(method="POST")
def http_endpoint(query: PersonSearchInput):
    # FastAPI validates and deserializes automatically
    pass
```

### ❌ CLI Passes Individual Params to GET

**Problem:**
```python
response = httpx.get(
    f"{MODAL_ENDPOINT}/http_endpoint",
    params={"name": name, "email": email, "api_key": api_key}
)
```

**Why:** Query params don't validate against BaseModel; no FastAPI validation.

**Fix:** Use POST with JSON body:
```python
query = PersonSearchInput(name=name, email=email, api_key=api_key)
response = httpx.post(
    f"{MODAL_ENDPOINT}/http_endpoint",
    json=query.model_dump(),
)
```

### ❌ Endpoint Calls Workflow Synchronously

**Problem:**
```python
@modal.fastapi_endpoint(method="POST")
def http_endpoint(query: PersonSearchInput):
    result = attio_search_people(...)  # Wrong: no .remote()
    return result.model_dump()
```

**Why:** Without `.remote()`, function runs in endpoint's context instead of Modal cloud.

**Fix:** Always use `.remote()`:
```python
result = attio_search_people.remote(
    name=query.name,
    email=query.email,
    attio_api_key=query.attio_api_key,
)
```

### ❌ `.remote()` flagged by Pyright in endpoint wrappers

**Problem:**
```python
result = attio_search_people.remote(...)
```
Static analysis may report `pyright/reportFunctionMemberAccess` even though Modal adds `.remote` at runtime.

**Fix (preferred in this repo):**
```python
result = attio_search_people.remote(...)  # pyright: ignore[reportFunctionMemberAccess]
```

**Trunk note:** For Pyright issues, this inline Pyright suppression is more reliable than `trunk-ignore(pyright/...)` comments, which can show `trunk/ignore-does-nothing` in some configurations.

### ❌ Endpoint Not Registered as @app.function()

**Problem:**
```python
@modal.fastapi_endpoint(method="POST")  # Missing @app.function()
def http_endpoint(query: PersonSearchInput):
    pass
```

**Why:** Endpoint won't be registered with Modal app or deployed.

**Fix:** Stack decorators in order:
```python
@app.function(image=image)
@modal.fastapi_endpoint(method="POST", docs=True)
def http_endpoint(query: PersonSearchInput):
    pass
```

## Deployment Checklist

- [ ] Endpoint defined in same file as workflow (co-located)
- [ ] Endpoint stacks `@app.function()` then `@modal.fastapi_endpoint()`
- [ ] Endpoint takes Pydantic BaseModel as parameter
- [ ] Endpoint calls workflow using `.remote()`
- [ ] Endpoint returns serialized result (`.model_dump()`)
- [ ] CLI imports BaseModel from libs
- [ ] CLI instantiates model, calls `httpx.post(..., json=model.model_dump())`
- [ ] Modal endpoint URL matches deployed app (format: `https://username--app-name.modal.run`)
- [ ] Deploy: `modal deploy src/app.py` (may need to touch file to force detection)
- [ ] Test: `curl -X POST https://username--app-name.modal.run/http_endpoint_name -H "Content-Type: application/json" -d '{...}'`

## Alternative: Direct SDK Calls via `Function.from_name().remote()`

When the CLI already has the Modal SDK installed and authenticated, you can skip HTTP endpoints entirely and call deployed functions directly:

```python
import modal

MODAL_APP = "my-app-name"

fn = modal.Function.from_name(MODAL_APP, "attio_search_people")
results = fn.remote(name="John", email=None, attio_api_key="sk-...")
```

**Key rule:** Pass parameters directly — do NOT wrap them in a BaseModel/Pydantic object. The `.remote()` call mirrors the function signature exactly.

```python
# ✅ Correct — direct params
fn.remote(name="John", email=None, attio_api_key="sk-...")

# ❌ Wrong — BaseModel wrapping
query = PersonSearchQuery(name="John", email=None, attio_api_key="sk-...")
fn.remote(query)  # Fails or produces unexpected results
```

## Gotchas (Lessons Learned)

### 1. Return Serialization Depends on Import Context

Modal may return dicts OR Pydantic objects depending on whether the model class is importable in the calling process. The calling process may not have the same imports as the Modal container.

**Always guard deserialization:**
```python
results = fn.remote(name="John")
for r in results:
    if hasattr(r, "model_dump"):
        data = r.model_dump()  # Pydantic object
    else:
        data = r  # Already a dict
```

### 2. Function Name Collisions from Workflow Imports

If CLI files import from `src.workflows_*` modules (e.g., `from src.parallel.findall import ...`), those imports execute the `@app.function` decorators again, registering duplicate functions. This causes deployment errors or silent failures.

**Fix:** Query/input models should live in `libs/` (shared pure code), NOT in the workflow files. CLI files should only import from `libs/` for models, and use `modal.Function.from_name()` to reference the deployed functions — never import directly from `src/` workflow modules.

```python
# ✅ Correct — CLI imports model from libs, references function by name
from libs.attio.models import PersonSearchInput
fn = modal.Function.from_name(MODAL_APP, "attio_search_people")

# ❌ Wrong — importing from workflow registers duplicate @app.function
from src.parallel.findall import parallel_findall_create
```

### 3. CLI Entry Point Must Use `python -m`

The CLI must be run via `python -m cli.main`, not by executing submodules directly. Submodules don't have a top-level `app()` call (Typer entrypoint), so running them directly does nothing or errors.

```bash
# ✅ Correct
python -m cli.main attio people search --name "John"

# ❌ Wrong — no app() call at module level
python cli/attio/people.py
```

## Why This Pattern Works

1. **Shared models:** CLI and endpoint use exact same BaseModel from libs
2. **Automatic validation:** FastAPI validates JSON against model before function runs
3. **No client-side Modal:** HTTP only, works from anywhere
4. **Type-safe end-to-end:** Model definition once, used by CLI + endpoint
5. **Stable API:** HTTP URL stable even if Modal SDK changes
6. **Co-location:** Endpoint and workflow together = easier to maintain
