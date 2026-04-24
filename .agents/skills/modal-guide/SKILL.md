---
name: modal-guide
description: Use when building, deploying, or debugging applications on Modal (modal.com) — serverless cloud compute for Python. Covers container images, GPU functions, web endpoints, volumes, secrets, scheduling, distributed training, calling deployed functions, and scaling patterns.
license: MIT
compatibility: Requires modal Python package (pip install modal). Python 3.10-3.14. Modal account with token configured (modal token set).
metadata:
  author: elviskahoro
  version: "1.1"
  tags: [modal, serverless, gpu, cloud, ml, inference, training, deployment]
  updated: "2026-03-29"
  change: "Added Function.from_name() pattern for calling deployed Modal functions (SDK 1.4.0+)"
---

# Modal Guide

Expert guide for building and deploying applications on Modal — a serverless cloud compute platform for Python. Modal handles containers, scaling, GPUs, and infrastructure so you write Python and deploy with `modal deploy` or `modal run`.

**Core mental model:** You write decorated Python functions. Modal packages them into containers, runs them on cloud infrastructure, and manages the lifecycle. No Dockerfiles, no Kubernetes, no infrastructure config.

## When to Use

- Writing new Modal applications or functions
- Configuring container images and dependencies
- Deploying GPU-accelerated ML inference or training
- Setting up web endpoints (FastAPI, ASGI, WSGI)
- Configuring persistent storage (Volumes, cloud bucket mounts)
- Managing secrets and environment variables
- Setting up scheduled/cron jobs
- Scaling horizontally with `.map()`, `.spawn()`, or `@modal.concurrent`
- Multi-node distributed training
- Calling deployed Modal functions from external code (CLI, scripts, services)
- Debugging Modal deployments or cold start issues

## Execution Steps for Agents

### Step 1: Understand the Modal Application Structure

Every Modal app follows this pattern:

```python
import modal

app = modal.App("my-app-name")

# Define container image
image = modal.Image.debian_slim().pip_install("requests")

# Define a function
@app.function(image=image)
def my_function(x):
    return x * 2

# Local entrypoint (runs on your machine, calls remote functions)
@app.local_entrypoint()
def main():
    result = my_function.remote(21)
    print(result)  # 42
```

**Run it:** `modal run my_app.py`
**Deploy it:** `modal deploy my_app.py`

### Step 2: Choose the Right Execution Pattern

| Pattern | Code | Use Case |
|---------|------|----------|
| Remote call | `fn.remote(arg)` | Single synchronous invocation |
| Async remote | `await fn.remote.aio(arg)` | Non-blocking call |
| Parallel map | `fn.map(iterable)` | Process many inputs in parallel |
| Spawn (fire & forget) | `call = fn.spawn(arg)` | Non-blocking, get result later with `call.get()` |
| Gather | `modal.FunctionCall.gather(fn.spawn(a), fn.spawn(b))` | Parallel spawn + collect |
| Generator | `fn.remote_gen(arg)` | Stream results back |
| Local | `fn.local(arg)` | Run locally for testing |

### Step 3: Build Container Images

Chain image operations — each step is cached:

```python
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libpq-dev", "ffmpeg")
    .pip_install("torch~=2.6.0", "transformers~=4.51.0")
    .env({"HF_HOME": "/cache"})
    .add_local_file("config.yaml", "/root/config.yaml")
    .run_commands("echo 'setup complete'")
)
```

**Key image methods** — see `references/IMAGES_AND_CONTAINERS.md` for full details.

**Style rule:** Pin all dependency versions. Use `~=x.y.z` for SemVer, `==0.y.z` for pre-1.0.

### Step 4: Configure Functions

```python
@app.function(
    image=image,
    gpu="H100",                          # GPU type
    cpu=2.0,                             # CPU cores
    memory=8192,                         # MB
    timeout=600,                         # seconds
    retries=modal.Retries(max_retries=3, initial_delay=1.0),
    secrets=[modal.Secret.from_name("my-secret")],
    volumes={"/data": modal.Volume.from_name("my-vol", create_if_missing=True)},
    schedule=modal.Cron("0 9 * * *"),    # optional cron
    concurrency_limit=10,                # max concurrent containers
    allow_concurrent_inputs=5,           # requests per container
)
def my_function():
    pass
```

**GPU options:**
- Single: `"T4"`, `"L4"`, `"A10G"`, `"A100"`, `"H100"`, `"H200"`, `"B200"`
- Multi-GPU: `"H100:8"` (8 GPUs per container)
- Fallback list: `["h100", "a100", "any"]` — tries in order
- `"any"` matches L4/A10/T4

### Step 5: Use Stateful Classes for Services

