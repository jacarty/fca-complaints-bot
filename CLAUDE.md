# fca-complaints-bot — Claude Code Context

## Project Overview

A retrieval-augmented FCA complaints handling assistant. Given a complaint scenario, it retrieves the relevant FCA Handbook provisions, drafts a compliant response grounded in that text, cites the specific provisions, and flags cases needing human review. Built on AWS Bedrock Knowledge Bases. Output quality is measured by a drafting-aware adaptation of the dual LLM-as-judge harness from `rag-eval-framework`.

This is the working-application counterpart to the evaluation framework: the foundation project measured RAG quality on FCA text; this project applies the best-performing configuration to a real use case and tests whether the chunking finding generalises from Q&A to response drafting.

**Issue Tracking:** Linear — project "AI and Machine Learning", team "James", parent issue JAM-270 (sub-issues JAM-271 through JAM-276; JAM-62 is the parked Slack-bot follow-on).

---

## Rules

These are non-negotiable. They apply to every session regardless of scope.

- **Never commit directly to main.** Always create a feature or fix branch and open a PR.
- **Before pushing, verify only intended files are staged.** Run `git status` and `git diff --cached --name-only` before every commit.
- **James manages all git operations.** Claude does not push, commit, or open PRs directly.
- **Run lint + tests locally before opening a PR.**
- **No secrets in code.** AWS credentials and API keys come from environment variables or `.env`. Never hardcode them.
- **No manual steps.** Scraping, ingestion, KB setup, and eval runs are all scripted. If it can't be reproduced by running a command, it's not done.
- **This is a public repo.** No references to personal job applications, hiring targets, or company-specific career objectives in any committed content (code, docs, comments, commit messages).
- **Coach, don't generate.** James writes all code. Claude explains, guides, and reviews. Discuss the approach first, then guide James through writing it — do not generate implementation code unprompted.

---

## Architecture

```
FCA Handbook REST API (api-handbook.fca.org.uk)
  → scripts/scrape_fca.py → data/fca/fca_handbook.jsonl   (incl. DISP + CONC)
  → scripts/convert_fca_to_sections.py → section markdown
  → src/chunking/structure.py (provision-level, token-capped)

Synthetic bank policies (pinned, data/synthetic/policies/)
  → second KB data source (not regenerated — pinned markdown is the source of truth)

  → scripts/setup_knowledge_bases.py
      → S3 bucket: fca-complaints-bot (dedicated)
      → Bedrock KBs (S3 Vectors, Titan V2): structure-titan (bot) + fixed-titan (eval comparison)

Complaint ground-truth (scripts/generate_qa.py)
  → data/qa/ (scenario → expected provisions + required response elements)

Retrieval + drafting (src/pipeline.py)
  → Bedrock KB Retrieve API (semantic) → Claude Sonnet 4.6
  → structured outputs (outputConfig.textFormat) → typed response with citations

Web app (app/main.py)
  → FastAPI + HTMX + Jinja2 → chat UI with follow-up support

Evaluation (src/judge.py, src/metrics.py, scripts/run_eval.py)
  → drafting-aware dual judges (Haiku 4.5 + GPT-oss-120b)
  → {structure, fixed} chunking comparison → comparison tables
```

## Key Technology Choices

- **Language:** Python 3.13
- **Package manager:** uv
- **Cloud:** AWS (eu-west-1)
- **Web:** FastAPI + HTMX + Jinja2, served with uvicorn
- **Vector store:** S3 Vectors (serverless), in this project's own bucket. Semantic retrieval only — S3 Vectors does not support hybrid (dense + BM25) search. Two KBs/indexes: `structure-titan` and `fixed-titan`.
- **Embedding model:** Amazon Titan Text Embeddings V2 via Bedrock. Cohere is dropped entirely (the eval varies chunking, not embedding). The structure-aware chunker still caps chunks at 480 *Cohere* tokens — not because Cohere is used, but to keep chunk boundaries identical to the Post 16 baseline so the `structure-titan` results stay comparable; the cap is measured with the vendored tokenizer at `src/chunking/cohere_tokenizer.json` (no network at runtime).
- **Generation model:** Claude Sonnet 4.6 via Bedrock, with structured outputs.
- **Judge models:** Claude Haiku 4.5 (primary) + GPT-oss-120b (secondary, cross-provider agreement). Note: GPT-4o is retired on Bedrock; GPT-oss-120b is a reasoning model returning `reasoningContent` blocks, so the response parser must handle both formats.
- **Data validation:** Pydantic throughout.

