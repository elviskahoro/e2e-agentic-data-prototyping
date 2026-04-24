---
name: clean-specs-plans-status-sync
description: Scan design/ for backlog/todo artifacts, check if work has been merged into the codebase, and auto-rename completed ones to done-. Use for periodic hygiene, after merging branches, or to audit design artifact status.
metadata:
  author: elviskahoro
  version: "1.0"
---

# Design Artifact Status Sync

Scan `design/` for non-done artifacts (backlog-, todo-), determine if the described work has been merged into the codebase, and rename completed artifacts to `done-`.

**Announce at start:** "I'm using the clean-specs-plans-status-sync skill to check design artifacts against the codebase and update their status."

## When to Use

- Periodically (monthly/quarterly) to keep your backlog curated
- After merging feature branches that had corresponding design docs
- Before starting new planning to know what's actually outstanding
- When the user asks to clean up or audit design artifacts

## Detection Strategy

For each non-done artifact, gather evidence from multiple signals and score them:

### Evidence Signals (scored 0-1 each)

1. **Git log keyword match (weight: 0.3)** — Search `git log --oneline --all` for keywords from the artifact's objective slug (e.g., `gtm_cli_architecture` → search for "gtm cli", "gtm_cli", "cli architecture")
2. **File/path existence (weight: 0.3)** — Read the first 50 lines of the artifact to find referenced file paths, directories, or module names. Check if they exist in the repo
3. **Branch merge status (weight: 0.2)** — Look for merged branches containing the objective slug keywords (`git branch --merged main` filtered by slug terms)
4. **Spec deliverables check (weight: 0.2)** — For specs, look for "Deliverables", "Target", "Output", or "## Scope" sections and check if described outputs exist

### Decision Thresholds

- **Score >= 0.6 → Auto-rename to `done-`** (strong evidence, proceed without asking)
- **Score 0.3–0.59 → Ask user** (ambiguous, present evidence summary)
- **Score < 0.3 → Skip** (clearly not done, leave as-is)

## Execution Steps

### 1. Resolve canonical design directory and discover nested repos

```bash
PRIMARY_REPO_ROOT="$(dirname "$(git rev-parse --git-common-dir)")"
DESIGN_DIR="$PRIMARY_REPO_ROOT/design"
```

Discover nested repos that artifacts may target:

```bash
# Find all git repos under gtm/ and tmp/ (not submodules, just nested repos)
NESTED_REPOS=$(find "$PRIMARY_REPO_ROOT/gtm" "$PRIMARY_REPO_ROOT/tmp" -maxdepth 2 -name .git -type d 2>/dev/null | xargs -I{} dirname {})
```

These nested repos (e.g., `gtm/apollo`, `gtm/attio`, `tmp/dlt-official`) have independent git histories. Design artifacts in the parent repo often describe work that lives entirely in a nested repo.

### 2. List non-done artifacts

```bash
ls "$DESIGN_DIR" | grep -E '^(backlog|todo)-' | sort
```

### 3. For each artifact, gather evidence

For each file:

a. **Parse the filename** to extract the objective slug:
   - Format: `status-YYYYMMDDHHMM-objective_details-type-NN.md`
   - Extract `objective_details` (e.g., `gtm_cli_architecture`)

b. **Derive search keywords** from the slug:
   - Split on `_` to get individual words
   - Create search variants: the full slug, space-separated words, hyphenated form

c. **Identify target repos** for evidence gathering:
   - Read the first 80 lines of the artifact
   - Look for explicit repo references like `gtm/apollo`, `gtm/attio`, `tmp/foo` — these indicate the work targets a nested repo
   - If a nested repo is referenced, that repo becomes the **primary search target** for git log, branch, and file existence checks
   - Always also search the parent repo as a fallback
   - If no nested repo is referenced, search only the parent repo (original behavior)

d. **Run evidence checks** against each identified target repo:

```bash
# For each target repo (nested + parent):
TARGET_REPO="<resolved repo path>"

# Signal 1: Git log keyword match
git -C "$TARGET_REPO" log --oneline --all --grep="<keyword>" | head -5

# Signal 2: Read artifact for referenced paths, check existence
head -80 "$DESIGN_DIR/<filename>"
# Then check referenced paths relative to TARGET_REPO with ls/test
# e.g., if artifact says "ci/pipeline.py", check "$TARGET_REPO/ci/pipeline.py"

# Signal 3: Branch merge status
git -C "$TARGET_REPO" branch --merged main 2>/dev/null | grep -i "<keyword>"
# Note: nested repos may use different default branches — fall back to HEAD if main doesn't exist

# Signal 4: Read deliverables section, check outputs exist
grep -A 20 -E "^## (Scope|Deliverables|Target|Output)" "$DESIGN_DIR/<filename>"
# Check deliverable paths relative to TARGET_REPO
```

e. **Calculate score** using weights above. If multiple repos are searched, use the **highest signal score** from any repo for each signal category (best-of across repos).

### 4. Apply decisions

- **Auto-rename (score >= 0.6):**
  ```bash
  cd "$DESIGN_DIR"
  mv "backlog-<rest>" "done-<rest>"
  # or
  mv "todo-<rest>" "done-<rest>"
  ```
  Report: "✓ Renamed `<old>` → `<new>` (score: X.XX, evidence: ...)"

- **Ask user (score 0.3–0.59):**
  Present evidence summary and ask: "Rename to done? [y/n]"

- **Skip (score < 0.3):**
  Report: "⊘ Skipped `<filename>` (score: X.XX — appears incomplete)"

### 5. Stage and report

After all renames:

```bash
cd "$DESIGN_DIR"
git add .
git status --short
```

Present a summary table:

| Artifact | Action | Score | Key Evidence |
|----------|--------|-------|--------------|
| ... | renamed/asked/skipped | X.XX | ... |

Do NOT commit — leave that to the user or a commit skill.

## Grouping Related Artifacts

Artifacts sharing the same objective slug (e.g., `backlog-202603290000-gtm_cli_architecture-spec-01.md` and `backlog-202603290000-gtm_cli_architecture-plan-01.md`) should be scored together, with this lifecycle exception:

- If both a spec and a plan exist for the same slug and there is no explicit implementation evidence yet, treat this as **planning-complete only**:
  - Mark spec artifact(s) `done-`
  - Keep plan artifact(s) non-done (`backlog-`/`todo-`)
- Do **not** auto-mark plans `done-` only because a corresponding spec exists.
- Plans are marked `done-` only when implementation evidence clears the normal threshold.

## Edge Cases

- If an artifact has `## Status: done` or `## Status: complete` in its body but the filename still says `backlog-`/`todo-`, auto-rename regardless of score
- If a plan is `done-` but there is no implementation evidence and the user policy says "plan-created-from-spec only", rename the plan back to `backlog-` (or prior non-done status if known).
- Prompt files (`-prompt.md`) are one-shot — if any git log hit references the prompt topic, score the git signal at 1.0
- Tool files (`-todo.py`) follow the same rules as plans/specs
- Nested repos may use a default branch other than `main` (e.g., `master`). Use `git -C "$REPO" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null` or fall back to `HEAD` for branch merge checks
- If an artifact references a nested repo path that doesn't exist (e.g., `gtm/foo`), skip that repo silently and score based on remaining evidence
- Artifacts may reference multiple nested repos (e.g., "shared Dagger module across gtm/attio and gtm/apollo"). Search all referenced repos and use best-of scoring
