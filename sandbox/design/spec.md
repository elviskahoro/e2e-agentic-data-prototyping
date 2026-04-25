# Spec: port `dlt_agent_sandbox_with_api.py` from Dagger → Modal Sandboxes

## Goal

Replace Dagger with Modal Sandboxes as the container runtime for the agent-run dlt demo, and install the Hotdata SDK from its public GitHub repo instead of mounting `../sdk-python`. Preserve the host-side `HotdataSession` behavior (sandbox creation, header scoping, preview) and the container's stdout JSON contract verbatim. The original Dagger driver stays in place for reference.

**Self-contained packaging constraint (from user):** all code and assets must live under `sandbox/`. The new directory must be deployable as-is — no imports or file references outside `sandbox/`.

## Final layout under `sandbox/`

```
sandbox/
├── design/
│   ├── modal_port_spec_prompt.md   (existing)
│   └── spec.md                     (this file)
├── dlt_agent_sandbox_with_modal.py (NEW — host driver, Modal-based)
├── dlt_agent_container_entry.py    (COPY of local/dlt_agent_container_entry.py, byte-for-byte)
├── dlt_datagen_module/             (COPY of local/dlt_datagen_module/, with one trim — see below)
│   ├── pyproject.toml
│   └── src/dlt_datagen/{__init__.py,load.py,main.py}
├── pyproject.toml                  (NEW — host-side deps for the Modal driver only)
└── readme.md                       (NEW — one-pager: how to run)
```

**Trim on copy of `dlt_datagen_module/`:** the existing `pyproject.toml` declares `dependencies = ["dagger-io"]` with `[tool.uv.sources] dagger-io = { path = "sdk", editable = true }`. That `sdk` path doesn't exist and `dagger-io` isn't actually used by `load.py` — it's a leftover from when this module was a Dagger module. Drop both lines on copy. Also drop `dagger.json`, `uv.lock`, `LICENSE`, `.gitattributes`, `.gitignore` — the package only needs to be `pip install`-able / importable, and uv.lock would re-introduce the dagger-io pin. Final `dlt_datagen_module/pyproject.toml`:

```toml
[project]
name = "dlt_datagen"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = []

[build-system]
requires = ["uv_build>=0.8.4,<0.9.0"]
build-backend = "uv_build"
```

(Runtime deps `dlt`, `duckdb`, `pyarrow`, `hotdata` are installed at the image layer, not declared here, matching how the Dagger version did it.)

**Host `sandbox/pyproject.toml`** (driver-only — no `dagger-io`, no local SDK path):

```toml
[project]
name = "dlt-agent-sandbox-modal-demo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "modal>=0.66",
    "hotdata @ git+https://github.com/hotdata-dev/sdk-python@main",
]
```

The host needs `hotdata` because `HotdataSession` runs on the host. Pin policy is the same as inside the image — see decision §10.

## Dagger → Modal mapping

| Dagger primitive                                  | Modal equivalent                                                  |
| ------------------------------------------------- | ----------------------------------------------------------------- |
| `dag.container().from_("…uv:python3.13…")`         | `modal.Image.from_registry("ghcr.io/astral-sh/uv:python3.13-bookworm-slim", add_python=None)` |
| `.with_mounted_cache("/root/.cache/uv", …)`        | `modal.Volume.from_name("dlt-datagen-uv-cache", create_if_missing=True)` mounted at `/root/.cache/uv` on the Sandbox |
| `.with_env_variable(K, V)` (non-secret)            | `modal.Secret.from_dict({K: V})` passed in `secrets=[...]` (Modal has no separate non-secret env on Sandbox; see §3) |
| `.with_secret_variable("HOTDATA_API_KEY", secret)` | same `Secret.from_dict({"HOTDATA_API_KEY": api_key})`             |
| `.with_exec(["uv","pip","install",...])`           | `image.run_commands("uv pip install --system ...")`               |
| `.with_mounted_directory("/app/dlt_datagen", …)`   | `image.add_local_dir(local_path, "/app/dlt_datagen", copy=False)` (runtime-only — see §5) |
| `.with_mounted_file("/app/entry.py", …)`           | `image.add_local_file(local_path, "/app/entry.py", copy=False)`   |
| `.with_workdir("/app")`                            | `image.workdir("/app")`                                           |
| `.with_exec(["python","/app/entry.py"])`           | command passed positionally to `Sandbox.create(..., "python","/app/entry.py", ...)` (see §2) |
| `await container.stdout()`                         | `sb.wait(); sb.stdout.read()`                                     |
| Dagger raises on non-zero exit                     | manual check on `sb.returncode` (see §9)                          |