```python
@app.cls(gpu="H100", image=image)
class ModelServer:
    @modal.enter()
    def load_model(self):
        """Runs once when container starts. Load model weights here."""
        self.model = load_my_model()

    @modal.method()
    def predict(self, input_data):
        """Handles each request."""
        return self.model(input_data)

    @modal.exit()
    def cleanup(self):
        """Runs when container shuts down."""
        pass
```

**Optimize cold starts:**
- `enable_memory_snapshot=True` on `@app.cls()` — snapshot after `@modal.enter()` for faster restarts
- Cache model weights in Volumes to avoid re-downloading

### Step 6: Expose Web Endpoints

```python
# Simple endpoint
@app.function()
@modal.fastapi_endpoint(docs=True)
def hello(name: str = "world") -> str:
    return f"Hello {name}!"

# Full FastAPI/ASGI app
@app.function()
@modal.asgi_app()
def web_app():
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/")
    def root():
        return {"message": "hello"}

    return app

# Streaming
from fastapi.responses import StreamingResponse

@app.function()
@modal.fastapi_endpoint()
def stream():
    def generate():
        for i in range(10):
            yield f"data: {i}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")

# Auth-protected (Modal infra-level — blocks before container starts)
@app.function()
@modal.fastapi_endpoint(requires_proxy_auth=True)
def protected():
    return "secret data"
# Client must send Modal-Key + Modal-Secret headers (create in dashboard)

# Auth-protected (custom FastAPI-level — Bearer token via Modal Secret)
@app.function(secrets=[modal.Secret.from_name("my-web-auth-token")])
@modal.fastapi_endpoint(method="POST")
def custom_auth(data: dict, authorization: str = Header(None)):
    if authorization != f"Bearer {os.environ['AUTH_TOKEN']}":
        raise HTTPException(status_code=401)
    return data
```

**All endpoints are public by default.** See `references/WEB_ENDPOINTS.md` for full auth patterns.

### Step 7: Manage Storage and Secrets

**Volumes (persistent block storage):**
```python
vol = modal.Volume.from_name("my-volume", create_if_missing=True)

@app.function(volumes={"/data": vol})
def write_data():
    Path("/data/output.txt").write_text("hello")
    vol.commit()  # Persist changes
```

**Cloud Bucket Mounts (S3/GCS):**
```python
@app.function(
    volumes={"/bucket": modal.CloudBucketMount("my-bucket", secret=s3_secret)}
)
def read_s3():
    files = list(Path("/bucket/data").glob("*.parquet"))
```

**Secrets:**
```python
secret = modal.Secret.from_name("my-secret", required_keys=["API_KEY"])

@app.function(secrets=[secret])
def use_secret():
    import os
    key = os.environ["API_KEY"]
```

### Step 8: Scale with Batching and Concurrency

**Dynamic batching** (group individual requests into batches):
```python
@app.cls(gpu="A10G")
class BatchProcessor:
    @modal.batched(max_batch_size=16, wait_ms=100)
    def process(self, items: list[str]) -> list[str]:
        # Receives list, returns list — Modal handles batching
        return [item.upper() for item in items]
```

**Concurrent inputs per container:**
```python
@app.cls(gpu="A10G", max_containers=1)
@modal.concurrent(max_inputs=100)
class Server:
    @modal.method()
    async def predict(self, x):
        return await self.model(x)
```

### Step 9: Distributed / Multi-Node Training

For multi-node GPU training, see `references/MULTINODE_TRAINING.md` for detailed patterns.

**Quick start pattern:**
```python
@app.function(gpu="H100:8", timeout=86400)
@modal.experimental.clustered(n_nodes=2, rdma=True)
def train():
    cluster_info = modal.experimental.get_cluster_info()
    from torch.distributed.run import parse_args, run

    run(parse_args([
        f"--nnodes=2",
        f"--nproc-per-node=8",
        f"--node_rank={cluster_info.rank}",
        f"--master_addr={cluster_info.container_ips[0]}",
        "--master_port=29500",
        "train_script.py",
    ]))
```

### Step 10: Call Deployed Modal Functions

**From external code** (e.g., CLI, scripts, services), reference deployed functions via `modal.Function.from_name()` — **not** the old `lookup()` API (deprecated in Modal SDK 1.4.0+):

```python
import modal

# Reference the deployed Modal app
MODAL_APP = "my-app-name"

# Get reference to a deployed function
fn = modal.Function.from_name(MODAL_APP, "my_function_name")

# Call it remotely
result = fn.remote(arg1, arg2)
print(result)
```

**Pattern in CLI tools:**
```python
import os
import modal
import typer

MODAL_APP = "my-app-name"

@app.command()
def cli_command(arg: str) -> None:
    """Call a deployed Modal function from CLI."""
    try:
        fn = modal.Function.from_name(MODAL_APP, "http_handler")
        result = fn.remote(arg)
        print(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)
```

