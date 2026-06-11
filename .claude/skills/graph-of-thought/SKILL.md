---
name: graph-of-thought
description: >
  Advanced ideation and problem-solving using the Graph of Thoughts (GoT) technique.
  Use when the user wants deep, interconnected exploration where ideas are combined, refined,
  and aggregated — not just branched and pruned. Triggers include "graph of thought",
  "GoT analysis", "combine approaches", "aggregate ideas", "interconnected thinking",
  "synthesise multiple angles", "cross-pollinate ideas", "merge the best parts",
  "hybrid solution", or complex challenges where the best answer combines elements from
  multiple reasoning paths. Also use for systems design, strategy with many interdependencies,
  multi-stakeholder trade-offs, or wicked problems needing fused partial solutions.
  Prefer over tree-of-thought when the user wants ideas merged rather than compared and selected.
---

# Graph of Thoughts — Interconnected Ideation & Problem Solving

## What This Skill Does

This skill applies the **Graph of Thoughts (GoT)** framework to explore a problem space as a
**directed graph** rather than a tree. Where Tree of Thought branches and prunes to find the
best single path, GoT allows reasoning chains to **merge, aggregate, refine, and loop back** —
producing hybrid solutions that combine the strongest elements from multiple lines of thinking.

The key operations that distinguish GoT from ToT:

- **Branching** — splitting a thought into multiple parallel explorations (same as ToT)
- **Aggregating chains** — merging insights from separate reasoning paths into a combined solution
- **Aggregating thoughts** — fusing individual ideas from different branches into new composite thoughts
- **Refining** — looping back to improve an earlier thought using insights gained later
- **Backtracking** — returning to a prior node when a path stalls, carrying forward what was learned

## When to Use GoT vs ToT

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

If in doubt, start with GoT for complex problems — it subsumes ToT's capabilities.
For decision-and-act problems, start with ToT — it produces cleaner, more actionable output.

---

## Process Overview

GoT runs through six phases. The graph structure means phases are not strictly linear —
refinement and aggregation can loop back. Present each phase with a clear heading.

```
Phase 1: Frame the Problem & Map the System
Phase 2: Generate Initial Thought Nodes
Phase 3: Explore, Branch & Score Intermediate Thoughts
Phase 4: Aggregate — Merge Chains & Fuse Thoughts
Phase 5: Refine & Backtrack
Phase 6: Synthesise — Final Solution & Reasoning Graph
```

---

## Phase 1 — Frame the Problem & Map the System

Go beyond simple problem framing — map the system around the problem.

1. **Restate the challenge** in one or two sentences.
2. **Identify the key dimensions** of the problem. These become the axes of exploration.
   For example, a GTM strategy might have dimensions: target segment, pricing model,
   channel mix, competitive positioning.
3. **Map interdependencies** — which dimensions affect each other? A change in pricing
   model constrains channel mix; a target segment choice shapes positioning. Note these
   links — they are where aggregation will happen later.
4. **Define success criteria** — 3-5 measurable or assessable criteria.
5. **Identify stakeholders or perspectives** that should inform the exploration.

Present this as a concise system map. Confirm with the user before proceeding.

---

## Phase 2 — Generate Initial Thought Nodes

Generate **3 to 5 initial thought nodes**. Unlike ToT branches (which are complete solution
paths), GoT nodes can be **partial ideas** — a strong insight about one dimension of the
problem that doesn't yet constitute a full solution.

For each node:

- **Node label** — short identifier (e.g. "N1: Platform-led growth", "N2: Enterprise-first")
- **Core insight** — 2-3 sentences on the idea
- **Dimension(s) covered** — which parts of the problem does this address?
- **Open connections** — what dimensions does this leave unresolved? These are ports where
  other nodes can connect.

The goal is coverage: collectively, the initial nodes should touch all key dimensions
identified in Phase 1, even if no single node addresses everything.

---

## Phase 3 — Explore, Branch & Score Intermediate Thoughts

Develop each node further. This is where the graph starts forming.

