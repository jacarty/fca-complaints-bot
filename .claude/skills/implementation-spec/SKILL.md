---
name: implementation-spec
description: >
  Produce a phased implementation spec (build guide) from requirements and research.
  Use when the user has a researched problem and needs a concrete plan for building it —
  with phases, acceptance criteria, and file-level change lists. Triggers include
  "write a spec", "create a build plan", "implementation plan", "how should I build this",
  "write a PRD", "create a build guide", "phase this work", "break this into steps",
  "spec this out", or any situation where requirements and research exist and the next
  step is a structured plan for implementation. Also use when a tracked issue has both
  requirements and a research brief and needs to move to the build stage.
---

# Implementation Spec — From Research to Build Plan

## What This Skill Does

This skill takes requirements and research as inputs and produces an **implementation
spec** — a single document combining scoping decisions, technical choices, acceptance
criteria, and a phased build plan. The spec is the contract between the planning stage
and the build stage.

It is designed to be consumed by a developer, by Claude Code, and by review agents
(e.g. a phase-acceptance agent), so it must be precise and actionable.

## When to Use This Skill

Use when:

- Requirements exist (from the requirements-elicitation skill or an existing brief)
- Research has been done (from the research-brief skill or existing investigation)
- The user wants a structured plan for building, not just a list of things to do
- A tracked issue has been researched and needs to move to implementation

Do NOT use when:

- Requirements haven't been gathered yet (use requirements-elicitation first)
- The technical landscape hasn't been investigated (use research-brief first)
- The work is small enough that a spec would be overhead (just do it)
- The user wants to explore options (use tree-of-thought, graph-of-thought, or
  trade-off-analysis)

---

## Process

### Step 1 — Gather Inputs

Collect the inputs that ground the spec:

- **Requirements document** — from the requirements-elicitation skill, an existing PRD,
  or the issue description itself.
- **Research brief** — from the research-brief skill or existing research comments on
  a tracked issue. If no research brief exists, stop and say so — a spec without
  research is guesswork.
- **Codebase context** — if a target repository exists, read key files using whatever
  tools are available. At minimum check for a `CLAUDE.md` or similar project context
  file, and any files referenced in the research brief's recommended approach. This
  grounds the spec in the actual codebase rather than abstract assumptions.
- **Issue context** — if the work is tracked in a project tracker, read the issue and
  any parent issue for broader project context.

### Step 2 — Make Scoping Decisions

Based on the requirements and research, decide:

- **What's in scope (Must have)** — non-negotiable for this piece of work
- **What's deferred (Should have)** — valuable but can wait if the work runs long
- **What's out of scope (Won't have)** — explicitly excluded to prevent scope creep

Then make the key technical decisions:

- Which of the research brief's recommended approaches to adopt
- What tools, libraries, or patterns to use
- What trade-offs are being accepted (reference the research brief findings)

If a scoping decision is genuinely difficult, note it as a candidate for the
trade-off-analysis skill rather than making an arbitrary call.

### Step 3 — Phase the Work

Break the implementation into phases. Each phase should:

- **Deliver a testable increment** — something that can be verified as working
- **Be small enough to review as a single PR** — if a phase would be a 1000-line diff,
  it's too big
- **Be large enough to move the feature forward meaningfully** — if a phase is just
  "create an empty file," it's too small
- **Include specific verification steps** — commands to run, behaviour to observe, or
  tests to pass

Most features are 2-4 phases. If you're writing more than 6, the scope is probably
too large for a single spec — suggest decomposing.

### Step 4 — Write Acceptance Criteria

Write testable conditions for done. These are what a reviewer (human or agent)
validates against. Use either:

- **Given / When / Then** format for behaviour-driven criteria
- **Checkbox statements** for simpler verification ("API returns 200 for valid input")

Every acceptance criterion must trace back to a requirement. Don't invent criteria
the requirements didn't ask for.

### Step 5 — Delivery

Produce the full spec as markdown using the output template below. This is always
the primary output.

Then ask the user if they would like the output additionally delivered via any
connected tools (e.g. posted as a comment on a tracked issue, saved to a shared
document, etc.). Use whatever tools are available in the current environment.
If no additional tools are connected, skip this step.

When posting to an issue tracker, check for existing specs before posting to avoid
duplicates.

---

## Output Template

```markdown
# Implementation Spec: [Feature or Issue Title]

**Source:** [Issue reference, requirements doc, or brief summary]
**Based on:** [Research brief date/reference]
**Date:** [current date]

## Problem Statement
What is being built and why. Ground this in the requirements and project context.
One to two paragraphs maximum.

## Requirements

### Must Have
Numbered list of non-negotiable requirements. Each must be specific and testable.

### Should Have
Requirements that add significant value but aren't blocking.

### Won't Have (this phase)
Explicitly out of scope. This prevents scope creep during implementation and sets
expectations for what this work does NOT deliver.

## Technical Decisions
Key choices made during scoping, with rationale. Reference research brief findings.
Format as a short list — decision: rationale.

## Acceptance Criteria
Testable conditions for done. Write as "Given / When / Then" or checkbox statements.

## Build Plan

### Phase 1: [descriptive name]
**Objective:** What this phase delivers (one sentence).

**Changes:**
- File-level list of what to create or modify, with a brief description of each change.

**Verification:** How to confirm this phase is complete — specific commands to run,
behaviour to observe, or tests to pass.

### Phase 2: [descriptive name]
...

(Continue for as many phases as needed. Most features are 2-4 phases.)

## Dependencies
What must exist before this work can start. What other issues or systems this blocks.

## Open Questions
Anything unresolved that the developer should decide before or during implementation.
Carried forward from the research brief plus any new questions raised during scoping.
```

## What Comes Next

After a spec is produced, the natural next steps are:

- **Build** — a developer (or Claude Code) works through the phases in order.
- **Phase acceptance** (phase-acceptance agent, if available) — after each phase,
  validate that the acceptance criteria are met before moving to the next phase.
- **Trade-off analysis** (trade-off-analysis skill) — if an open question from the
  spec needs a structured decision before building can continue.

---

## Interaction Style

- Precise and actionable. Every statement in the spec should tell the developer
  something they need to know. Remove anything that doesn't.
- File-level specificity in the build plan. "Update the API handler" is too vague.
  "Add a `/health` endpoint to `src/api/routes.py`" is right.
- Trace every requirement and decision back to its source (requirements doc or
  research brief). Don't invent requirements the inputs didn't contain.
- If the inputs are insufficient to write a good spec, say what's missing rather
  than guessing.
- British English spelling and punctuation.

## Handling Edge Cases

- **No research brief exists**: Stop and recommend running the research-brief skill
  first. A spec without research is guesswork.
- **Requirements are vague**: Do your best but flag the vague areas explicitly in
  the open questions section. Suggest running requirements-elicitation if the gaps
  are severe enough to undermine the spec.
- **Scope is too large for one spec**: Suggest decomposing into multiple specs, each
  covering a coherent slice of the work. Produce the first spec and outline what
  the others would cover.
- **Codebase doesn't exist yet (greenfield)**: Skip the codebase context step. The
  build plan's Phase 1 should cover project scaffolding.
- **User wants to change scope after seeing the spec**: Revise the spec. Don't treat
  the first draft as final — specs improve through review.
