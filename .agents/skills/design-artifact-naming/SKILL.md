---
name: design-artifact-naming
description: Use when creating specifications, implementation plans, or tools for a feature - enforces consistent naming across related artifacts
---

# Creating Specs, Plans, and Tools

## Overview

Design artifacts live directly under `design/` in the primary checkout root (not inside a worktree-local `design/` directory) with a status-first naming scheme and explicit artifact suffixes. Plans/specs also carry a numeric index so follow-up scope can be tracked without ambiguity.

## Canonical Location (Mandatory)

- Resolve the canonical design directory before writing artifacts:
  - `PRIMARY_REPO_ROOT="$(dirname "$(git rev-parse --git-common-dir)")"`
  - `DESIGN_DIR="$PRIMARY_REPO_ROOT/design"`
- Create specs/plans/prompts/todo tools only in `$DESIGN_DIR`.
- Never write artifacts to `worktrees/*/design/` or `.worktrees/*/design/`.

## Naming Convention

**Format (plans/specs):** `status-YYYYMMDDHHMM-feature_name-<artifact>-NN`

Where:
- `<artifact>` is `spec` or `plan`
- `NN` is a two-digit index (`01`, `02`, `03`, ...)

**Format (other artifacts):**
- `status-YYYYMMDDHHMM-feature_name-prompt.md`
- `status-YYYYMMDDHHMM-feature_name-todo.py`

**Status values for specs/plans in this project:** `backlog`, `todo`

**Default status policy (mandatory):**
- Default to `backlog` when creating new artifacts.
- Use `todo` only when the user explicitly signals immediate execution readiness.
- If ambiguous, choose `backlog`.

**Artifact suffix values:**
- `spec` for specification docs
- `plan` for implementation plans
- `prompt` for prompt docs
- `todo` for executable helper tools/scripts

**Example for "embed superpowers" created on 2026-03-21 (status: backlog):**
- `design/backlog-202603210000-embed_superpowers-spec-01.md`
- `design/backlog-202603210000-embed_superpowers-plan-01.md`
- `design/backlog-202603210000-embed_superpowers-prompt.md`
- `design/backlog-202603210000-embed_superpowers-todo.py`

**Key rules:**
- Status comes first: `backlog` or `todo` (status prefix only, do NOT repeat in feature name)
- Use hyphens between semantic entities; use underscores within the topic/details segment
- Use the creation date (today's date when you create the artifact)
- Use the same feature name across all related artifacts
- Use explicit artifact suffix: `-spec-<NN>`, `-plan-<NN>`, `-prompt`, or `-todo`
- Plans/specs start at `-01`; follow-ups increment (`-02`, `-03`, ...)
- Do not create `-02+` for routine edits; edit the current file when still in scope
- Use `-02+` only when new work is intentionally out of scope for the current artifact and deferred for later
- No extra trailing suffixes like `-resources`, `-design`, `-implementation`, or `-refactor`
- Store all artifacts in the primary checkout's `design/` (no per-type subfolders)
- Do not use `in_progress` or `waiting` for specs/plans
- **IMPORTANT:** Never repeat status keywords in the feature name. Wrong: `backlog-202603291740-pyright_error_backlog-spec-01.md`. Right: `backlog-202603291740-pyright_error-spec-01.md`

## When to Create Each Type

| Artifact | Purpose | Content |
|----------|---------|---------|
| **Spec** | Define requirements & design | Problem statement, requirements, design decisions, acceptance criteria |
| **Plan** | Outline implementation steps | Step-by-step tasks, dependencies, estimated effort, testing approach |
| **Prompt** | Capture reusable prompt workflows | Prompt templates, input contracts, evaluation notes |
| **Todo Tool** | Automation or helpers | Executable code that supports the work (scripts, utilities, generators) |

A single feature may have all three, or just the ones needed.

## Before You Start

1. Check if a spec/plan/tool already exists with the same feature name
2. Resolve `PRIMARY_REPO_ROOT` and `DESIGN_DIR`; confirm the target path is not under `worktrees/*` or `.worktrees/*`
3. If renaming an existing artifact, normalize to indexed naming (e.g., `20260321-embed-superpowers-resources.md` → `backlog-202603210000-embed_superpowers-spec-01.md`)
4. Decide edit vs follow-up:
   - In-scope clarification or correction: edit existing `-NN` artifact
   - Out-of-scope deferred work: create next indexed artifact (`-NN+1`)
5. Use today's date unless updating an existing artifact (then keep its original date)

## Quick Reference

**Creating a spec:**
```
File: design/status-YYYYMMDDHHMM-feature_name-spec-01.md
Contains: Requirements, design, decisions
```

**Creating a plan:**
```
File: design/status-YYYYMMDDHHMM-feature_name-plan-01.md
Contains: Implementation steps, timeline, success criteria
```

**Creating a prompt:**
```
File: design/status-YYYYMMDDHHMM-feature_name-prompt.md
Contains: Prompt templates, usage, expected outputs
```

**Creating a todo tool:**
```
File: design/status-YYYYMMDDHHMM-feature_name-todo.py
Contains: Executable code that automates or supports the work
```

**Status guide (specs/plans):**
- `backlog` — Planned but not started
- `todo` — Ready for execution

## Example

**Scenario:** You're adding a new feature to categorize Linear issues.

Today is 2026-03-23. Execution has not been explicitly authorized yet, so status defaults to `backlog`:

1. **Spec:** `design/backlog-202603230000-linear_issue_categorization-spec-01.md`
   - Requirements for categorization rules
   - Design for matching algorithm
   - Data format decisions

2. **Plan:** `design/backlog-202603230000-linear_issue_categorization-plan-01.md`
   - Step 1: Design matching algorithm
   - Step 2: Build categorization service
   - Step 3: Add API endpoint
   - Step 4: Test with sample data

3. **Prompt doc:** `design/backlog-202603230000-linear_issue_categorization-prompt.md`
   - Prompt templates for classification and fallback behavior
   - Expected prompt inputs/outputs

4. **Todo tool:** `design/backlog-202603230000-linear_issue_categorization-todo.py`
   - Helper script to batch-categorize existing issues
   - Or: Training script for the ML model
   - Or: Validation script to check rules

**Status policy:** Keep specs/plans as `backlog-*` or `todo-*` only.

If the user later says "ready to execute now," rename the related artifacts from `backlog-*` to `todo-*` while keeping the same timestamp/topic/index stem.

**If new out-of-scope work appears later:**
- Keep `...-plan-01.md` and `...-spec-01.md` as the original scope record
- Create follow-ups: `...-plan-02.md` and/or `...-spec-02.md`
- Continue incrementing for additional deferred scopes

All related files share the same status/date/topic stem and differ only by artifact suffix, making them instantly recognizable and easy to track together.