For each node:

1. **Branch** where the node could go in multiple directions. Create child nodes.
2. **Score intermediate thoughts** — not just end states. Assign each thought node a
   viability score (Strong / Promising / Weak) with a one-line rationale. This is a key
   GoT differentiator: intermediate reasoning steps are evaluated, not just final outputs.
3. **Cross-reference** — as you develop one branch, note where it echoes, contradicts, or
   complements a thought from another branch. Flag these as potential aggregation points.

Present the emerging graph structure. Use notation like:

```
N1 → N1a (Strong) → N1a-i
N1 → N1b (Promising)
N2 → N2a (Weak — deprioritise)
N2 → N2b (Strong) ←→ links to N1a (aggregation candidate)
```

### Graph Readability

As the graph grows, raw node notation becomes harder to follow. Use these guidelines to
keep the reasoning traceable without overwhelming the reader:

- **Up to ~6 active nodes**: use full notation (N1 → N1a, N2b ←→ N1a, etc.) inline.
- **7-12 active nodes**: present a consolidated graph snapshot at the end of this phase
  using a summary block that groups nodes by status (Strong / Promising / Weak / Pruned)
  and lists cross-references separately. Continue using labels in prose but don't repeat
  the full tree inline for every node.
- **12+ active nodes**: consolidate related nodes into named clusters before proceeding
  to aggregation. For example, if N2a, N2b, and N2c all concern technical depth, group
  them as "Technical Depth Cluster" with a one-line summary of each sub-node's status.
  Use cluster names in subsequent phases and only expand back to individual nodes when
  the detail matters.

The goal is that a reader encountering the analysis for the first time can follow the
reasoning graph without needing to cross-reference more than one or two sections. When in
doubt, favour a prose summary with selective notation over exhaustive node-by-node listings.

---

## Phase 4 — Aggregate: Merge Chains & Fuse Thoughts

This is the phase that makes GoT distinctive. Perform two types of aggregation:

### Chain Aggregation
Take two or more reasoning chains that approached the problem from different angles and
merge them into a combined solution path. The merged chain inherits the strengths of both
parent chains.

- Identify which chains are complementary (they solve different dimensions)
- Describe the merged chain and what it inherits from each parent
- Score the merged chain — it should be stronger than either parent alone. If it isn't,
  note the tension and whether it can be resolved.

### Thought Aggregation
Take individual thought nodes from different branches and fuse them into a new composite
thought that didn't exist in any single branch.

- Identify 2-3 thoughts from different branches that, combined, form something new
- Describe the fused thought and why the combination is more than the sum of its parts
- Note any contradictions that need resolving in the fusion

Present aggregated solutions clearly, showing their lineage:

```
AGGREGATED SOLUTION A
├── From N1a: [inherited element]
├── From N2b: [inherited element]
├── From N3:  [inherited element]
└── Novel synthesis: [what emerges from the combination]
```

### Aggregation Quality Check

Not every combination is a genuine aggregation. Before presenting a fused solution, test it:

1. **Emergence test** — does the combination produce an insight or capability that didn't
   exist in any parent node? If you're just listing elements side by side, that's a
   collection, not an aggregation. A real aggregation changes the framing, reveals a hidden
   constraint, or unlocks an approach that wasn't available from any single branch.
