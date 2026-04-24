# Scaling Patterns Reference

Complete reference for horizontal scaling, job queues, sandboxes, and coordination on Modal.

## Parallel Execution

### .map() — Parallel Processing

```python
@app.function()
def process(item):
    return heavy_computation(item)

@app.local_entrypoint()
def main():
    items = list(range(1000))
    results = list(process.map(items))  # Spins up containers in parallel
```

`.map()` automatically scales containers to match the input size.

### .starmap() — Multiple Arguments

```python
@app.function()
def add(a, b):
    return a + b

results = list(add.starmap([(1, 2), (3, 4), (5, 6)]))
```

### .spawn() / .gather() — Fire and Forget

```python
# Spawn non-blocking
call_a = task_a.spawn("input_a")
call_b = task_b.spawn("input_b")

# Gather results
results = modal.FunctionCall.gather(call_a, call_b)

# Or get individually
result_a = call_a.get()  # Blocks until ready
result_b = call_b.get(timeout=30)  # With timeout
```

### Async Variants

```python
@app.local_entrypoint()
async def main():
    # Async remote call
    result = await fn.remote.aio(input)

    # Async map
    async for result in fn.map.aio(items):
        print(result)

    # Async generator
    async for chunk in fn.remote_gen.aio(input):
        print(chunk)
```

## Dynamic Batching

Groups individual requests into batches automatically:

```python
@app.cls(gpu="A10G")
class BatchProcessor:
    @modal.batched(max_batch_size=16, wait_ms=100)
    def embed(self, texts: list[str]) -> list[list[float]]:
        # Receives a list, returns a list
        # Modal batches individual .remote("text") calls
        return self.model.encode(texts).tolist()
```

Callers send single items — Modal batches them:
```python
# Each of these becomes part of a batch
result = BatchProcessor().embed.remote("single text")
```

## Concurrent Inputs Per Container

```python
@app.cls(gpu="A10G", max_containers=1)
@modal.concurrent(max_inputs=100)
class Server:
    @modal.method()
    async def handle(self, request):
        return await self.process(request)
```

Multiple requests share one container (and one GPU). Use for I/O-bound or lightweight GPU work.

## Runtime Configuration Variants

```python
@app.cls()
class Worker:
    @modal.method()
    def process(self, data):
        return compute(data)

# Create variants with different resources
gpu_worker = Worker.with_options(gpu="T4")
big_worker = Worker.with_options(gpu="A100", memory=65536)
```

## Job Queues

### Queue + Dict Coordination

```python
@app.function()
def worker(q: modal.Queue, results: modal.Dict, batch: list[str]):
    for item in batch:
        result = process(item)
        results[item] = result
    q.put("batch_done")

@app.function()
def coordinator(items: list[str]):
    with modal.Queue.ephemeral() as q, modal.Dict.ephemeral() as results:
        # Split into batches and spawn workers
        batch_size = 100
        batches = [items[i:i+batch_size] for i in range(0, len(items), batch_size)]

        for batch in batches:
            worker.spawn(q, results, batch)

        # Wait for all workers
        for _ in range(len(batches)):
            q.get(timeout=300)

        return dict(results)
```

### Queue API

```python
with modal.Queue.ephemeral() as q:
    q.put("item")                    # Put one item
    q.put_many(["a", "b", "c"])      # Put many
    item = q.get(timeout=10)         # Get one (blocks)
    items = q.get_many(100, timeout=5)  # Get up to 100
```

### Dict API

```python
with modal.Dict.ephemeral() as d:
    d["key"] = "value"       # Set
    val = d["key"]           # Get
    d.pop("key")             # Remove
    "key" in d               # Check existence
```

## Sandboxes — Isolated Code Execution

```python
@app.function()
def run_user_code(code: str) -> str:
    image = modal.Image.debian_slim().apt_install("python3")
    sandbox = modal.Sandbox.create(app=app, image=image, timeout=30)

    # Execute code
    process = sandbox.exec("python3", "-c", code)
    output = process.stdout.read()
    errors = process.stderr.read()

    sandbox.terminate()
    return output

# Multi-language support
image = modal.Image.debian_slim().apt_install("nodejs", "ruby", "php")
sandbox = modal.Sandbox.create(app=app, image=image)
sandbox.exec("node", "-e", "console.log('hello from JS')")
sandbox.exec("ruby", "-e", "puts 'hello from Ruby'")
```

### Sandbox File Operations

```python
sandbox = modal.Sandbox.create(app=app, image=image)

# Upload file
sandbox.put_file("local_input.txt", "/sandbox/input.txt")

# Run processing
sandbox.exec("python3", "process.py", "/sandbox/input.txt")

# Download result
sandbox.get_file("/sandbox/output.txt", "local_output.txt")

sandbox.terminate()
```

## Scheduling

### Cron Schedule

```python
@app.function(schedule=modal.Cron("0 9 * * *"))  # Daily at 9 AM UTC
def daily_job():
    pass

@app.function(schedule=modal.Cron("*/15 * * * *"))  # Every 15 minutes
def frequent_check():
    pass

# With timezone
@app.function(schedule=modal.Cron("0 9 * * *", timezone="America/New_York"))
def daily_eastern():
    pass
```

### Period Schedule

```python
@app.function(schedule=modal.Period(hours=1))     # Every hour
@app.function(schedule=modal.Period(minutes=30))   # Every 30 min
@app.function(schedule=modal.Period(seconds=10))   # Every 10 sec
```

**Deploy scheduled functions:** `modal deploy app.py` — they run automatically.

## Scaling Configuration

| Parameter | Purpose | Default |
|-----------|---------|---------|
| `concurrency_limit` | Max total containers | None (auto) |
| `max_containers` | Hard container limit | None |
| `allow_concurrent_inputs` | Requests per container | 1 |
| `keep_warm` | Min warm containers | 0 |
| `container_idle_timeout` | Seconds before shutdown | ~60s |