## Image build (proposed)

```python
import modal

# 1. Base image with uv preinstalled. add_python=None: the registry image
#    already provides Python 3.13; don't let Modal re-install it.
image = (
    modal.Image.from_registry(
        "ghcr.io/astral-sh/uv:python3.13-bookworm-slim",
        add_python=None,
    )
    # 2. Pip install layer FIRST — large, slow, cache-friendly. Hashed by the
    #    command string, so changing pins busts the layer (intended).
    #    --system because the uv image has no venv; we install into the
    #    interpreter's site-packages just like the Dagger version did.
    .env({"UV_LINK_MODE": "copy"})
    .run_commands(
        "uv pip install --system "
        "  'dlt[duckdb]' duckdb pyarrow "
        "  'git+https://github.com/hotdata-dev/sdk-python@main'"
        # SDK runtime deps (urllib3, python-dateutil, pydantic, typing-extensions)
        # come in transitively from the SDK's pyproject — no manual pin list needed,
        # which is the whole point of dropping the local mount.
    )
    .workdir("/app")
    # 3. Source layers LAST — change every iteration. copy=False (default):
    #    files are made available at runtime in the sandbox's filesystem but
    #    are NOT baked into the image, so editing them does not invalidate
    #    the pip layer above. No subsequent build step needs to read them
    #    (we only `python /app/entry.py` at runtime), so copy=True would be
    #    pure waste here.
    .add_local_dir(
        str(SANDBOX_DIR / "dlt_datagen_module" / "src" / "dlt_datagen"),
        "/app/dlt_datagen",
        copy=False,
    )
    .add_local_file(
        str(SANDBOX_DIR / "dlt_agent_container_entry.py"),
        "/app/entry.py",
        copy=False,
    )
)
```

**Why no `copy=True` anywhere:** `copy=True` only matters when a *later* `run_commands` / build step needs to read those files. Here the only thing that touches `/app/dlt_datagen` and `/app/entry.py` is the runtime `python /app/entry.py` invocation, which sees runtime-mounted files just fine. Picking `copy=True` would force every source edit to invalidate everything below it.

**uv cache volume note:** Dagger's `with_mounted_cache` is mounted at *image build time* and during the exec. Modal's equivalent during build is automatic for the pip layer (Modal caches build layers by command string), and at *runtime* we mount a `modal.Volume` at `/root/.cache/uv` on the Sandbox. The volume is **only useful if the runtime ever runs `uv pip install` again** — our entry script doesn't, so the volume is optional. **Recommendation: omit it.** Keep the image simple; the build-layer cache handles everything we care about. If a future iteration adds runtime pip installs, add the volume then.

## Host driver (proposed `dlt_agent_sandbox_with_modal.py`)

Keep `HotdataSession` and `parse_tables` **inline in the new file** rather than importing from `local/dlt_agent_sandbox_with_api.py`. Reasoning:

- Self-containment constraint — `sandbox/` must be deployable without reaching into `local/`.
- The Dagger file imports `dagger`, `dagger.dag`, `@function`, `@object_type` at module top — importing it would force `dagger-io` to be a host-side dep of the Modal driver, which defeats the port.
- The duplication is ~80 lines of pure HTTP code that doesn't change often. Worth the copy.

Drop the `@object_type` / `@function` decorators on `Pipeline`. Those are Dagger-module bindings; the Modal version is a plain class (or just a function — see below).

**Drop the `Pipeline` class entirely.** It existed to satisfy Dagger's module/object_type framing. In Modal there is no analogous requirement — a single function `run_in_modal_sandbox(...)` is clearer. Keep `parse_tables` as a module-level function. This is a small, justified deviation from the prompt's "show the Pipeline replacement" framing.