2. **Tension test** — do the parent nodes actually conflict on any dimension? If so, name
   the tension and explain how the aggregation resolves it (or acknowledge it doesn't).
   Aggregations that paper over contradictions are weaker than those that resolve them.
3. **So-what test** — state in one sentence what the aggregated solution enables that a
   single-branch recommendation would not. If you can't, the aggregation may be adding
   complexity without adding value — consider whether a simpler ToT-style recommendation
   would serve the user better. Be honest about this.

---

## Phase 5 — Refine & Backtrack

Now loop back through the graph with fresh eyes.

### Refine
Revisit earlier thought nodes using insights gained from later exploration and aggregation.
Does the aggregated solution from Phase 4 reveal a better version of an early idea? Update it.

- Identify 1-2 early nodes that can be improved with hindsight
- Describe the refined version and what changed
- Re-score if the refinement materially changes viability

### Backtrack
If any aggregated solution hits a dead end or internal contradiction:

1. Identify the blocking constraint
2. Trace back through the graph to find the node where the problematic assumption entered
3. Explore an alternative from that node, carrying forward everything learned since
4. Attempt re-aggregation with the new path

Be explicit about backtracking — it's a feature, not a failure. State what was learned
from the dead end.

---

## Phase 6 — Synthesise: Final Solution & Reasoning Graph

### Recommended Solution

Present the final synthesised solution:

- **Solution summary** — 3-5 sentences describing the recommended approach
- **Lineage** — which original nodes and aggregations contributed to this solution
- **Why aggregation beat any single branch** — what does the combined solution achieve
  that no individual branch could?
- **Concrete next steps** — 3-5 actionable items
- **Decision triggers** — signals that would prompt revisiting the solution

### Reasoning Graph Summary

Provide a compact map of the full reasoning journey:

```
Initial nodes: N1, N2, N3, N4
Pruned: N2a (weak feasibility)
Key aggregation: N1a + N2b → Aggregated Solution A
Refinement: N3 updated after aggregation revealed [insight]
Backtrack: Solution A hit [constraint], traced back to N1a, explored N1a-ii
Final synthesis: Solution A′ (refined) combining [elements]
```

### Reflection

- **What aggregation surfaced** — insights that only emerged from combining branches
- **Comparison to linear thinking** — what would a single-path analysis have missed?
- **Limitations** — assumptions needing validation, data gaps, blind spots
- **Suggested validation steps** — how to test the synthesised solution before committing

---

## Output Template

Adapt depth to problem complexity. Use this structure:

```markdown
# Graph of Thoughts: [Problem Title]

## 1. Problem Frame & System Map
[Challenge, dimensions, interdependencies, success criteria]

## 2. Initial Thought Nodes
### N1: [Label]
[Insight, dimensions covered, open connections]
### N2: [Label]
...

## 3. Exploration & Intermediate Scoring
[Branching, scoring, cross-references, emerging graph]

## 4. Aggregation
### Chain Aggregation
[Merged chains with lineage]
### Thought Aggregation
[Fused thoughts with lineage]

## 5. Refinement & Backtracking
[Refined nodes, backtrack journeys, lessons from dead ends]

## 6. Synthesised Solution
**Recommended approach:** ...
**Lineage:** ...
**Why aggregation won:** ...
**Next steps:** ...
**Decision triggers:** ...

## 7. Reasoning Graph
[Compact graph summary]

## 8. Reflection
[What emerged, limitations, validation steps]
```

---

## Interaction Style

- Collaborative thinking-partner tone — working through the graph together.
- British English spelling and punctuation.
- Be concrete and specific. Abstract hand-waving undermines the rigour of GoT.
- Use the graph notation (N1 → N1a, etc.) consistently so the user can trace the reasoning.
- Make aggregation explicit — always show what was combined and why.
- When backtracking, frame it positively: "This dead end taught us X, which improves Y."
- For very large problems, suggest decomposing into sub-graphs and tackling them in sequence,
  then aggregating the sub-graph outputs.

## Handling Edge Cases

- **User provides minimal context**: Run Phase 1 as a brief interview (3-4 targeted
  questions), then proceed. Focus questions on dimensions and interdependencies.
- **Problem is simple enough for ToT**: Say so. Offer to use ToT instead, noting that GoT
  adds overhead that may not pay off for straightforward decisions.
- **User wants to skip aggregation**: Respect it — but note that skipping aggregation
  essentially reduces GoT to ToT, and suggest they consider the ToT skill instead.
- **Too many nodes**: If the graph exceeds ~8-10 active nodes, consolidate. Group related
  nodes and treat the group as a single composite node going forward.
- **User wants a visual of the graph**: Describe the graph structure in text notation and
  offer to create a diagram if they'd like one.