**Key points:**
- Use `modal.Function.from_name(app_name, function_name)` to reference deployed functions
- Pass parameters directly to `.remote()` — do NOT wrap in BaseModel/Pydantic objects
- Only works on functions marked with `@app.function()` or `@app.cls()` (decorated with Modal decorators)
- The app must be deployed: `modal deploy app.py`
- Requires Modal CLI authenticated: `modal token set`

**Gotchas:**
- **Return serialization varies:** Modal may return dicts or Pydantic objects depending on whether the model class is imported in the calling process. Guard with `hasattr(r, "model_dump")`.
- **Avoid importing workflow modules in CLI:** Importing from `src.workflows_*` re-executes `@app.function` decorators, causing duplicate registration. Import models from `libs/` only; reference functions via `modal.Function.from_name()`.
- **CLI entry point:** Run via `python -m cli.main`, not directly on submodules (no `app()` call at module level).
- **Pyright and `.remote` calls:** Static type checkers often cannot infer Modal's runtime-added `.remote` attribute on decorated functions. In this repo, the lowest-friction fix is an inline Pyright suppression on the call line:
  - `result = workflow_fn.remote(...)  # pyright: ignore[reportFunctionMemberAccess]`

### Step 11: Debug and Operate

**CLI commands:**
```bash
modal run app.py              # Run locally-triggered
modal deploy app.py           # Deploy as persistent service
modal serve app.py            # Live-reload dev server
modal shell app.py            # Interactive shell in container
modal container list          # List running containers
modal container exec <id> bash  # SSH into container
modal app list                # List deployed apps
modal app stop <name>         # Stop an app
```

**Conditional imports** (for packages only in the container):
```python
with image.imports():
    import torch
    import transformers
```

**Logs:** Check Modal dashboard at modal.com or use `modal app logs <app-name>`.

## Common Mistakes to Avoid

| Mistake | Problem | Fix |
|---------|---------|-----|
| Unpinned dependencies | Builds break when packages update | Pin versions: `torch~=2.6.0` |
| Using `latest` base image tag | Non-reproducible builds | Pin to specific tag |
| `from modal import Image` | Breaks namespace clarity | Use `modal.Image` (fully qualified) |
| Loading model in `@modal.method()` | Reloads every request, slow | Load in `@modal.enter()` |
| Forgetting `vol.commit()` | Volume writes not persisted | Call `.commit()` after writes |
| No GPU fallback | Stuck waiting for specific GPU | Use `gpu=["h100", "a100", "any"]` |
| Blocking main thread in async | Kills throughput | Use `async def` + `await` consistently |
| Giant container images | Slow cold starts | Minimize apt/pip installs, use caching |

## Quick Reference

### Decorators

| Decorator | Purpose |
|-----------|---------|
| `@app.function()` | Define a Modal function |
| `@app.cls()` | Define a stateful Modal class |
| `@app.local_entrypoint()` | Local CLI entry point |
| `@modal.enter()` | Container startup hook (class) |
| `@modal.exit()` | Container shutdown hook (class) |
| `@modal.method()` | Class method callable remotely |
| `@modal.fastapi_endpoint()` | HTTP endpoint |
| `@modal.asgi_app()` | Full ASGI app |
| `@modal.wsgi_app()` | WSGI app (Flask) |
| `@modal.batched()` | Dynamic request batching |
| `@modal.concurrent()` | Concurrent input handling |
| `@modal.experimental.clustered()` | Multi-node cluster |

### Core Classes

| Class | Purpose |
|-------|---------|
| `modal.App` | Application container |
| `modal.Image` | Container image builder |
| `modal.Volume` | Persistent block storage |
| `modal.CloudBucketMount` | S3/GCS filesystem mount |
| `modal.Secret` | Secrets/credentials |
| `modal.Queue` | Distributed queue |
| `modal.Dict` | Distributed key-value store |
| `modal.Sandbox` | Isolated code execution |
| `modal.FunctionCall` | Handle from `.spawn()` |
| `modal.Cron` / `modal.Period` | Scheduling |
| `modal.Retries` | Retry configuration |

## References

Detailed reference material is split into focused documents:

- `references/IMAGES_AND_CONTAINERS.md` — Container image building, base images, dependency management, CUDA setup
- `references/GPU_AND_ML.md` — GPU configuration, model serving patterns, inference optimization, LLM serving with vLLM
- `references/WEB_ENDPOINTS.md` — FastAPI endpoints, ASGI/WSGI apps, streaming, authentication, polling patterns
- `references/MULTINODE_TRAINING.md` — Distributed training with torchrun, Accelerate, Lightning, Megatron, Ray
- `references/STORAGE_AND_DATA.md` — Volumes, cloud bucket mounts, datasets, caching strategies
- `references/SCALING_PATTERNS.md` — Map/spawn/gather, batching, concurrency, job queues, sandboxes
- `references/ECOSYSTEM.md` — Community projects, integration patterns, real-world use cases
