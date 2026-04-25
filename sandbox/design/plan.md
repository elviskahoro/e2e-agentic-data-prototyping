# Plan: implement `sandbox/design/spec.md`

Port `local/dlt_agent_sandbox_with_api.py` (Dagger) → Modal Sandboxes, self-contained under `sandbox/`. This plan turns the spec into an ordered, verifiable build. Each step produces something runnable or checkable before the next begins — don't batch.

## Known spec drift (resolve before copying)

Two places where the spec's text doesn't match the current tree. Resolve both the first time they come up; don't copy the spec's stale strings into code.

1. **Datagen module contents.** Spec §"Final layout" lists `src/dlt_datagen/{__init__.py, load.py, main.py}`. Current tree is `__init__.py`, `main.py`, `source.py`, `pipeline.py` (there is an in-progress `git mv load.py → source.py` visible in `git status`). **Decision:** copy the *current* tree verbatim — all four files. Don't try to reconstruct the spec's list.
2. **`HotdataSession` line range.** Spec §"Host driver" says "copied verbatim … (lines 26–101)." In the current file the class spans 26 through the line before `class Pipeline:` at 105 (so 26–104 inclusive). **Decision:** copy from line 26 up to but not including `class Pipeline`. Don't trust the `101`.

## Step 1 — scaffold directory, copy static assets

Goal: `sandbox/` has every file it will ship with, except the new host driver.

- Create `sandbox/dlt_datagen_module/` and copy **only** `pyproject.toml` and `src/dlt_datagen/` from `local/dlt_datagen_module/`.
  - Do **not** copy `dagger.json`, `uv.lock`, `LICENSE`, `.gitattributes`, `.gitignore` (spec §"Trim on copy").
  - Do **not** copy `__pycache__` if present.
- Overwrite the copied `sandbox/dlt_datagen_module/pyproject.toml` with the exact 10-line version in spec §"Trim on copy" (drops `dagger-io` dep and the `[tool.uv.sources]` block).
- Copy `local/dlt_agent_container_entry.py` → `sandbox/dlt_agent_container_entry.py` byte-for-byte. Verify with `diff -q` after.
- Write `sandbox/pyproject.toml` exactly as in spec §"Host `sandbox/pyproject.toml`".
- **Stop and verify:** `grep -r dagger sandbox/` returns zero hits; `grep -r '\.\./sdk-python' sandbox/` returns zero hits. If either hits, fix before moving on.

## Step 2 — write the host driver

Goal: `sandbox/dlt_agent_sandbox_with_modal.py` exists and imports cleanly (no Modal calls yet).

