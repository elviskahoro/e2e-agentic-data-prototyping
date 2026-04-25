# Task: produce an implementation spec — port `dlt_agent_sandbox_with_api.py` from Dagger to Modal Sandboxes

You are writing a SPEC, not code. The spec will be reviewed by a human, then handed to an
implementation agent. Do not edit any source files. Output a single markdown spec.

## Background

The repo at `/Users/elvis/Documents/hotdata/demo` is a demo that shows an "agent-run dlt"
pipeline: a containerized job runs `dlt` against an in-memory DuckDB, converts arrow tables
to parquet bytes, uploads them straight to the Hotdata API, and registers them as datasets
inside a per-run Hotdata sandbox. A host-side Python driver orchestrates this and then runs
a verification query against the freshly-loaded tables.

Today the container runtime is Dagger. We want to replace Dagger with Modal Sandboxes
(https://modal.com/docs/guide/sandboxes) so the demo shows the same agent-run pipeline
landing in Hotdata, but executed on Modal's infrastructure instead of local Dagger.

We also want to install the Hotdata SDK from its public GitHub repo
(https://github.com/hotdata-dev/sdk-python) instead of mounting the local editable
checkout at `../sdk-python`. The repo's `pyproject.toml` already declares the SDK's
runtime deps (urllib3, python-dateutil, pydantic, typing-extensions), so a single
`pip install git+https://github.com/hotdata-dev/sdk-python@<ref>` replaces both the
mount-and-`--no-deps`-install dance and the manually-pinned dep list.

## Files to read FIRST (in this order)

1. `/Users/elvis/Documents/hotdata/demo/dlt_agent_sandbox_with_api.py` — the existing
   Dagger driver. This is the file being ported. Pay attention to:
   - `HotdataSession` (lines 26–101): host-side API client, sandbox creation, preview.
     This MUST be preserved verbatim — it's pure Hotdata API work and is independent
     of the container runtime.
   - `Pipeline.run_in_container` (lines 110–167): the Dagger-specific image build and
     execution. This is the part being replaced.
   - `Pipeline.parse_tables` (lines 169–173): parses the final stdout line as JSON to
     recover the list of created table names. The replacement must preserve this contract.
   - `main()` (lines 182–214): the orchestration — note that `HotdataSession` opens
     BEFORE the container runs and the preview happens AFTER, all under one session.
2. `/Users/elvis/Documents/hotdata/demo/dlt_agent_container_entry.py` — the script that
   runs inside the container. Do not modify its logic. Note the env vars it expects:
   `HOTDATA_API_KEY`, `HOTDATA_API_URL`, `HOTDATA_WORKSPACE_ID`, `HOTDATA_SANDBOX_ID`,
   `DLT_DATAGEN_RUN_ID`. It prints structured logs to stderr and exactly one JSON line
   to stdout (`{"tables": [...]}`).
3. `/Users/elvis/Documents/hotdata/demo/dlt_datagen_module/src/dlt_datagen/` — the dlt
   source package that gets baked into the image. Note its structure but don't change it.
4. `/Users/elvis/Documents/hotdata/demo/pyproject.toml` and `readme.md` — for context on
   how the demo is run today.
5. https://modal.com/docs/guide/sandboxes — the Modal Sandbox API. Pay attention to:
   image construction (`pip_install`, `run_commands`, `add_local_dir`, `add_local_file`,
   `copy=True` semantics — critical: `copy=True` makes files visible to subsequent
   image build steps), `Sandbox.create` signature, secrets via
   `modal.Secret.from_dict({...})`, entrypoint-style command vs `sb.exec()`,
   `sb.wait()` and `sb.stdout.read()`, `sb.terminate()`, and the `App.lookup(...,
   create_if_missing=True)` pattern for spawning sandboxes from outside Modal.

## Goal

Produce `spec.md` describing how to create a NEW file
`/Users/elvis/Documents/hotdata/demo/dlt_agent_sandbox_with_modal.py` that is functionally
equivalent to `dlt_agent_sandbox_with_api.py` but uses Modal Sandboxes instead of Dagger
and installs the Hotdata SDK from GitHub. The original Dagger file stays in place as a
reference — do not delete it.

## Hard constraints

- The host-side `HotdataSession` class behavior must be preserved exactly (sandbox
  creation, header scoping `X-Workspace-Id` / `X-Sandbox-Id` / `X-Session-Id`, preview).
  Whether to copy it into the new file or import it from the old one is your call —
  argue the tradeoff in the spec.
- `dlt_agent_container_entry.py` must run unchanged. It already expects the five env
  vars listed above; the Modal sandbox must inject all five.
- The container's stdout-JSON contract (last line is `{"tables": [...]}`) must still be
  how the driver recovers table names. `parse_tables` should be reusable as-is.
- Hotdata SDK installs from `git+https://github.com/hotdata-dev/sdk-python@<ref>` only.
  No local mount of `../sdk-python`. The spec must specify how `<ref>` is chosen
  (pinned tag, commit SHA, or `main`) and the tradeoff between reproducibility and
  freshness — note that Modal hashes the install command string, not the upstream
  commit, so an unpinned `main` will get a stale cached layer until the command
  string changes.
- The new file must be runnable the same way as the existing one (`HOTDATA_API_KEY`
  set in env, optionally `HOTDATA_API_URL`). It should print the same `=== preview ===`
  output at the end.
- Naming: there are now TWO things called "sandbox" — Modal Sandbox and Hotdata
  Sandbox. Specify a naming convention for local variables (e.g. `modal_sb` vs
  `hotdata_sandbox_id`) so the code stays readable.

## Design decisions the spec must resolve (with reasoning, not just a pick)

1. **Sandbox vs Function.** Modal supports both `modal.Sandbox` (closer to Dagger's
   "run an arbitrary container") and `@app.function` (typed Python call, return value
   instead of stdout parsing). Default recommendation is Sandbox to preserve the
   demo's "agent runs a container" framing, but argue it.
2. **Entrypoint command vs `sb.exec`.** Passing the command positionally to
   `Sandbox.create` runs once and exits. Creating a bare sandbox + `sb.exec(...)`
   keeps it open for multiple commands or interactive debugging. Pick one and explain.
3. **Secrets vs env.** Modal injects via `secrets=[modal.Secret.from_dict({...})]`.
   Decide whether non-secret values (workspace id, run id) go in the same dict or a
   separate one. Simplicity argues one dict; security hygiene argues splitting.
4. **Image layer ordering.** The `dlt_datagen` package and `entry.py` change frequently
   during demo iteration; the pip install layer is stable. Order layers so the
   expensive pip layer is cached across iterations (`add_local_dir` for the package
   should come AFTER `run_commands` for pip).
5. **`copy=True` on `add_local_dir`.** Required when subsequent build steps need to
   see the files; not required when files are only read at runtime. Specify which
   each layer needs and why.
6. **App lifecycle.** `modal.App.lookup("dlt-datagen-demo", create_if_missing=True)`
   vs a fresh app per run. Pick one.
7. **Cleanup.** `sb.terminate()` in a `finally` block, or rely on Modal's timeout?
   The Modal docs flag `detach()` for the local connection — specify the cleanup
   sequence.
8. **Timeout.** Pick a reasonable default and justify (the Dagger version has no
   explicit timeout — Dagger relies on the host process).
9. **Error paths.** What happens if the Modal sandbox exits non-zero? Today Dagger
   raises on non-zero exit. Specify the equivalent check (`sb.returncode`) and the
   error message contract.

## What the spec should contain

- Brief restatement of the goal in 2–3 sentences (so a reviewer skimming knows what
  they're looking at).
- A side-by-side mapping table: Dagger primitive → Modal equivalent.
- The full proposed image build, as a code block, with comments explaining
  `copy=True` choices and layer ordering.
- The full proposed `Pipeline.run_in_container` replacement, as a code block (or its
  equivalent — if you decide to drop the `Pipeline` class entirely, justify and show
  the replacement).
- The exact diff of `main()` — which lines change, which stay.
- Resolution of every numbered design decision above, with reasoning.
- A "what could go wrong" section: at least 3 failure modes (e.g., GitHub rate
  limits during pip install, Modal cold start, SDK ref drift) and how the spec
  handles each.
- A validation checklist the implementation agent will run after writing code:
  `python dlt_agent_sandbox_with_modal.py` succeeds end-to-end with `HOTDATA_API_KEY`
  set, the preview output matches the Dagger version's shape, etc.

## Out of scope (do not include in spec)

- Changes to `dlt_agent_container_entry.py`, `dlt_datagen_module/`, or
  `dlt_agent_sandbox_with_cli.py`.
- Migrating away from Dagger entirely — keep the Dagger file as-is for comparison.
- New features beyond what the Dagger version does today.
- Documentation/readme updates.

## Style

Concrete over abstract. File paths and line numbers, not hand-waves. If you're
recommending something non-obvious (e.g., picking a Modal-Sandbox-specific quirk),
say *why* in one sentence. Spec should be reviewable in ~10 minutes.
