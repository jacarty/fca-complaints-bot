---
name: tree-of-thought
description: >
  Structured ideation and problem-solving using the Tree of Thought (ToT) technique.
  Use this skill whenever the user wants to brainstorm, ideate, explore solutions to a challenge,
  solve a complex problem, weigh options, or think through a decision with multiple possible paths.
  Triggers include phrases like "help me think through", "brainstorm", "explore options",
  "what are the possible approaches", "ideation session", "problem solve", "tree of thought",
  "think this through systematically", "explore different angles", "weigh my options",
  "decision analysis", or any open-ended challenge where multiple solution paths should be
  explored, evaluated, and refined before arriving at a recommendation. Also use when the user
  presents a business problem, strategic question, or creative challenge and wants structured
  exploration rather than a single direct answer.
---

# Tree of Thought — Structured Ideation & Problem Solving

## What This Skill Does

This skill applies the **Tree of Thought (ToT)** prompting framework to systematically explore
a problem space. Instead of jumping to a single answer, it generates multiple reasoning branches,
evaluates them, prunes weak paths, backtracks when needed, and refines the strongest candidates
into a final recommendation — producing a documented reasoning trail the user can review and learn from.

## When to Use This Skill

Use ToT when the problem has **more than one plausible approach** and the user would benefit from
seeing the trade-offs explored rather than receiving a single answer. Good fits include:

- Strategic or business decisions with competing priorities
- Technical architecture choices with multiple viable patterns
- Creative challenges where divergent thinking adds value
- Go-to-market, pricing, or positioning questions
- Process improvement or workflow redesign
- Career or organisational decisions with multiple viable paths
- Any scenario where the user says "help me think through this"

Do NOT use ToT for simple factual lookups, single-step tasks, or when the user explicitly wants
a quick direct answer.

## When to Use ToT vs GoT

| Signal | Use ToT | Use GoT |
|--------|---------|---------|
| "Which option should I pick?" | ✓ | |
| "How can I combine the best of both?" | | ✓ |
| Problem has clear, mutually exclusive paths | ✓ | |
| Problem has interdependent, overlapping concerns | | ✓ |
| User wants a ranked comparison | ✓ | |
| User wants a hybrid or synthesised solution | | ✓ |
| Simple decision with 2-4 clear options | ✓ | |
| Wicked problem, systems design, multi-stakeholder | | ✓ |
| User needs actionable output with timelines and checklists | ✓ | |
| The answer depends on how you frame the problem | | ✓ |

If in doubt, start with ToT for decision problems — it produces cleaner, more actionable output.

---

## Process Overview

The skill runs through five phases. Present each phase clearly with a heading so the user can
follow the reasoning. Use the output template at the end of this file as the structure.

```
Phase 1: Frame the Problem
Phase 2: Generate Thought Branches
Phase 3: Evaluate & Prune
Phase 4: Deepen & Refine the Strongest Branches
Phase 5: Synthesise — Final Recommendation & Reflection
```

---

## Phase 1 — Frame the Problem

Before branching, establish a clear problem frame. This prevents wasted exploration.

1. **Restate the challenge** in one or two sentences, confirming understanding with the user.
2. **Identify constraints** — budget, time, skills, dependencies, risk appetite, values.
3. **Define success criteria** — what does a good solution look like? Try to surface 3-5 criteria
   (e.g. feasibility, cost, speed to value, strategic alignment, scalability, reversibility).
4. **Scope boundaries** — what is explicitly out of scope?

Present this as a concise summary and ask the user to confirm or adjust before proceeding.
If the user has already provided rich context, confirm inline and move on without blocking.

---

## Phase 2 — Generate Thought Branches

Generate **3 to 5 distinct solution branches**. Each branch should represent a genuinely
different strategic direction, not minor variations of the same idea.

For each branch, provide:

- **Branch label** — a short, memorable name (e.g. "Build In-House", "Partner & Integrate",
  "Acquire Capability")
- **Core idea** — 2-3 sentences describing the approach
- **Key assumptions** — what must be true for this branch to work
- **Early signal of strength/risk** — one reason this could be the winner and one reason it
  could fail

Think creatively. Include at least one branch that is unconventional or contrarian — the kind
of idea that might get dismissed too early but could be transformative.

---

## Phase 3 — Evaluate & Prune

Score each branch against the success criteria defined in Phase 1 using a simple
High / Medium / Low rating. Present this as a comparison table:

```
| Criterion          | Branch A | Branch B | Branch C | Branch D |
|--------------------|----------|----------|----------|----------|
| Feasibility        | High     | Medium   | Low      | Medium   |
| Strategic fit      | Medium   | High     | High     | Low      |
| Speed to value     | High     | Low      | Medium   | High     |
| ...                | ...      | ...      | ...      | ...      |
```

After scoring:

1. **Prune** any branch that scores Low on two or more critical criteria — explain why it is
   being set aside.
2. **Flag** branches worth backtracking to later if assumptions change.
3. **Carry forward** the top 2-3 branches into Phase 4.

Be transparent about the reasoning. If a branch is borderline, say so — the user may disagree
with the pruning and can redirect.

---

