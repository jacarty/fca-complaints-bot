---
name: trade-off-analysis
description: >
  Quick, structured comparison of two or three options when a full Tree of Thought
  exploration would be overkill. Use when the user faces a binary or ternary decision
  and wants a clear pros/cons/recommendation — not a deep multi-branch exploration.
  Triggers include "should I use X or Y", "compare these options", "what are the
  trade-offs", "pros and cons", "which is better", "A vs B", "help me choose between",
  or any decision with a small number of well-defined alternatives. Prefer this over
  tree-of-thought when the options are already known and the user wants a decisive
  recommendation, not an open-ended exploration.
---

# Trade-Off Analysis — Quick Structured Comparison

## What This Skill Does

This skill provides a **fast, structured comparison** of 2-3 known options. It's the
lightweight alternative to Tree of Thought — use it when the options are already on the
table and the user wants clarity, not discovery.

The output is a single, scannable analysis that a busy person can read in 2-3 minutes
and walk away with a clear recommendation.

## When to Use This Skill

Use when:

- The user has 2-3 specific options and wants help choosing
- The decision is important enough to think through but not complex enough for ToT/GoT
- Time pressure favours speed over exhaustive exploration
- The user says "should I X or Y?" or "compare these for me"
- A decision point surfaced during requirements elicitation, research, or spec writing
  and needs a quick resolution

Do NOT use when:

- The user hasn't identified options yet (use tree-of-thought to generate them)
- The problem has deep interdependencies (use graph-of-thought)
- There are more than 3 options to compare (use tree-of-thought)
- The user wants open-ended brainstorming, not a comparison

---

## Process

### Step 1 — Confirm the Options and Context

State the options being compared and the context that matters. If the user hasn't
specified evaluation criteria, infer 4-6 relevant ones from the context. Common
dimensions include: cost, complexity, speed to value, maintainability, scalability,
risk, reversibility, team capability fit.

Keep this brief — 2-3 sentences of context, then the criteria list.

### Step 2 — Compare

Present a comparison table:

```
| Dimension        | Option A         | Option B         | Option C         |
|------------------|------------------|------------------|------------------|
| Cost             | Low              | Medium           | High             |
| Complexity       | Simple           | Moderate         | Complex          |
| Speed to value   | Fast (days)      | Medium (weeks)   | Slow (months)    |
| ...              | ...              | ...              | ...              |
```

Use concrete language in the cells, not just High/Medium/Low. "Fast (days)" is better
than "Fast". "$500/month" is better than "Moderate cost". Ground the comparison in
specifics wherever possible.

Below the table, write **1-2 sentences per option** summarising its core strength and
core weakness. Don't repeat the table — synthesise.

### Step 3 — Recommendation

State a clear recommendation in this format:

- **Go with [Option X]** — 2-3 sentence rationale
- **Switch to [Option Y] if** — the condition under which the recommendation changes
- **Avoid [Option Z] unless** — when the weakest option would actually be the right call

### Step 4 — Caveats

One short paragraph noting:

- Assumptions you've made that the user should validate
- Information that would change the recommendation if it turned out differently
- Anything you're uncertain about

### Step 5 — Delivery

For quick, in-conversation decisions, present the analysis inline — this is the default.

For decisions tied to a tracked piece of work or where the user wants a standalone
document, produce the analysis as markdown using the output template below.

Then ask the user if they would like the output additionally delivered via any
connected tools (e.g. posted as a comment on a tracked issue, saved to a shared
document, etc.). Use whatever tools are available in the current environment.
If no additional tools are connected, skip this step.

---

## Output Template

```markdown
# Trade-Off Analysis: [Decision Title]

**Context:** [Brief situation summary]
**Date:** [current date]

## Evaluation Criteria
[List of 4-6 dimensions being compared]

## Comparison
[Table + per-option synthesis]

## Recommendation
**Go with [Option]** — [rationale]
**Switch to [Option] if** — [condition]
**Avoid [Option] unless** — [condition]

## Caveats
[Assumptions, missing information, uncertainty]
```

---

## Interaction Style

- Decisive. The user wants a recommendation, not a balanced-but-unhelpful "it depends."
- Concrete. Use numbers, timeframes, and specifics wherever possible.
- Brief. The whole analysis should fit comfortably in one screen.
- British English spelling and punctuation.
- If you genuinely can't recommend one option over another, say so and explain exactly
  what information would break the tie.

## Handling Edge Cases

- **User gives only two options**: Drop the third column. Don't pad with a third option
  they didn't ask about.
- **Options are too vague to compare**: Ask one clarifying question to sharpen them,
  then proceed. Don't interview.
- **One option is clearly dominant**: Say so quickly. Don't artificially inflate the
  weaker option to seem balanced.
- **User wants deeper analysis**: Suggest switching to the tree-of-thought skill for
  a fuller exploration.
- **Decision surfaced during another skill**: Reference the source context (e.g.
  "This decision came up during the research brief for [topic]") and keep the analysis
  focused on the specific choice.
