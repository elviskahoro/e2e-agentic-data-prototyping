---
name: executing-plans
description: Use when you have a written implementation plan to execute in a separate session with review checkpoints
hooks:
  PreToolUse:
    - matcher: "Write|Edit|Bash"
      hooks:
        - type: command
          command: "R=$(dirname \"$(git rev-parse --git-common-dir 2>/dev/null)\" 2>/dev/null); f=$(ls \"$R\"/design/{todo,backlog}-*-plan-*.md 2>/dev/null | head -1); [ -f \"$f\" ] && head -30 \"$f\"; true"
  Stop:
    - hooks:
        - type: command
          command: "R=$(dirname \"$(git rev-parse --git-common-dir 2>/dev/null)\" 2>/dev/null); f=$(ls \"$R\"/design/{todo,backlog}-*-plan-*.md 2>/dev/null | head -1); [ -f \"$f\" ] && { DONE=$(grep -c \"^- \\[x\\]\" \"$f\" || echo 0); TODO=$(grep -c \"^- \\[ \\]\" \"$f\" || echo 0); echo \"[executing-plans] $DONE steps done, $TODO remaining.\"; [ \"$TODO\" -gt 0 ] && echo \"[executing-plans] Open tasks remain — verify completion before stopping.\"; }; true"
---

# Executing Plans

## Overview

Load plan, review critically, execute all tasks, report when complete.

**Announce at start:** "I'm using the executing-plans skill to implement this plan."

**Note:** If subagents are available, prefer `subagent-driven-development` for faster iteration. Use this skill when you explicitly want inline or batched execution in one session.

## Workflow Conventions

- User's explicit instructions override defaults in this skill.
- Track task state in Todo tracking throughout execution.
- If debugging is required mid-task, switch to `systematic-debugging` before proposing fixes.
- Use direct skill names in handoffs (no `superpowers:` prefixes).

## Attention Management

This skill defines hooks that mechanically prevent goal drift during long execution sessions:

- **PreToolUse** (Write|Edit|Bash) resurfaces the first 30 lines of the active plan before each tool call, keeping the goal and current task in the attention window
- **Stop** counts completed vs remaining checkboxes and warns if tasks are still open

These hooks find plan files in the primary checkout's `design/` directory automatically, including from worktrees.

## The Process

### Step 0: Session Recovery (Reboot Test)

If resuming plan execution after `/clear`, a new session, or context compression, answer these five questions before continuing:

| Question | Source |
|----------|--------|
| Where am I? | Current task checkbox in plan file |
| Where am I going? | Remaining unchecked tasks |
| What's the goal? | Plan header `**Goal:**` line |
| What have I learned? | `git log` + any findings from prior work |
| What have I done? | `git diff main...HEAD --stat` |

If you can answer all five, proceed from the current task. If not, re-read the plan file and git history until you can.

### Step 1: Load and Review Plan
1. Read plan file
2. Review critically - identify any questions or concerns about the plan
3. If concerns: Raise them with your human partner before starting
4. If no concerns: Create TodoWrite and proceed

### Step 2: Execute Tasks

For each task:
1. Mark as in_progress
2. Follow each step exactly (plan has bite-sized steps)
3. Run verifications as specified
4. Mark as completed

### Step 3: Complete Development

After all tasks complete and verified:
- Announce: "I'm using the finishing-a-development-branch skill to complete this work."
- **REQUIRED SUB-SKILL:** Use finishing-a-development-branch
- Follow that skill to verify tests, present options, execute choice

## When to Stop and Ask for Help

**STOP executing immediately when:**
- Hit a blocker (missing dependency, test fails, instruction unclear)
- Plan has critical gaps preventing starting
- You don't understand an instruction
- Verification fails repeatedly

**Ask for clarification rather than guessing.**

## When to Revisit Earlier Steps

**Return to Review (Step 1) when:**
- Partner updates the plan based on your feedback
- Fundamental approach needs rethinking

**Don't force through blockers** - stop and ask.

## Remember
- Review plan critically first
- Follow plan steps exactly
- Don't skip verifications
- Reference skills when plan says to
- Stop when blocked, don't guess
- Never start implementation on main/master branch without explicit user consent

## Integration

**Required workflow skills:**
- **using-git-worktrees** - REQUIRED: Set up isolated workspace before starting
- **plan-writing** - Creates the plan this skill executes
- **finishing-a-development-branch** - Complete development after all tasks