## Phase 4 — Deepen & Refine the Strongest Branches

For each surviving branch, go deeper:

1. **Implementation sketch** — high-level steps, key milestones, rough timeline
2. **Risk analysis** — what could go wrong, mitigations, contingency triggers
3. **Resource requirements** — people, money, tools, skills
4. **Second-order effects** — downstream consequences, dependencies, opportunities unlocked
5. **Backtrack check** — revisit assumptions from Phase 2. Has anything changed? If a branch
   now looks weaker, note it. If a pruned branch now looks viable, resurrect it.

This is where the "tree" earns its value — by going deeper on fewer branches rather than
staying shallow on many.

### Cross-Pollination Check

After deepening all surviving branches, step back and look across them before recommending.
This prevents the classic ToT weakness of evaluating branches in isolation when they share
insights that should inform each other.

1. **Shared insights** — did two branches independently surface the same finding? If so,
   that finding is high-confidence and should feature in the final recommendation regardless
   of which branch wins.
2. **Transferable elements** — does Branch A's implementation plan contain a step or tactic
   that would also strengthen Branch B? Note it. The recommendation may be "Branch A, but
   borrow this element from Branch B."
3. **Reframing check** — now that all branches have been deepened, does the overall picture
   suggest the problem is fundamentally different from how it was framed in Phase 1? If
   several branches point to the same root cause or the same constraint being the real
   bottleneck, call it out. Sometimes the deepening reveals that the problem isn't "which
   path?" but "which framing?" — e.g. a gap analysis might reveal that the gaps are
   presentational rather than substantive, which reframes the entire action plan.
4. **Hybrid viability** — could the strongest elements of two branches be combined into a
   solution that is better than either alone? If so, describe the hybrid briefly. This is
   not a full GoT-style aggregation — it's a quick check to see if the tree naturally
   suggests a graft.

If nothing emerges from this check, that's fine — say so and move to the recommendation.
But don't skip the check. The best ToT analyses are the ones where the final recommendation
is informed by the full landscape, not just the winning branch.

---

## Phase 5 — Synthesise: Final Recommendation & Reflection

### Recommendation

Present a clear recommendation:

- **Recommended path** and a concise rationale (2-3 sentences)
- **Runner-up** and when you would switch to it (decision triggers / reversibility)
- **Quick-win actions** — 2-3 concrete next steps the user can take immediately
- **Watch-outs** — signals that the chosen path is failing and it is time to backtrack

### Reasoning Trace

Provide a compact summary of the exploration journey:

> "We started with [N] branches. [Branch X] was pruned because [reason]. [Branch Y] and
> [Branch Z] were deepened. [Branch Y] emerged as the strongest due to [key differentiator]."

### Reflection on the Process

Close with a brief meta-reflection:

- **What the ToT approach surfaced** that a single-path analysis might have missed
- **Limitations** of this analysis (missing data, assumptions that need validation, biases)
- **Suggested next steps** for the user to validate the recommendation (e.g. talk to specific
  people, run a pilot, gather data)

---

## Output Template

Use this structure for the final deliverable. Adapt section depth to the complexity of the
problem — a lightweight challenge gets shorter sections; a strategic decision gets fuller ones.

```markdown
# Tree of Thought: [Problem Title]

## 1. Problem Frame
[Restated challenge, constraints, success criteria, scope]

## 2. Thought Branches
### Branch A: [Label]
[Core idea, assumptions, early signal]

### Branch B: [Label]
...

### Branch C: [Label]
...

## 3. Evaluation & Pruning
[Comparison table, pruning decisions, branches carried forward]

## 4. Deep Dive
### [Surviving Branch 1]
[Implementation sketch, risks, resources, second-order effects]

### [Surviving Branch 2]
...

## 5. Cross-Pollination Check
[Shared insights, transferable elements, reframing, hybrid viability — or "none identified"]

## 6. Recommendation
**Recommended path:** ...
**Runner-up:** ...
**Next steps:** ...
**Watch-outs:** ...

## 7. Reasoning Trace
[Compact journey summary]

## 8. Reflection
[What ToT surfaced, limitations, validation steps]
```

---

## Interaction Style

- Use a collaborative, thinking-partner tone — not lecturing.
- British English spelling and punctuation.
- Be direct and concrete. Avoid vague hand-waving like "consider various factors".
- Use analogies or real-world parallels where they sharpen understanding.
- When the problem is ambiguous, make a reasonable assumption, state it, and keep moving.
  Don't stall the exploration with excessive clarification questions.
- If the user pushes back on a pruning decision, re-open the branch without defensiveness.
- For very large problems, suggest breaking into sub-trees and tackling them sequentially.

## Handling Edge Cases

- **User provides very little context**: Run Phase 1 as a brief interview (2-3 targeted
  questions maximum), then proceed. Don't over-interview.
- **User wants fewer branches**: Generate the requested number but note what you would have
  explored with more.
- **User wants to skip evaluation**: Respect the request — jump to deepening the branches
  they find most interesting.
- **Problem is too narrow for ToT**: Say so. Suggest a direct answer instead and offer to
  use ToT if they want the fuller exploration anyway.
