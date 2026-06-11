# Development Process

> Process rules for feature work. Read this alongside `CLAUDE.md` (architecture context) and the current build guide before starting any work.

## Branch and PR cadence

- One branch per feature or build-guide sub-section.
- One PR per branch, merged to `main` (or `dev`), branch deleted on merge.
- **Docs-only changes** (status log, tech-debt, process doc, ADRs) commit directly to `main`/`dev`. Don't open PRs for doc-only changes.

<!-- Customise branch naming to match your project:
- feature/v1.1a-<slug> — for build-guide sub-sections
- fix/<slug> — bug fixes
- docs/<slug> — documentation
-->

## Issue tracking

<!-- Replace with your tool: Linear, GitHub Issues, Jira, etc. -->

Every feature PR corresponds to one or more tracked issues. Closing the loop is part of the PR lifecycle, not an afterthought:

1. **When opening the PR** — reference the issue(s) in the PR body (`Closes #XX` or tool-specific syntax). This gives reviewers scope context and enables auto-linking.
2. **When the PR merges** — move the corresponding issue(s) to **Done** in the same working session. Do not defer this; stale "In Progress" issues make the board lie.
3. **If the PR only partially addresses an issue** — leave the issue open, add a comment describing what shipped vs what remains, and link the PR.

If no issue exists for the work, create one before opening the PR so the board reflects reality. The only exception is the trivial-change fast path (below).

## Agent cadence

All agents are defined in `.claude/agents/`. This is the policy for **when to invoke each one automatically** vs when to use it ad-hoc.

### Pre-commit (always, except trivial)

| Agent | Trigger | Why |
|---|---|---|
| **linter** | After the last edit in a session, before `git add` | Haiku-speed cleanup of formatting, unused imports, style violations. Cheap enough to run unconditionally. |

**Self-check before every commit** (not agent-gated — just do it):

1. `git status` — verify only intended files are staged. No stray config files, lockfile churn, or editor artefacts.
2. `git diff --cached --name-only` — scan the list. If anything looks unrelated, unstage it.
3. Run lint and tests locally. Do not push code that fails lint or tests.

These three steps prevent the most common friction: unrelated files in commits, unused imports breaking CI, and bugs caught late by reviewers that should have been caught locally.

### Pre-PR (conditional gates)

**Critical**: these agents run **before** the PR is created, not in parallel with it. Findings fold into the in-progress branch (new commits, amend, or rebase) so the PR opens in its final state. Reviewers should see one coherent diff, not "feature commits then fix-up commits".

**Fold timing** — two legitimate shapes:

- **One-atomic-fold** — keep the working tree uncommitted while the gates run, then commit feature + gate findings as a single commit. Cleanest review experience. Preferred for larger PRs.
- **Two-commit-fold** — commit features, run gates, add findings as a second commit. Acceptable for small fixes where the extra commit is a minor concern.

**Rule of thumb:**
- **PR ≥ 300 net lines** → one-atomic-fold
- **PR < 300 lines AND a single logical fix** → two-commit-fold is fine
- **If a background agent is actively writing files while gates run** → always wait for it to finish before committing

Either way, all findings land on the branch before the PR opens.

| Agent | Trigger — runs if ANY of these are true |
|---|---|
| **code-reviewer** | Net diff ≥ 300 lines • touches core business logic • touches IAM/auth/permissions • introduces a new function/handler/endpoint • explicitly requested |
| **devops-reviewer** | Touches IaC files • touches CI/CD workflows • changes compute config (runtime, memory, timeout, entry point) |
| **test-generator** | A new exported function lands without tests • a new handler/endpoint lands without tests • handler logic grows > 100 net lines in a PR • explicitly requested |
| **doc-generator** | Any non-trivial PR (i.e. doesn't qualify for the trivial-change fast path). Runs after code-reviewer findings are folded in. Writes doc updates directly to the branch — CLAUDE.md drift, README updates, inline docs for new exports, ADR drafts for design decisions. |
| **phase-acceptance** | PR completes a build-guide phase (not every PR within a phase — just the final PR that closes out a phase). Reads the relevant PRD section + build guide verification checklist and checks the code against every requirement. |

<!-- Add project-specific triggers here. Examples:
| **code-reviewer** | ...touches `backend/functions/chat/**` or `helpers.mjs` |
| **devops-reviewer** | ...touches `terraform/**` |
-->

### Phase boundaries (periodic, not per-PR)

| Agent | Cadence |
|---|---|
| **codebase-review** | End of each build-guide phase. Refreshes tech-debt tracking. |
| **doc-generator** | Paired with the phase-end codebase-review. Drives CLAUDE.md refresh, architecture diagram updates, and ADRs for decisions that accumulated during the phase. |

### Ad-hoc (no automatic trigger)

- **code-optimiser** — when performance concerns arise
- **refactorer** — when explicit refactor work is scoped (e.g. dependency upgrade, pattern migration)
- **Explore** — general codebase searching that exceeds what `Grep` / `Glob` can handle
- **general-purpose** — fallback for open-ended research

## Exception: trivial-change fast path

If a PR is **all** of the following:
- ≤ 50 net lines added
- ≤ 2 files touched
- No changes to IaC, CI/CD workflows, or core business logic
- No new exports, handlers, routes, or endpoints

...then only the **linter** runs. Straight to commit, no second-opinion agents. Prevents overhead on one-line fixes, typo corrections, and small docs tweaks.

## Parallel execution

When multiple pre-PR gates apply, **spawn the agents in parallel**, not sequentially. `code-reviewer` + `devops-reviewer` + `test-generator` typically finish much faster in parallel. Same for phase-boundary agents — `codebase-review` + `doc-generator` can run concurrently.

## When an agent flags something

1. **Read the full report** before acting — don't skim.
2. **If blocking (security, correctness, isolation)**: fix before pushing. These are the cases that earned the gate in the first place.
3. **If non-blocking (style, suggestion, nit)**: address inline if cheap; otherwise add to tech-debt tracking and note it in the PR body under "Known gaps".
4. **If the agent is wrong**: say so in a code comment or the PR body, and keep moving. Agents are fallible; they're a second pair of eyes, not a binding authority.
5. **If phase-acceptance flags a missing requirement**: this is always blocking. Fix the gap, re-run the agent, then proceed.

## What gets written down

After each feature PR merges: (1) close the corresponding issue(s), and (2) update the status log (if you maintain one) via a docs-only commit to main.

After each **phase** closes, the codebase-review + doc-generator pair produces a refresh of tech-debt tracking and a rewrite of `CLAUDE.md` to reflect the final post-phase state. Both commit directly to main.

Significant technical decisions taken during implementation (that aren't already in the PRD) should be written up as ADRs under `docs/decisions/NNNN-short-title.md`.

## Updating this document

This file is a living process doc. When a rule doesn't fit the work — when an agent is giving consistent false positives, or a new class of bug shows up that the rules didn't catch — update the rule and commit the change with a short justification in the commit message. Don't accumulate stale rules.