## AWS Resources

- **Region:** eu-west-1
- **Account pinning:** the stack lives in a specific member account, not the Org management account. Marketplace subscriptions and model enablement are per-account. The default AWS profile may resolve to the management account, so `.env` pins `AWS_PROFILE` to the correct member account.
- **S3 bucket:** `fca-complaints-bot` (new, dedicated). This project is fully self-contained — nothing is shared with the eval framework's infrastructure.
- **S3 Vectors bucket / index:** new, dedicated to this project; set in `.env`.
- **Knowledge Bases:** two, both Titan — `structure-titan` (the bot's production config) and `fixed-titan` (built only for the structure-vs-fixed precision-on-drafting comparison). IDs stored in `config/knowledge_bases.json` after setup.

---

## Corpus Scope

The foundation scraper's `TARGET_MODULES` did **not** include DISP. Complaint handling cannot be answered without it. The complaints corpus adds:

- **DISP** — Dispute Resolution: Complaints. The core sourcebook: effective handling (DISP 1.3), resolving complaints (1.4), complaint time limits and the final-response rule (1.6), and the Financial Ombudsman Service referral rules (DISP 2/3).
- **CONC** — Consumer Credit, for credit-related complaints.
- The existing conduct/principles modules (PRIN, SYSC, BCOBS, COBS, ICOBS, etc.) remain relevant context.

Alongside the FCA text, the corpus includes the synthetic bank compliance policies carried over from the foundation project (pinned markdown in `data/synthetic/policies/`), uploaded to the bot's bucket as a second KB data source. These are not regenerated — the pinned markdown is the source of truth, so the ground-truth Q&A stays stable.

---

## Repository Structure

```
fca-complaints-bot/
├── CLAUDE.md                 # This file
├── README.md                 # Public-facing, results-led
├── pyproject.toml            # Dependencies (uv)
├── .env.example
├── src/
│   ├── pipeline.py           # Retrieval + response drafting
│   ├── judge.py              # Drafting-aware dual LLM-as-judge
│   ├── metrics.py            # Grounding, citation correctness, required-element coverage
│   ├── scraper/fca_client.py # FCA Handbook API client
│   └── chunking/structure.py # Structure-aware chunker (+ vendored Cohere tokenizer)
├── app/
│   ├── main.py               # FastAPI app
│   ├── templates/            # Jinja2 + HTMX
│   └── static/
├── scripts/
│   ├── scrape_fca.py         # FCA scraper (DISP + CONC + conduct modules)
│   ├── convert_fca_to_sections.py
│   ├── setup_knowledge_bases.py
│   ├── generate_policies.py  # Synthetic bank policy generator (provenance; not re-run)
│   ├── generate_qa.py        # Complaint ground-truth generator
│   ├── query.py              # Manual retrieval/draft testing CLI
│   └── run_eval.py           # Eval runner with per-question checkpointing
├── config/knowledge_bases.json
├── data/
│   ├── fca/                  # Scraped source (pinned); derived chunks gitignored
│   ├── synthetic/            # Synthetic bank policies (pinned markdown + metadata)
│   ├── qa/                   # Complaint ground-truth set (pinned)
│   ├── prompts/              # Drafting + judge prompts (pinned)
│   └── eval/                 # Results (gitignored except summaries)
├── docs/adr/                 # Architecture decision records
└── tests/
```

**Data in git:** source corpus (`fca/fca_handbook.jsonl`), ground-truth (`qa/`), and prompts are pinned for reproducibility; everything derived (section markdown, chunk prefixes) is gitignored and rebuilt by the scripts.

---

## Key Patterns

### FCA Handbook API

Two public REST endpoints, no authentication:

- **Table of contents:** `GET https://api-handbook.fca.org.uk/Handbook/GetAllHandbook` — full hierarchy: blocks → sourcebooks → chapters → sections, each node with an `entityId`.
- **Section content:** `GET https://api-handbook.fca.org.uk/Handbook/GetAllHandBookProvisionsSortedOrderByChapter/{chapterId}?sectionId={sectionId}&IsDeleted=false` — provisions with `provisionName` (rule ID), `provisionType` (Rules/Guidance/Evidential), `contentText` (plain text), `contentType` (HTML with cross-references).

### Structured outputs for drafting

The response is a Pydantic model (the drafted reply, the provisions cited, a human-review flag). Pass its JSON schema via `outputConfig.textFormat` to constrain Claude at the grammar level — no parsing hacks. First call compiles the schema (~30s), then it's cached for 24 hours.

### Ground-truth design (two ground truths)

Each complaint scenario records (1) the FCA provisions that should be retrieved, using section-level references (e.g. "DISP 1.6.2R"), and (2) the response elements a compliant reply must contain. The harness maps sections to chunk IDs per strategy, keeping ground truth strategy-agnostic.

### Drafting-aware eval

The foundation judge decomposed an answer into factual claims and graded each against retrieved chunks. A complaint response is mostly *not* drawn from the Handbook, so running that rubric unchanged scores legitimate non-regulatory content as "ungrounded". The judge is reworked to score regulatory grounding, citation correctness, and required-element coverage separately. Per-question checkpointing and `--resume` are retained from the foundation — essential for long runs.

---

## Testing

```bash
uv run pytest
```

| Level | Tool | Scope |
|-------|------|-------|
| Unit | pytest | Chunker, metrics, cross-reference parser |
| Integration | pytest | Retrieval + drafting on a small test corpus |

---

## Git Workflow

```
main                              ← protected
└── feature/jam-272-corpus-disp   ← feature work, named by Linear issue
└── fix/<slug>                    ← bug fixes
```

---

## Common Pitfalls & Constraints

Carried over from the foundation project — the corpus rebuild re-runs scraping and ingestion, so these recur.

- **Bedrock S3 metadata sidecars must be named `<full-filename>.metadata.json` (WITH the source extension) and wrapped in `{"metadataAttributes": {...}}`.** A wrongly named sidecar or a bare object is silently ignored — ingestion succeeds but chunks carry no custom metadata (verify with a `retrieve` call: only `x-amz-bedrock-kb-*` keys come back). Keep custom metadata to short scalars: it counts against the S3 Vectors 2048-byte filterable cap, so large lists can breach it and fail ingestion. Map ground truth via the scalar `section` key; provision IDs stay in the chunk text.
- **Account pinning matters.** Marketplace subscriptions and model enablement are per-account. The default AWS profile may resolve to the Org management account; `.env` pins `AWS_PROFILE` to the member account that owns the stack.
- **S3 Vectors `returnMetadata=True` requires both `s3vectors:QueryVectors` AND `s3vectors:GetVectors` IAM permissions.** Missing the second causes a 403 at query time.
- **One ingestion job per KB at a time.** `start_ingestion_job` rejects a second data source while the first is running. Sequence sources: start a job, poll `get_ingestion_job` to a terminal state (`COMPLETE`/`FAILED`/`STOPPED`), then start the next.
- **FCA API politeness.** No observed rate limits, but add a 0.5–1s delay between calls.
- **Hybrid search is unavailable on S3 Vectors.** Retrieval is semantic-only. If hybrid is ever needed, it means migrating to OpenSearch Serverless.

**Chunk-boundary parity (Cohere tokenizer retained, Cohere embedding dropped):**

- This project embeds with Titan only, but the structure-aware chunker still packs by **Cohere tokens (cap 480)** using the vendored tokenizer at `src/chunking/cohere_tokenizer.json` (no network at runtime). The cap is kept not because Cohere is used, but so chunk boundaries stay identical to the Post 16 baseline and the `structure-titan` results remain comparable. `convert_fca_to_sections.py` still decodes HTML entities before chunking (entities like `&nbsp;` inflate token counts sharply). Titan's own cap (~8192 tokens) is never binding. If comparability with Post 16 ever stops mattering, relax the cap to Titan's limit and drop the vendored tokenizer.

---

## Updating this document

Keep this file current as the project evolves. If a pattern, constraint, or resource changes, update it here.
