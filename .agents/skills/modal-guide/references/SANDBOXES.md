# Modal Sandboxes

Reference for `modal.Sandbox` — secure containers for running untrusted user or agent code on Modal. Sandboxes let you define container environments at runtime and execute arbitrary commands inside them. Common uses: running LLM-generated code, code interpreters, isolated build/test envs, ephemeral git operations, custom dependency containers per request.

**Source:** https://modal.com/docs/guide/sandboxes

## Mental Model

Sandboxes are like Modal Functions, but the image and lifecycle are controlled imperatively from the calling code instead of being declared with a decorator. You create one, exec commands against it, optionally move files in/out or snapshot its filesystem, and terminate it.

Unlike Functions, Sandboxes have **no default access to your workspace's resources** — they are isolated by design (gVisor runtime + custom syscall filtering). You opt in to volumes, secrets, and network access explicitly.

## Creating a Sandbox

```python
import modal

app = modal.App.lookup("my-sandbox-app", create_if_missing=True)

image = modal.Image.debian_slim(python_version="3.11").pip_install("requests==2.32.3")

sb = modal.Sandbox.create(
    app=app,
    image=image,
    timeout=60 * 10,                    # max lifetime, default 5min, max 24h
    idle_timeout=60,                    # auto-terminate after N seconds idle
    cpu=2.0,
    memory=4096,
    gpu="T4",                           # optional
    volumes={"/data": modal.Volume.from_name("shared", create_if_missing=True)},
    secrets=[modal.Secret.from_name("api-keys")],
    workdir="/workspace",
    block_network=False,
    encrypted_ports=[8080],             # expose ports via tunnels
)
```

**App requirement:** When creating a sandbox from outside a Modal container (e.g., a CLI), you must pass an `App`. Use `modal.App.lookup(name, create_if_missing=True)` to attach to one without writing a deployed module.

**Idle activity** that resets `idle_timeout`: running `exec()`, writing to stdin, or active TCP connections through tunnels.

## Executing Commands

`Sandbox.exec()` returns a `ContainerProcess` with `stdin`, `stdout`, `stderr` streams plus `returncode` / `poll()` / `wait()`.

```python
# Blocking — read everything once it finishes
proc = sb.exec("python3", "-c", "print(2 + 2)")
output = proc.stdout.read()
errors = proc.stderr.read()
exit_code = proc.wait()

# Streaming — iterate as lines arrive
proc = sb.exec("bash", "-c", "for i in 1 2 3; do echo $i; sleep 1; done")
for line in proc.stdout:
    print(line, end="")

# Bidirectional — write to stdin
proc = sb.exec("python3", "-i")
proc.stdin.write(b"print('hello')\n")
proc.stdin.drain()
proc.stdin.write_eof()
print(proc.stdout.read())

# Async variant
proc = sb.exec("python3", "script.py")
async for line in proc.stdout.aio():
    ...
await proc.wait.aio()
```

**Stream types:**
- `stdin` is a `StreamWriter` (sync + async writes, `drain()`, `write_eof()`)
- `stdout` / `stderr` are `StreamReader`s — `.read()` blocks until process exits and returns full output; iteration streams line by line
- Streams respect `Sandbox.exec(timeout=...)`

## Filesystem Access

Direct file API on the Sandbox handle (reads ≤ 5GB, writes any size):

```python
# Upload / download — convenience methods
sb.put_file("./local.txt", "/workspace/input.txt")
sb.get_file("/workspace/output.txt", "./result.txt")

# File handle API
with sb.open("/workspace/data.json", "w") as f:
    f.write('{"x": 1}')

with sb.open("/workspace/data.json", "r") as f:
    contents = f.read()

# Directory ops
sb.mkdir("/workspace/sub", parents=True)
entries = sb.listdir("/workspace")
sb.remove("/workspace/old.txt")
```

**When to use what:**
- `put_file` / `get_file` / `open` — per-sandbox transient state
- `modal.Volume` mounted into the sandbox — share data across many sandboxes; call `vol.commit()` (or use Volume v2 `sync`) to persist mid-run
- `modal.CloudBucketMount` — auto-syncing S3/GCS access
- `image.add_local_file()` / `add_local_dir()` — bake files in when they're reused across sandboxes or needed at entrypoint time

## Networking and Tunnels

Sandboxes are isolated by default. Opt into network surface explicitly.

```python
# Lock down outbound
sb = modal.Sandbox.create(app=app, image=image, block_network=True)

# Allowlist outbound CIDRs
sb = modal.Sandbox.create(
    app=app,
    image=image,
    cidr_allowlist=["10.0.0.0/8", "192.168.0.0/16"],
)

# Expose a port to the internet via a tunnel
sb = modal.Sandbox.create(
    app=app,
    image=image,
    encrypted_ports=[8080],         # TLS-wrapped TCP
    # unencrypted_ports=[5000],     # raw TCP (avoid unless required)
    # h2_ports=[8443],              # HTTP/2 + TLS
)
tunnel = sb.tunnels()[8080]
print(tunnel.url)                    # https://<id>.w.modal.host
print(tunnel.tls_socket)             # (host, port) for raw clients
```

**Sandbox Connect Tokens** (recommended for HTTP/WebSocket): server listens on port 8080, Modal authenticates the request and forwards it with a `X-Verified-User-Data` header. Token is passed via `Authorization` header, `_modal_connect_token` query param, or cookie.

**Security note:** Sandboxes run on gVisor with custom syscall filtering. Even with network access, blast radius is limited to the container itself — no default access to your workspace resources.

## Readiness Probes (Beta)

Wait for a server inside the sandbox to be ready before sending traffic:

