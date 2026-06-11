---
name: requirements-elicitation
description: >
  Structured interview to turn a vague brief or idea into concrete, actionable
  requirements. Use when the user has a project concept, feature idea, or problem
  statement that needs sharpening before design or implementation begins. Triggers
  include "I want to build", "I have an idea for", "help me scope this", "what should
  the requirements be", "write requirements for", "help me define this project",
  "what do I need to think about before building", "scope this out", or any situation
  where the user has intent but not yet a clear specification. Also use when the user
  shares a brief or PRD that feels incomplete and wants to identify gaps.
---

# Requirements Elicitation — From Vague Brief to Clear Spec

## What This Skill Does

This skill runs a **structured elicitation process** that transforms a rough idea or
vague brief into a concrete set of requirements. It surfaces assumptions, identifies
gaps, forces prioritisation, and produces a document the user can hand to a team or
use as a project foundation.

It is not a template filler — it's an interactive process that asks targeted questions
to draw out what the user knows (and doesn't know they know) about what they want to build.

## When to Use This Skill

Use when:

- The user has a project idea but hasn't formalised requirements
- A brief or PRD exists but feels incomplete or hand-wavy
- The user is about to start building and wants to think it through first
- Someone says "I want to build X" without specifying what X actually does

Do NOT use when:

- Requirements already exist and the user wants to implement them
- The user wants architecture or design decisions (use tree-of-thought or graph-of-thought)
- The task is a small, well-defined piece of work (just do it)

---

## Process

### Phase 1 — Understand the Intent

Start by understanding what the user is trying to achieve, not what they want to build.
These are different questions.

1. **What problem does this solve?** — Who has the problem? How do they deal with it today?
   What's the cost of the status quo?
2. **What does success look like?** — If this project works perfectly, what's different in
   6 months? Be specific.
3. **Who are the users?** — Primary users, secondary users, administrators, stakeholders
   who don't use it but care about it.

Present a brief summary of intent and confirm with the user before moving on.

### Phase 2 — Functional Requirements

Walk through the core capabilities. For each, capture:

- **What it does** — plain language description of the behaviour
- **Input** — what triggers it or what data does it need
- **Output** — what the user sees or what changes in the system
- **Happy path** — the expected flow when everything works
- **Edge cases** — what happens when inputs are unexpected, missing, or malformed

Group related capabilities into logical clusters. Don't aim for exhaustive coverage
in the first pass — aim for the 5-8 capabilities that define the core product.

### Phase 3 — Non-Functional Requirements

Probe the constraints that won't appear in a feature list but shape every decision:

- **Performance** — response time expectations, throughput, concurrent users
- **Scale** — how much data, how many users, growth expectations
- **Security** — authentication, authorisation, data sensitivity, compliance
- **Reliability** — uptime expectations, disaster recovery, data loss tolerance
- **Cost** — budget constraints, ongoing operational cost tolerance
- **Maintainability** — who maintains it, how often does it change, team size/skills
- **Integration** — what existing systems does it need to talk to

Don't ask about all of these robotically. Pick the 3-4 that matter most based on the
project context and probe those. Mention the others briefly and let the user flag any
that need deeper discussion.

### Phase 4 — Prioritise

Not everything is equally important. Force a prioritisation using MoSCoW:

- **Must have** — the project fails without these
- **Should have** — significant value, expected by users, but workarounds exist
- **Could have** — nice to have if time/budget allows
- **Won't have (this version)** — explicitly out of scope for now

Present the requirements grouped by priority. This is where the user often realises
they've been treating everything as a must-have, and the conversation gets productive.

### Phase 5 — Gaps and Risks

Surface what's still unknown or risky:

- **Open questions** — things that need answers before building can start
- **Assumptions** — things treated as true but not yet validated
- **Dependencies** — external systems, teams, or decisions this project depends on
- **Risks** — what could derail this, and how likely is each

### Phase 6 — Delivery

Produce the full requirements document as markdown using the output template below.
This is always the primary output.

Then ask the user if they would like the output additionally delivered via any
connected tools (e.g. posted as an issue in a project tracker, saved to a shared
document, etc.). Use whatever tools are available in the current environment.
If no additional tools are connected, skip this step.

---

## Output Template

```markdown
# Requirements: [Project Name]

## 1. Problem & Intent
**Problem:** [Who has it, what it costs them]
**Success:** [What's different when this works]
**Users:** [Primary, secondary, stakeholders]

## 2. Functional Requirements

### [Capability Cluster 1]
- **FR-01:** [Description] — [Input] → [Output]
- **FR-02:** [Description] — [Input] → [Output]

### [Capability Cluster 2]
- **FR-03:** [Description]
...

## 3. Non-Functional Requirements
- **NFR-01:** [Performance — e.g. API response < 200ms at P95]
- **NFR-02:** [Security — e.g. OAuth 2.0, data encrypted at rest]
...

## 4. Priority (MoSCoW)

### Must Have
- FR-01, FR-03, NFR-01

### Should Have
- FR-02, NFR-02

### Could Have
- FR-04

### Won't Have (this version)
- [Explicitly scoped out items]

## 5. Open Questions & Risks
| Item | Type | Impact | Owner |
|------|------|--------|-------|
| [Question/risk] | Open question / Assumption / Risk | High/Med/Low | [Who resolves it] |
```

## What Comes Next

After requirements are captured, the natural next steps are:

- **Research brief** (research-brief skill) — investigate the technical landscape,
  compare approaches, and surface implementation options for the requirements.
- **Trade-off analysis** (trade-off-analysis skill) — if requirements surface a decision
  between competing approaches, run a quick structured comparison.
- **Implementation spec** (implementation-spec skill) — once requirements and research
  are done, produce a phased build plan with acceptance criteria.

---

## Interaction Style

- Interviewer, not interrogator. Keep it conversational. One question at a time where
  possible; batch only when the questions are tightly related.
- Challenge vague answers. "It should be fast" → "What response time would feel fast?
  Under 500ms? Under 2 seconds?"
- Reflect back what you've heard before moving to the next phase. Misunderstandings
  caught early are cheap; caught late they're expensive.
- British English spelling and punctuation.
- Be willing to say "you probably don't need to decide this yet" for things that can
  be deferred without risk.

## Handling Edge Cases

- **User already has a detailed brief**: Skip Phase 1, scan the brief for gaps, and
  focus on Phases 3-5 (non-functional requirements, prioritisation, and risks are
  almost always underdeveloped in existing briefs).
- **User wants to go straight to building**: Acknowledge the impulse, do a 5-minute
  rapid version (intent + top 5 must-haves + top 3 risks), then let them go.
- **User doesn't know the answer to a question**: Log it as an open question and move
  on. Don't stall the process.
- **Scope is enormous**: Suggest breaking into phases or workstreams. Elicit requirements
  for phase 1 only, and capture the rest as "future scope."