```python
"""Host driver: same demo as dlt_agent_sandbox_with_api.py, but the container
runs on Modal Sandboxes and the Hotdata SDK is installed from GitHub."""

import json, os, secrets, sys, time
from pathlib import Path

import modal
import hotdata  # host-side, from sandbox/pyproject.toml
from hotdata.api_client import ApiClient
from hotdata.models.query_request import QueryRequest

SANDBOX_DIR = Path(__file__).resolve().parent
APP_NAME = "dlt-datagen-demo"
SANDBOX_TIMEOUT_SECONDS = 600  # see §8

# --- HotdataSession: copied verbatim from local/dlt_agent_sandbox_with_api.py
#     (lines 26–101). DO NOT EDIT during the port — diff against the original
#     should be empty for this block. ---
class HotdataSession: ...

# --- image build: the block shown above ---
image = ...

def parse_tables(stdout: str) -> list[str]:
    """Container's last stdout line is JSON: {"tables": [...]}."""
    return list(json.loads(stdout.strip().splitlines()[-1])["tables"])


def run_in_modal_sandbox(
    *,
    api_key: str,
    api_url: str,
    workspace_id: str,
    hotdata_sandbox_id: str,
    run_id: str,
) -> str:
    """Spawn one Modal Sandbox, run the entry script, return stdout. Raises on non-zero exit."""
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    secret = modal.Secret.from_dict({
        "HOTDATA_API_KEY": api_key,
        "HOTDATA_API_URL": api_url,
        "HOTDATA_WORKSPACE_ID": workspace_id,
        "HOTDATA_SANDBOX_ID": hotdata_sandbox_id,
        "DLT_DATAGEN_RUN_ID": run_id,
    })
    modal_sb = modal.Sandbox.create(
        "python", "/app/entry.py",
        image=image,
        app=app,
        secrets=[secret],
        timeout=SANDBOX_TIMEOUT_SECONDS,
    )
    try:
        modal_sb.wait()
        stdout = modal_sb.stdout.read()
        stderr = modal_sb.stderr.read()
        # Mirror container stderr to host stderr so the user sees the dlt
        # progress logs (Dagger streams them by default via log_output=sys.stderr).
        if stderr:
            sys.stderr.write(stderr)
        if modal_sb.returncode != 0:
            raise RuntimeError(
                f"Modal sandbox exited with code {modal_sb.returncode}. "
                f"Last stdout line: {stdout.strip().splitlines()[-1] if stdout.strip() else '(empty)'}"
            )
        return stdout
    finally:
        # Belt-and-braces: wait() should have ended the container, but if we
        # raised before wait() (KeyboardInterrupt) we still want to free the
        # remote slot. terminate() is a no-op on an already-finished sandbox.
        try:
            modal_sb.terminate()
        except Exception:
            pass


def main() -> None:
    api_key = os.environ.get("HOTDATA_API_KEY")
    if not api_key:
        raise RuntimeError("HOTDATA_API_KEY must be set before running.")
    run_id = f"{int(time.time() * 1000):012x}{secrets.token_hex(10)}"
    print(f"→ run {run_id}", file=sys.stderr)
    host = os.environ.get("HOTDATA_API_URL", "https://api.hotdata.dev")

    with HotdataSession(api_key, host) as session:
        hotdata_sandbox_id = session.create_sandbox(f"agent_{run_id}")
        stdout = run_in_modal_sandbox(
            api_key=api_key,
            api_url=host,
            workspace_id=session.workspace_id,
            hotdata_sandbox_id=hotdata_sandbox_id,
            run_id=run_id,
        )
        tables = parse_tables(stdout)
        print(f"→ container uploaded tables: {tables}", file=sys.stderr)
        previews = session.preview(tables)

    print("\n=== preview ===", flush=True)
    for table, rows in previews.items():
        print(f"[{table}]", flush=True)
        for row in rows:
            print("\t".join("" if v is None else str(v) for v in row), flush=True)
        print(flush=True)


if __name__ == "__main__":
    main()  # synchronous; Modal's sync API removes the asyncio.run wrapper
```

## Diff of `main()` against the Dagger version

| Dagger `main()` line                                                   | Modal `main()`                                                              |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `async def main()`                                                     | `def main()` — Modal's Python API is synchronous here                       |
| `asyncio.run(main())`                                                  | `main()`                                                                    |
| (lines 183–197 — env reads, run_id, host, `HotdataSession.__enter__`, `create_sandbox`) | identical |
| `async with dagger.connection(dagger.Config(log_output=sys.stderr)):`   | gone — Modal needs no client connection scope                               |
| `api_key_secret = dag.set_secret("hotdata-api-key", api_key)`           | gone — secret built inline inside `run_in_modal_sandbox`                    |
| `pipeline = Pipeline(...)` + `await pipeline.run_in_container(...).stdout()` | one call: `run_in_modal_sandbox(api_key=..., api_url=..., workspace_id=..., hotdata_sandbox_id=..., run_id=...)` |
| (lines 208–214 — `parse_tables`, preview, print) | identical (preview formatting unchanged) |

