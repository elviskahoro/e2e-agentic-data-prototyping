# Web Endpoints Reference

Complete reference for exposing HTTP endpoints on Modal.

## Endpoint Types

| Decorator | Use Case | Framework |
|-----------|----------|-----------|
| `@modal.fastapi_endpoint()` | Simple REST endpoints | FastAPI (built-in) |
| `@modal.asgi_app()` | Full ASGI applications | FastAPI, Starlette |
| `@modal.wsgi_app()` | WSGI applications | Flask, Django |
| `@modal.web_server()` | Raw HTTP server | Any (binds to port) |

## Simple FastAPI Endpoints

```python
# GET with query parameter
@app.function()
@modal.fastapi_endpoint(docs=True)
def greet(name: str = "world") -> str:
    return f"Hello {name}!"
# → GET /greet?name=Alice

# POST with request body
@app.function()
@modal.fastapi_endpoint(method="POST", docs=True)
def process(data: dict) -> dict:
    return {"result": data["input"].upper()}
# → POST /process with JSON body

# Stateful endpoint on a class
@app.cls(gpu="A10G")
class ModelAPI:
    @modal.enter()
    def load(self):
        self.model = load_model()

    @modal.fastapi_endpoint(docs=True)
    def predict(self, text: str) -> dict:
        return {"prediction": self.model(text)}
```

`docs=True` enables automatic OpenAPI/Swagger docs at `/docs`.

## Full ASGI App (FastAPI)

```python
@app.function(image=image)
@modal.asgi_app()
def web():
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    web_app = FastAPI()

    @web_app.get("/api/health")
    def health():
        return {"status": "ok"}

    @web_app.post("/api/predict")
    async def predict(request: Request):
        data = await request.json()
        result = heavy_compute.remote(data["input"])
        return {"result": result}

    web_app.mount("/", StaticFiles(directory="/assets", html=True))
    return web_app
```

## WSGI App (Flask)

```python
@app.function(image=image)
@modal.wsgi_app()
def flask_app():
    from flask import Flask
    app = Flask(__name__)

    @app.route("/")
    def index():
        return "Hello from Flask on Modal!"

    return app
```

## Streaming Responses

```python
from fastapi.responses import StreamingResponse

@app.function()
@modal.fastapi_endpoint()
def stream():
    def generate():
        for i in range(100):
            yield f"data: chunk {i}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

## Server-Sent Events (SSE) for LLM Streaming

```python
@app.function(gpu="H100")
@modal.fastapi_endpoint(method="POST")
def chat(request: dict):
    prompt = request["prompt"]

    def generate():
        for token in model.stream(prompt):
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

## Authentication

By default, all Modal web endpoints are **publicly accessible** — anyone on the internet can call them with no auth. There are two ways to add auth.

### Option 1: Proxy Auth Tokens (Recommended)

Modal's built-in infrastructure-level auth. Requests are rejected **before** your container spins up, so no wasted compute on unauthorized calls.

```python
# Add requires_proxy_auth=True to any endpoint decorator
@app.function()
@modal.fastapi_endpoint(requires_proxy_auth=True, docs=True)
def protected_endpoint():
    return {"secret": "data"}

# Also works with asgi_app, wsgi_app, web_server:
@app.function()
@modal.asgi_app(requires_proxy_auth=True)
def web():
    ...
```

**Setup:**
1. Deploy with `requires_proxy_auth=True` — the URL will show a "🔑" emoji in deploy output
2. Create a **Proxy Auth Token** in Modal workspace settings (dashboard → Settings → Proxy Auth Tokens)
3. Everyone in the workspace can manage tokens

**Making authenticated requests:**
```python
import httpx

url = "https://workspace--app-name-function-name.modal.run"
headers = {
    "Modal-Key": "ak-...",      # Token ID
    "Modal-Secret": "as-...",   # Token Secret
}
response = httpx.get(url, headers=headers)
```

```bash
# curl example
curl -H "Modal-Key: ak-..." -H "Modal-Secret: as-..." \
  https://workspace--app-name-function-name.modal.run
```

Without the headers → **401 Unauthorized** (container never starts).

### Option 2: Custom Auth (FastAPI-Level)

Handle auth yourself using FastAPI patterns. Useful when you need custom logic (e.g., per-user API keys, OAuth, JWT).

```python
import modal
from fastapi import Header, HTTPException

# Store your auth token as a Modal Secret
@app.function(secrets=[modal.Secret.from_name("my-web-auth-token")])
@modal.fastapi_endpoint(method="POST", docs=True)
def protected(data: dict, authorization: str = Header(None)):
    import os
    expected = os.environ["AUTH_TOKEN"]
    if not authorization or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"result": "authenticated"}

# Client-side:
# headers = {"Authorization": "Bearer secret-random-token"}
# httpx.post(url, json=data, headers=headers)
```

**Tradeoff:** Your container must start to process the auth check, so unauthorized requests still incur cold-start costs.

### Which to Choose

| | Proxy Auth Tokens | Custom Auth |
|---|---|---|
| Setup | Dashboard + decorator flag | Code + Modal Secret |
| Auth happens at | Modal infrastructure (pre-container) | Inside your container |
| Unauthorized cost | Zero (container never starts) | Cold start + compute |
| Flexibility | Workspace-level tokens only | Any auth scheme (JWT, OAuth, per-user keys) |
| Best for | Internal APIs, service-to-service | Public APIs with user-level auth |

## Async Polling Pattern (Long-Running Tasks)

```python
from modal import FunctionCall

@app.function()
@modal.asgi_app()
def web():
    from fastapi import FastAPI
    web_app = FastAPI()

    @web_app.post("/submit")
    async def submit(data: dict):
        call = heavy_task.spawn(data["input"])  # Non-blocking
        return {"job_id": call.object_id}

    @web_app.get("/result/{job_id}")
    async def get_result(job_id: str):
        call = FunctionCall.from_id(job_id)
        try:
            result = call.get(timeout=0)  # Non-blocking check
            return {"status": "complete", "result": result}
        except TimeoutError:
            return {"status": "pending"}

    return web_app
```

## Port Forwarding (Development)

```python
@app.function()
def run_dev_server(timeout: int = 600):
    with modal.forward(8080) as tunnel:
        print(f"Server available at: {tunnel.url}")
        subprocess.Popen(["python", "-m", "http.server", "8080"])
        time.sleep(timeout)
```

## Deployment

```bash
# Deploy with a permanent URL
modal deploy app.py

# Dev mode with live reload
modal serve app.py
```

Deployed endpoints get URLs like: `https://<workspace>--<app-name>-<function-name>.modal.run`