- Start from the code block in spec §"Host driver (proposed …)" verbatim.
- Paste `HotdataSession` from `local/dlt_agent_sandbox_with_api.py` lines 26 through (but not including) `class Pipeline` — see drift note above. Do **not** modify it.
- Add the `HOTDATA_SDK_REF = "main"` module-level constant called out in spec §"What could go wrong" #3 and use it to build the install command string in the image definition (single source of truth for the SDK ref).
- `parse_tables`: module-level, exactly as in spec.
- `run_in_modal_sandbox`: exactly as in spec, including the `try/finally` terminate guard and the `sys.stderr.write(stderr)` mirror.
- Add the `print("→ building/spinning up Modal sandbox...", file=sys.stderr)` line immediately before `Sandbox.create` (spec §"What could go wrong" #2).
- Image definition: build with `.add_local_dir(..., ignore=["__pycache__"])` (spec §"What could go wrong" #5). Omit the uv cache volume (spec §"uv cache volume note" — recommendation is omit).
- `main()`: synchronous, mirrors the Dagger `main()` per the diff table in spec §"Diff of `main()` …".
- Naming: `modal_sb`, `hotdata_sandbox_id`, `image` (spec §"Naming convention").
- **Stop and verify:** `uv run python -c "import dlt_agent_sandbox_with_modal"` from `sandbox/` imports without error after `uv sync`. Don't run `main()` yet.

## Step 3 — first end-to-end run

Goal: one cold-path invocation produces the preview block.

Prereq: `HOTDATA_API_KEY` must already be in the shell — do **not** read from any `*.secrets.toml` file, do **not** echo the value.

- From `sandbox/`: `uv sync` (pulls `hotdata` from GitHub — confirms the git URL in `sandbox/pyproject.toml` resolves).
- From `sandbox/`: `uv run python dlt_agent_sandbox_with_modal.py`.
- First run is slow: pip layer builds (dlt + duckdb + pyarrow + SDK clone). Expect multi-minute pause after the "building/spinning up" stderr line. 600s timeout covers it.
- Confirm each stderr line from spec §"Validation checklist":
  - `→ run <id>`, `→ using workspace …`, `→ created sandbox …`
  - dlt load logs from inside the container
  - `→ container uploaded tables: ['purchases', 'customers']`
- Confirm stdout has the `=== preview ===` block with header + up-to-10 rows per table, tab-separated.
- If the run fails: read the `RuntimeError` message (includes returncode + last stdout line) and the mirrored container stderr; diagnose root cause. Do **not** add retries or fallbacks — fix the underlying issue.

## Step 4 — warm-path run (cache verification)

Goal: second run reuses the Modal app and image layers.

- Re-run immediately: `uv run python dlt_agent_sandbox_with_modal.py`.
- Wall-clock should drop substantially (no pip install, no image rebuild — container spawn + dlt work only).
- If it rebuilds: the pip layer's command string changed between runs (likely `HOTDATA_SDK_REF` interpolation differs, or `.env(...)` ordering shifted). Normalize and re-run.

## Step 5 — failure-path smoke test

Goal: prove the error path surfaces clearly.

- Temporarily edit `sandbox/dlt_agent_container_entry.py` to `raise RuntimeError("boom")` at the top of the `if __name__ == "__main__":` block.
- Run once. Confirm:
  - Host raises `RuntimeError` with non-zero return code in the message.
  - Container traceback appears on host stderr (via the `sys.stderr.write(stderr)` mirror).
- Revert the edit. `git diff sandbox/dlt_agent_container_entry.py` must be empty after revert.

## Step 6 — write the readme

Goal: `sandbox/readme.md` is a one-pager per spec §"Final layout".

Must cover, concretely (not hand-waved):
- Prereqs: `HOTDATA_API_KEY` in env, `modal` CLI auth (`modal token new` one-time), `uv`.
- Run: `cd sandbox && uv sync && uv run python dlt_agent_sandbox_with_modal.py`.
- Expected first-run latency + what the user sees during the pause.
- How to bump the SDK: change `HOTDATA_SDK_REF` in the driver **and** the ref in `sandbox/pyproject.toml` together, then re-run `uv sync`. Call out that they must match (spec §10 + §"What could go wrong" #3), and that the host install is at `uv sync` time so it does not pick up the Python constant automatically.
- Pointer to `design/spec.md` for the port rationale.

## Step 7 — final validation

Run the full spec §"Validation checklist" top to bottom from `sandbox/`:

- [ ] `tree -L 3 .` matches spec §"Final layout" (modulo the drift note above: `source.py`/`pipeline.py` present instead of `load.py`).
- [ ] `grep -r dagger sandbox/` from repo root: zero hits.
- [ ] `grep -r '\.\./sdk-python' sandbox/`: zero hits.
- [ ] `uv sync` succeeds from `sandbox/`.
- [ ] End-to-end run produces the preview block (Step 3).
- [ ] Warm re-run is substantially faster (Step 4).
- [ ] Force-fail path surfaces traceback (Step 5).
- [ ] `git diff local/` is empty — original Dagger driver untouched.

## Out of scope (explicit non-goals)

Do **not**, without a follow-up ask:
- Modify `local/dlt_agent_sandbox_with_api.py` or `local/dlt_agent_container_entry.py`.
- Pre-bake an image with the SDK (spec §"What could go wrong" #1 defers this).
- Add retries, fallbacks, or config flags the spec doesn't call for.
- Switch `add_local_*` calls to `copy=True` (spec §"Why no `copy=True`").
- Mount the uv cache volume (spec §"uv cache volume note").
- Rename the Modal app or the Hotdata sandbox naming convention.