## Naming convention

Two things called "sandbox" coexist. Convention:

- **Modal Sandbox** → local var `modal_sb`, function suffix `_in_modal_sandbox`, image `image` (no prefix; only one image).
- **Hotdata Sandbox** → local var `hotdata_sandbox_id` (always with `_id` suffix; the value is a string, never an object), method `HotdataSession.create_sandbox` (kept as-is — already qualified by the class name).
- The Modal app name `"dlt-datagen-demo"` and the Hotdata sandbox name `f"agent_{run_id}"` are different namespaces; keep them distinct. Don't reuse `run_id` as the Modal sandbox name.

## Resolutions to design decisions

1. **Sandbox vs Function.** Use `modal.Sandbox`. The demo's narrative is "an agent spawns a container, the container exits, we read what it produced." `@app.function` would invert that into "host calls a typed Python function," dropping the stdout-JSON contract that `dlt_agent_container_entry.py` is built around. Keeping Sandbox also means `dlt_agent_container_entry.py` runs unchanged (hard constraint).

2. **Entrypoint command vs `sb.exec`.** Pass the command positionally to `Sandbox.create("python", "/app/entry.py", ...)`. The container is single-shot — one process, one stdout, one exit code. `sb.exec` is for keep-alive sandboxes you reuse; we don't, and adding it would also force a separate `sb.terminate()` + extra error paths.

3. **Secrets vs env: one dict or split?** One dict. Modal's Sandbox API has no plain-env channel — non-secret values still go through `Secret.from_dict`. Splitting into two `Secret`s would be theatre: both end up as env vars in the same process, with the same exposure. The real-secret discipline is "don't log them," which lives in `dlt_agent_container_entry.py`. Argued against splitting because the existing Dagger code also lumped them together (plain env vars next to the one secret env var) and nobody flagged it.

4. **Image layer ordering.** Pip install BEFORE `add_local_dir` / `add_local_file`. The pip layer is multi-second and stable across iterations; the source files churn every edit. With the order above, editing `dlt_agent_container_entry.py` re-runs only the trailing `add_local_file` step. (See image-build code block for the explicit ordering.)

5. **`copy=True` choices.** None of the `add_local_*` calls need `copy=True`. Reasoning is in-line in the image-build block: nothing later in the build reads these files; they're only read at runtime by `python /app/entry.py`, which sees the runtime mount.

6. **App lifecycle.** `modal.App.lookup("dlt-datagen-demo", create_if_missing=True)`. Stable name → image build cache survives across runs (which is the whole point of layer ordering — wasted if the app is fresh every time). Multiple concurrent runs share the app cleanly because each call creates its own Sandbox under it.

7. **Cleanup.** `modal_sb.wait()` then read stdout/stderr, with `modal_sb.terminate()` in a `finally` as a guard against `KeyboardInterrupt` between create and wait. Modal docs mention `detach()` for the *local* connection; for the create-wait-read pattern there is no separate connection to detach — `wait()` already blocks until the container ends and `terminate()` is idempotent on a finished sandbox.

8. **Timeout.** 600s. The dlt run touches a few small generated tables and uploads parquet to Hotdata — the warm-path Dagger run today is well under 60s. 600s leaves headroom for cold image build (first run after a pip-pin change can take a few minutes) without letting a stuck container burn slot for an hour. The Dagger version has no explicit timeout because the host process supervises it; on Modal the sandbox runs remotely, so an explicit ceiling matters.

9. **Error paths.** Check `modal_sb.returncode != 0` after `wait()`. Raise `RuntimeError` whose message includes the return code and the last line of stdout (which, if the entry script got far enough, would be the `{"tables": [...]}` line, and otherwise will give a hint of where it died). Also dump the container's stderr to the host's stderr unconditionally so the dlt progress logs show up the way the Dagger version's `log_output=sys.stderr` made them show up. Do NOT swallow the stderr inside the RuntimeError message — it's typically multi-KB.

10. **Hotdata SDK ref.** Pin to `main` initially, both in the image's pip install string and in `sandbox/pyproject.toml`. **Reasoning + tradeoff stated explicitly:** Modal hashes the *install command string* for caching, not the upstream commit. So an unpinned `main` will keep returning a stale cached layer indefinitely until the command string changes — which makes "main" effectively a soft pin to whatever commit was current at first build. This is fine for the demo (we want freshness without thinking about it) but is a foot-gun if anyone expects "main" to mean "latest." **Mitigation in spec:** on every demo iteration that needs a fresh SDK, bump the ref to a specific short SHA (`@<sha>`) — the changed string busts the cache. For a tagged release path, switch to `@v0.x.y`. Either change forces a rebuild. Document this in `sandbox/readme.md` so the next person doesn't get confused by stale SDK code.

