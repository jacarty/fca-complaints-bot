---
name: research-brief
description: >
  Investigate a problem, feature, or project idea and produce a structured research
  brief with findings, recommended approach, and open questions. Use when the user
  wants to understand the technical landscape before building — comparing libraries,
  frameworks, patterns, or approaches. Triggers include "research this", "investigate",
  "what are the options for", "how should I approach", "what tools exist for",
  "compare approaches to", "do a deep dive on", "technical research for", or any
  situation where the user needs to gather information and form a recommendation
  before committing to an implementation path. Also use when a requirements document
  or tracked issue exists and needs technical investigation before a spec can be written.
---

# Research Brief — Technical Investigation & Recommendation

## What This Skill Does

This skill investigates a problem space and produces a **structured research brief** —
a document that summarises findings, compares options, makes a concrete recommendation,
and identifies open questions. The brief is designed to be consumed by a developer
(or by the implementation-spec skill) as the basis for technical decisions.

It is not a literature review. The output should be actionable: "here's what I found,
here's what I recommend, here's what's still uncertain."

## When to Use This Skill

Use when:

- The user has a problem or feature and needs to understand the solution landscape
- A requirements document exists and the next step is technical investigation
- A tracked issue needs research before implementation can begin
- The user says "how should I approach this?" or "what are the options?"
- Multiple libraries, frameworks, or patterns could solve the problem and the user
  needs a comparison

Do NOT use when:

- The user already knows what to build and wants a spec (use implementation-spec)
- The decision is between 2-3 known options (use trade-off-analysis — it's faster)
- The user wants to brainstorm approaches (use tree-of-thought or graph-of-thought)
- The problem is well-understood and just needs implementing (just do it)

---

## Process

### Step 1 — Understand What Needs Researching

Identify the research scope from one of these sources:

- **A tracked issue** — if the user points to an issue in a project tracker, read it
  using whatever tools are available. Extract what is being asked for, constraints,
  and any parent issue or project context.
- **A requirements document** — if requirements were produced by the requirements-elicitation
  skill, read them and identify the areas that need technical investigation.
- **A verbal brief** — if the user describes the problem conversationally, extract the
  core question and confirm scope before researching.

State what you're researching and what questions you're trying to answer. Confirm with
the user if the scope is ambiguous.

### Step 2 — Research

Adapt your research approach to the type of problem:

- **Technical implementation** (building, coding, integrating): Search for libraries,
  frameworks, best practices, existing implementations. Compare approaches with trade-offs.
  Look for pitfalls others have encountered.
- **Data and infrastructure** (ingestion, storage, pipelines): Search for data formats,
  parsing tools, storage patterns, scalability considerations. Check for existing solutions
  before recommending custom builds.
- **Evaluation and methodology** (testing, metrics, quality): Search for established
  frameworks, standard metrics, published methodologies. Look for real-world results.
- **Architecture and design** (choosing between approaches, system design): Search for
  comparison articles, architecture patterns, case studies. Focus on trade-offs.

Use your tools — don't guess when you can look things up:

- **Web search** — search for libraries, documentation, comparison articles, known pitfalls.
  Run multiple searches with different queries. Refine based on what you find.
- **Repository access** — if a target repository exists, read relevant files to ground
  research in the actual codebase. Check for a `CLAUDE.md` or similar project context file.
- **Issue tracker** — read issues and comments for context if the research is tied to a
  tracked piece of work.

Use whatever tools are available in the current environment. These categories aren't
exhaustive or mutually exclusive. Many problems span multiple types. Use judgement.

### Step 3 — Synthesise and Recommend

Distil your findings into a structured brief. Every recommendation must be grounded in
something you found, not in general knowledge. Be specific: "use pdfplumber 0.11+ with
`extract_tables`" not "consider using a PDF parsing library."

If the research surfaces a decision between competing approaches, note it. The user can
run the trade-off-analysis skill on that specific decision if they want a deeper comparison.

### Step 4 — Delivery

Produce the full research brief as markdown using the output template below. This is
always the primary output.

Then ask the user if they would like the output additionally delivered via any
connected tools (e.g. posted as a comment on a tracked issue, saved to a shared
document, etc.). Use whatever tools are available in the current environment.
If no additional tools are connected, skip this step.

When posting to an issue tracker, check for existing research briefs before posting
to avoid duplicates.

---

## Output Template

```markdown
# Research Brief: [Topic or Issue Title]

**Source:** [Issue reference, requirements doc, or verbal brief summary]
**Date:** [current date]
**Tools used:** [list each tool called during research]

## Summary
One paragraph: what was researched, what the key challenge is, and how it fits into
the broader project context (if applicable).

## Key Findings
Numbered list of the most important discoveries. Each finding should be specific and
actionable — not "there are many libraries available" but "pdfplumber preserves table
structure better than PyMuPDF for regulatory documents, based on comparisons in X and Y."

## Recommended Approach
Concrete recommendation for how to tackle this. Structure as a sequence of steps.
Justify each step by referencing findings. If there are meaningful alternatives, briefly
note them and explain why this approach is recommended over the others.

## Trade-Off Decisions
Any decisions that surfaced during research where multiple viable options exist. For
each, note the options and either make a recommendation or flag it for a dedicated
trade-off analysis.

## Open Questions
Things the research couldn't answer, ambiguities needing human judgement, or risks
to consider before starting implementation.

## Next Steps
Ordered list of concrete actions to take after reading this brief.
```

## What Comes Next

After a research brief is produced, the natural next steps are:

- **Trade-off analysis** (trade-off-analysis skill) — if the research surfaced a decision
  between 2-3 competing approaches, run a structured comparison.
- **Implementation spec** (implementation-spec skill) — combine the requirements and
  research brief into a phased build plan with acceptance criteria.

---

## Interaction Style

- Thorough but focused. A good brief is 500-1500 words, not 3000.
- Prefer concrete over abstract. Name specific tools, versions, and patterns.
- When comparing options, state trade-offs clearly. Make a recommendation and say why.
- If the problem is well-understood and straightforward, say so. Don't manufacture
  complexity.
- If the problem is underspecified or has significant risks, call that out clearly.
- Don't hallucinate capabilities or libraries. If you're not sure, search for it.
- British English spelling and punctuation.

## Handling Edge Cases

- **No tracked issue exists**: Work from the verbal brief or requirements document.
- **Research turns up nothing useful**: Say so. Note what you searched for and why
  the results were thin. This is valuable information — it tells the user they're
  in uncharted territory.
- **Scope is too broad**: Suggest decomposing into focused research questions. Tackle
  the most critical one first and note the others for follow-up.
- **Research contradicts the user's assumptions**: Present the findings neutrally.
  Don't soften them, but acknowledge the user's starting position and explain why
  the evidence points elsewhere.