```python
sb = modal.Sandbox.create(
    app=app,
    image=image,
    encrypted_ports=[8080],
    # TCP probe — port becomes accept()-able
    readiness_probe=modal.SandboxReadinessProbe.tcp(port=8080, interval_ms=200),
    # OR exec probe — command exits 0
    # readiness_probe=modal.SandboxReadinessProbe.exec(["curl", "-f", "http://localhost:8080/health"]),
)
sb.wait_until_ready()                # blocks; raises after 5min
```

## Filesystem Snapshots

`Sandbox.snapshot_filesystem()` captures the sandbox's FS as a `modal.Image`. Stored as a diff vs. the base image, so only modified files are persisted. Snapshots last indefinitely.

```python
sb = modal.Sandbox.create(app=app, image=base_image)
sb.exec("git", "clone", "https://github.com/org/repo.git", "/repo").wait()
sb.exec("pip", "install", "-r", "/repo/requirements.txt").wait()

snap_image = sb.snapshot_filesystem()
sb.terminate()

# Cold-start a fresh sandbox from the snapshot
sb2 = modal.Sandbox.create(app=app, image=snap_image)
```

Use this to: cache expensive setup steps, work around the 24h max sandbox lifetime by checkpointing and resuming, ship reproducible per-task images.

### Directory snapshots (beta)

```python
snap = sb.snapshot_directory("/workspace/.venv")     # snapshot one dir
sb2.mount_image(snap, "/workspace/.venv")             # reuse later
sb2.unmount_image("/workspace/.venv")
```

Directory snapshots expire **30 days** after last use; missing snapshots raise `NotFoundError`. Useful for separating dependency state from application code.

### Memory snapshots (alpha)

```python
mem_snap = sb._experimental_snapshot()                # SandboxSnapshot object
sb_clone = modal.Sandbox._experimental_from_snapshot(mem_snap)

# Rehydrate later by ID
mem_snap2 = modal.SandboxSnapshot.from_id(snap_id)
```

Captures full memory + filesystem. **Constraints:** 7-day expiration, no GPUs, can't snapshot while an `exec` is running, must restore on the same instance type.

## Lifecycle and Lookup

```python
# Named sandboxes — addressable by name within an app
sb = modal.Sandbox.create(app=app, image=image, name="user-42-session")
sb_again = modal.Sandbox.from_name("my-sandbox-app", "user-42-session")

# By ID
sb = modal.Sandbox.from_id(sandbox_id)

# Tagging + listing
sb = modal.Sandbox.create(app=app, image=image, tags={"user": "42", "kind": "repl"})
for s in modal.Sandbox.list(app_id=app.app_id, tags={"user": "42"}):
    ...

# Wait for completion / get exit code
exit_code = sb.wait()
print(sb.returncode)

# Termination
sb.terminate()                        # explicit shutdown
sb.detach()                           # close client connection, leave sandbox running
```

**Names:** alphanumeric + `-`, `.`, `_`, max 64 chars, unique per app.

**Detach gotcha:** after `detach()`, further calls on the original handle aren't guaranteed. Re-acquire via `from_id()` or `from_name()` if you need to keep interacting.

## Patterns

### Run untrusted code from an LLM

```python
@app.function()
def run_user_code(code: str, timeout_s: int = 30) -> dict:
    image = modal.Image.debian_slim(python_version="3.11").pip_install("numpy==2.1.3")
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=timeout_s,
        block_network=True,           # no exfiltration
        cpu=1.0,
        memory=512,
    )
    try:
        proc = sb.exec("python3", "-c", code)
        return {
            "stdout": proc.stdout.read(),
            "stderr": proc.stderr.read(),
            "exit_code": proc.wait(),
        }
    finally:
        sb.terminate()
```

### Pooling with snapshots

```python
# One-time: build and snapshot the heavy environment
warm = modal.Sandbox.create(app=app, image=base)
warm.exec("pip", "install", "torch==2.6.0", "transformers==4.51.0").wait()
warm_image = warm.snapshot_filesystem()
warm.terminate()

# Per request: cheap sandbox from the warm image
def handle(req):
    sb = modal.Sandbox.create(app=app, image=warm_image, timeout=120)
    try:
        return sb.exec("python3", "-c", req.code).stdout.read()
    finally:
        sb.terminate()
```

### Long-running server inside a sandbox

```python
sb = modal.Sandbox.create(
    app=app,
    image=image,
    encrypted_ports=[8080],
    timeout=60 * 60 * 4,
    readiness_probe=modal.SandboxReadinessProbe.tcp(port=8080),
)
sb.exec("python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080")
sb.wait_until_ready()
url = sb.tunnels()[8080].url
```

## Gotchas

- **`App` required from outside Modal.** Use `modal.App.lookup(name, create_if_missing=True)` in CLIs/scripts; in-cluster code can pass the running `app`.
- **`stdout.read()` blocks until process exit.** Use iteration (`for line in proc.stdout`) for streaming or you'll deadlock on long-running commands.
- **`timeout` is a hard cap.** Default 5min, max 24h. For longer work, snapshot + resume.
- **`idle_timeout` counts no-activity.** A sandbox that's just sitting there waiting for a tunnel connection counts as idle unless data flows.
- **No workspace resources by default.** Mount what you need (volumes, secrets) explicitly.
- **`unencrypted_ports`** sends raw TCP to the public internet. Prefer `encrypted_ports` or Connect Tokens.
- **Memory snapshots can't run with GPUs and can't snapshot during an active `exec`.** Plan checkpoints around quiescent moments.
- **Directory snapshots expire after 30 days of disuse** — handle `NotFoundError` on `mount_image()`.
- **`detach()` ≠ `terminate()`.** Detach leaves the sandbox running on Modal's side; terminate stops it.