## What could go wrong

1. **GitHub rate limits / outage during pip install.** A `git+https://github.com/...` install runs a `git clone` against github.com. Anonymous clone is rate-limited (~60/hr per IP) and Modal builders share IPs. Mitigation: the layer is cached aggressively — once one builder pulls successfully, subsequent runs reuse the layer. If a clone fails, the spec's `RuntimeError` from a non-zero exit will surface a `pip install` failure cleanly. Falling back to a Modal `Image.from_registry` baked with the SDK pre-installed is the next step if this becomes recurrent; out of scope for v1.

2. **Modal cold start / image build on first run.** First invocation builds the image (pip install layer is the slow part — pulling dlt + duckdb + pyarrow + the SDK). 600s timeout covers it, but the user will see a multi-minute pause with no progress output from the container (the dlt logs only start once the entry script runs). Mitigation: add a `print("→ building/spinning up Modal sandbox...", file=sys.stderr)` line in `run_in_modal_sandbox` immediately before `Sandbox.create` so the user knows what's happening. (Concrete spec instruction, not a hand-wave.)

3. **SDK ref drift / stale cache.** As noted in §10. Concretely: a contributor pushes a breaking change to `hotdata-dev/sdk-python@main`, but the cached image layer still holds the old SDK, so the host (using the new SDK from `pyproject.toml`) and the container (using the cached old SDK) diverge — the container makes API calls the host doesn't, or vice versa. Mitigation: pin both sides to the same ref string. The spec's host `pyproject.toml` and the image install command MUST use the same ref; this should be a single Python constant in `dlt_agent_sandbox_with_modal.py` (e.g. `HOTDATA_SDK_REF = "main"`) interpolated into the install command, so a reviewer can see at a glance that they match. (Note that the host install is at `uv sync` time and won't pick up the constant automatically — the readme should call this out.)

4. **`X-Workspace-Id` header forgotten in container.** `dlt_agent_container_entry.py` already sets all three headers from env. Not a regression risk in the port itself, but worth verifying — see checklist.

5. **`add_local_dir` picks up `__pycache__`.** The copy of `local/dlt_datagen_module/src/dlt_datagen/` will likely have a `__pycache__/` subdir. Modal's `add_local_dir` defaults to including everything; bytecode for the host's Python version inside the container is harmless but ugly. Cheap fix: pass `ignore=["__pycache__"]` to `add_local_dir`.

## Validation checklist (for the implementation agent)

After writing the code, run these from `/Users/elvis/Documents/hotdata/demo/sandbox/`:

- [ ] `tree -L 3 .` shows the layout in §"Final layout" — no `dagger.json`, no `uv.lock` under `dlt_datagen_module/`, no references to `../sdk-python`.
- [ ] `grep -r dagger sandbox/` (from repo root) returns zero hits in `sandbox/` — proves no Dagger leakage.
- [ ] `grep -r '\.\./sdk-python' sandbox/` returns zero hits — proves the SDK comes from GitHub.
- [ ] `uv sync` (or `pip install -e .`) inside `sandbox/` succeeds and pulls `hotdata` from the GitHub URL.
- [ ] `HOTDATA_API_KEY=... python dlt_agent_sandbox_with_modal.py` runs end-to-end and:
  - [ ] prints `→ run <id>`, `→ using workspace ...`, `→ created sandbox ...` on stderr (same as Dagger version);
  - [ ] prints dlt's load logs to stderr from inside the container;
  - [ ] prints `→ container uploaded tables: ['purchases', 'customers']` to stderr;
  - [ ] prints a `=== preview ===` block on stdout with rows for both tables — same shape as the Dagger version's output (header row, then up to 10 data rows, tab-separated).
- [ ] Re-run immediately. Second run reuses the Modal app and image layers; total wall-clock should drop substantially (no pip install, no image rebuild).
- [ ] Force-fail path: temporarily edit the entry script in the copied location to `raise RuntimeError("boom")`, run again, confirm the host raises with a non-zero return code message and the container's traceback shows up on host stderr. Revert.
- [ ] The original `local/dlt_agent_sandbox_with_api.py` is untouched (`git diff local/` is empty).
