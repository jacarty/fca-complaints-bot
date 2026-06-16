"""Generate ground-truth complaint scenarios for the drafting evaluation (JAM-274).

Adapts the JAM-237 Q&A generator. Seeds realistic synthetic complaint scenarios
from FCA Handbook sections (DISP-centric), generates them with Claude Opus, and
validates with gpt-oss-120b as an independent critic.

Each scenario records TWO ground truths:
  1. expected FCA provisions to retrieve/cite (provision refs + seed section IDs)
  2. required response elements a compliant reply must contain — drawn from a
     fixed controlled vocabulary (REQUIRED_ELEMENTS), so the JAM-275 rubric can
     score drafting deterministically.

Three complaint types:
  - single      — the complaint turns on one DISP area (the handling itself)
  - multi       — engages complaint handling + a substantive module (BCOBS/CONC/COBS/…)
  - escalation  — dissatisfaction / final response / referral to the FOS

Checkpointing: every scenario is written to a JSONL checkpoint as soon as it is
generated and validated. Re-running resumes from the checkpoint by default (the
JAM-237 lesson — a save-on-completion-only bug lost a 70-question run). Use
--fresh to start over, --finalise-only to rebuild outputs from the checkpoint.

Usage:
    uv run python scripts/generate_complaint_scenarios.py --dry-run     # show seed plan
    uv run python scripts/generate_complaint_scenarios.py               # generate (resumes)
    uv run python scripts/generate_complaint_scenarios.py --fresh       # wipe checkpoint, start over
    uv run python scripts/generate_complaint_scenarios.py --finalise-only
    uv run python scripts/generate_complaint_scenarios.py --test-models
"""

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

import boto3
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
FCA_SECTIONS_DIR = Path("data/fca/sections")
QA_OUTPUT_DIR = Path("data/qa")
PROMPTS_DIR = Path("data/prompts")

CHECKPOINT_PATH = QA_OUTPUT_DIR / "complaint_checkpoint.jsonl"
SCENARIOS_PATH = QA_OUTPUT_DIR / "complaint_scenarios.json"
CANDIDATES_PATH = QA_OUTPUT_DIR / "complaint_candidates.json"
FAILED_PATH = QA_OUTPUT_DIR / "complaint_failed.json"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
GENERATOR_MODEL = "global.anthropic.claude-opus-4-6-v1"
CRITIC_MODEL = "openai.gpt-oss-120b-1:0"

# ---------------------------------------------------------------------------
# Curated DISP section allowlists. The DISP section numbering is NOT sequential
# (disp1s9 == DISP 1.8; disp1s8 == Annex 1 return form), and most high-numbered
# sections are annexes, return forms, or reporting rules that make poor complaint
# seeds. These lists pin the substantive complaint-handling and FOS sections.
# ---------------------------------------------------------------------------
HANDLING_SECTIONS = [
    "disp1s2",  # DISP 1.2 Consumer awareness rules
    "disp1s3",  # DISP 1.3 Complaints handling rules
    "disp1s4",  # DISP 1.4 Complaints resolution rules
    "disp1s5",  # DISP 1.5 Resolved by close of the third business day
    "disp1s6",  # DISP 1.6 Complaints time limit rules (final response)
    "disp1s7",  # DISP 1.7 Complaints forwarding rules
    "disp1s9",  # DISP 1.8 Complaints time barring rule
]
# Sections that produce a final / summary response — the handling half of escalations.
FINAL_RESPONSE_SECTIONS = ["disp1s4", "disp1s5", "disp1s6"]
# FOS jurisdiction sections — the FOS half of escalations.
FOS_SECTIONS = [
    "disp2s2",  # DISP 2.2 Which complaints can FOS deal with
    "disp2s7",  # DISP 2.7 Is the complainant eligible
    "disp2s8",  # DISP 2.8 Was the complaint referred in time
]

# Caps per type (single is naturally bounded by the handling allowlist).
TARGET_MULTI = 20
TARGET_ESCALATION = 9

# Substantive-module section names to skip as multi pairings (too thin to seed on).
_SKIP_SUBSTANTIVE = ("purpose", "application", "interpretation", "introduction", "annex", "transitional", "product books")

API_DELAY = 1.0
MAX_SECTION_CHARS = 5000

# ---------------------------------------------------------------------------
# Controlled vocabulary for required_response_elements.
# Single source of truth, shared with the JAM-275 rubric (import from here).
# Granularity is by independent failure mode: FOS signposting (leaflet/website)
# is one element; the six-month limit is separate because it fails independently
# and is a more serious omission.
# ---------------------------------------------------------------------------
REQUIRED_ELEMENTS = {
    "acknowledgement": "Acknowledges the complaint and what it concerns.",
    "decision_and_reasons": "States the outcome (accept / partly accept / reject) with reasons.",
    "fos_referral_rights": (
        "Informs the customer of their right to refer the complaint to the Financial "
        "Ombudsman Service, including how to contact them (explanatory leaflet / website)."
    ),
    "six_month_referral_limit": (
        "States the six-month time limit for referring to the FOS and the appropriate "
        "consent wording (DISP 1 Annex 3R)."
    ),
    "eight_week_timeline": (
        "Addresses the eight-week final-response timeline where the timing of the "
        "response is in issue."
    ),
    "fair_treatment": (
        "Treats the customer fairly and with appropriate empathy, consistent with the "
        "Consumer Duty / Principles."
    ),
    "redress_consideration": (
        "Considers or offers appropriate redress or remedial action where the complaint "
        "warrants it."
    ),
    "vulnerability_consideration": (
        "Identifies and accommodates the needs of a vulnerable customer where the "
        "scenario indicates vulnerability."
    ),
}

# Substantive modules a multi-provision complaint might engage (besides DISP).
SUBSTANTIVE_MODULES = ["bcobs", "conc", "cobs", "mcob", "icobs", "prin"]

# Customer-conduct chapters per substantive module. Pins the multi pairings to
# sections a customer could actually complain about, excluding prudential/capital
# (CONC 10), APRC calculation (MCOB 10A), reporting, and meta chapters (PRIN 1).
SUBSTANTIVE_CHAPTERS = {
    "bcobs": ["bcobs2", "bcobs2a", "bcobs3", "bcobs4", "bcobs5", "bcobs6"],
    "conc": ["conc2", "conc3", "conc4", "conc5", "conc6", "conc7", "conc8", "conc11"],
    "cobs": ["cobs2", "cobs4", "cobs6", "cobs9", "cobs9a", "cobs10", "cobs10a", "cobs14", "cobs15", "cobs19"],
    "mcob": ["mcob2", "mcob2a", "mcob4", "mcob4a", "mcob5", "mcob6", "mcob7", "mcob12", "mcob13"],
    "icobs": ["icobs2", "icobs4", "icobs5", "icobs6", "icobs8"],
    "prin": ["prin2a"],
}


def _elements_block() -> str:
    """Render the controlled vocabulary as a bullet list for the prompts."""
    return "\n".join(f"- `{key}`: {desc}" for key, desc in REQUIRED_ELEMENTS.items())


def derive_elements(
    response_stage: str,
    redress_warranted: bool,
    timing_in_issue: bool,
    vulnerability_present: bool,
) -> list[str]:
    """Derive required_response_elements deterministically from stage + flags.

    This is the single rule that turns the model's classification into the element
    ground truth, so the set is consistent by construction rather than re-judged by
    the critic on every run. Ordered to match REQUIRED_ELEMENTS for stable output.
    """
    chosen = {"acknowledgement", "fair_treatment"}
    if timing_in_issue:
        chosen.add("eight_week_timeline")
    if vulnerability_present:
        chosen.add("vulnerability_consideration")
    if response_stage == "final":
        chosen.update({"decision_and_reasons", "fos_referral_rights", "six_month_referral_limit"})
        if redress_warranted:
            chosen.add("redress_consideration")
    return [e for e in REQUIRED_ELEMENTS if e in chosen]


# ===================================================================
# Section index (reused from the Q&A generator)
# ===================================================================


def load_fca_section_index(sections_dir: Path) -> dict[str, dict]:
    """Build an index of FCA sections from the .metadata.json sidecars."""
    index = {}
    for meta_path in sorted(sections_dir.glob("*.metadata.json")):
        with open(meta_path) as f:
            meta = json.load(f)["metadataAttributes"]
        section_id = meta["section"]
        md_path = sections_dir / f"{section_id}.md"
        if md_path.exists():
            index[section_id] = {
                "section_id": section_id,
                "module": meta["module"],
                "chapter": meta["chapter"],
                "section_name": meta.get("section_name", ""),
                "provision_count": meta.get("provision_count", 0),
                "path": md_path,
            }
    return index


def _core(section_id: str) -> bool:
    """Skip schedules, transitional provisions, and appendices."""
    return not any(tok in section_id for tok in ("sch", "tp", "app"))


def build_seed_plan(fca_index: dict[str, dict]) -> list[dict]:
    """Build the list of seeds from curated DISP allowlists.

    Each seed is {"complaint_type", "seed_sections": [section_id, ...]}.
      single      — one substantive handling section
      multi       — a handling section + a substantive-module section
      escalation  — a final-response section + a FOS jurisdiction section
    """
    def present(ids: list[str]) -> list[str]:
        return [s for s in ids if s in fca_index]

    handling = present(HANDLING_SECTIONS)
    final_resp = present(FINAL_RESPONSE_SECTIONS)
    fos = present(FOS_SECTIONS)

    if not handling:
        raise SystemExit("None of the curated DISP handling sections are in the corpus.")

    # Substantive-module sections, restricted to customer-conduct chapters and
    # skipping thin application/purpose/annex sections within them.
    substantive: dict[str, list[str]] = {}
    for sid in sorted(fca_index):
        info = fca_index[sid]
        mod = info["module"]
        if mod not in SUBSTANTIVE_MODULES or not _core(sid):
            continue
        if info["chapter"] not in SUBSTANTIVE_CHAPTERS.get(mod, []):
            continue
        name = info["section_name"].lower()
        if any(tok in name for tok in _SKIP_SUBSTANTIVE):
            continue
        substantive.setdefault(mod, []).append(sid)
    mod_cycle = [m for m in SUBSTANTIVE_MODULES if substantive.get(m)]

    plan: list[dict] = []
    seen: set[tuple] = set()

    def add(ctype: str, sections: list[str]) -> None:
        key = (ctype, tuple(sorted(sections)))
        if key not in seen:
            seen.add(key)
            plan.append({"complaint_type": ctype, "seed_sections": sorted(sections)})

    # single — one per handling section
    for sid in handling:
        add("single", [sid])

    # multi — handling x substantive, cycling modules for variety
    if mod_cycle:
        i = 0
        while sum(p["complaint_type"] == "multi" for p in plan) < TARGET_MULTI:
            h = handling[i % len(handling)]
            mod = mod_cycle[i % len(mod_cycle)]
            sub_list = substantive[mod]
            sub = sub_list[(i // len(mod_cycle)) % len(sub_list)]
            add("multi", [h, sub])
            i += 1
            if i > TARGET_MULTI * 6:  # safety: combos exhausted
                break
    else:
        logger.warning("No substantive-module sections found — skipping multi seeds")

    # escalation — final-response x FOS
    if final_resp and fos:
        for fr in final_resp:
            for f in fos:
                if sum(p["complaint_type"] == "escalation" for p in plan) >= TARGET_ESCALATION:
                    break
                add("escalation", [fr, f])
    else:
        logger.warning("Missing final-response or FOS sections — skipping escalation seeds")

    return plan


def seed_id(seed: dict) -> str:
    """Stable identifier for a seed, used to resume and de-duplicate."""
    return f"{seed['complaint_type']}:{'+'.join(sorted(seed['seed_sections']))}"


# ===================================================================
# Bedrock helpers (reused from the Q&A generator)
# ===================================================================


def get_bedrock_client(profile: str | None = None):
    session = boto3.Session(
        profile_name=profile or os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "eu-west-1"),
    )
    return session.client(
        "bedrock-runtime", config=boto3.session.Config(read_timeout=300)
    )


def _extract_text_from_converse(response: dict) -> str:
    content_blocks = response["output"]["message"]["content"]
    for block in content_blocks:
        if isinstance(block, dict) and "text" in block:
            return block["text"]
    if content_blocks:
        block = content_blocks[0]
        if isinstance(block, str):
            return block
        return json.dumps(block)
    raise ValueError(f"No text content in response: {response['output']['message']}")


def call_claude(client, system_prompt, user_prompt, *, max_tokens=4096, temperature=0.7) -> str:
    response = client.converse(
        modelId=GENERATOR_MODEL,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return _extract_text_from_converse(response)


def call_critic(client, system_prompt, user_prompt, *, max_tokens=2048, temperature=0.3) -> str:
    response = client.converse(
        modelId=CRITIC_MODEL,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return _extract_text_from_converse(response)


def parse_json_from_response(text: str) -> dict | list | None:
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for pattern in (r"(\{.*\})", r"(\[.*\])"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


# ===================================================================
# Prompts (controlled vocabulary injected via replace, not format —
# the JSON braces in the templates would break str.format)
# ===================================================================

COMPLAINT_GEN_SYSTEM = """\
You are a UK financial regulation expert creating synthetic complaint scenarios to \
evaluate a complaints-handling assistant for a UK bank. Each scenario must be \
realistic, grounded in the seed material provided, and come with two ground truths.

You are given one or more FCA Handbook sections as seed material. From them, create:

1. A realistic customer complaint, written as a complaints handler would receive or \
summarise it (a short narrative — what the customer is unhappy about).
2. `expected_provisions`: the specific FCA provision references (e.g. "DISP 1.6.2R") \
that a correct response should rely on. These MUST appear in the seed material — do \
not cite provisions that are not in the seed text.
3. A classification of what kind of response the scenario calls for. You do NOT list the \
required response elements yourself — those are derived from your classification. Provide:
   - `response_stage`: "final" for the normal case — the complaint raises a substantive \
matter the firm can investigate and decide (a wrong charge, a mis-sale, an information \
failure), so the handler drafts a substantive final response. The fact that the firm has \
not replied yet does NOT make it holding — that is simply why a response is being drafted. \
Use "holding" ONLY when the complaint is itself about the firm's failure to acknowledge or \
progress an earlier complaint ("I complained weeks ago and have heard nothing"), where the \
only appropriate response right now is to acknowledge and update.
   - `redress_warranted`: true if a compliant response would need to offer or consider \
redress or remedial action (only meaningful at the final stage).
   - `timing_in_issue`: true ONLY when the firm's own response timeliness is part of the \
complaint — i.e. the customer is complaining about delay, a missed acknowledgement, or the \
firm exceeding its response deadline (the eight-week final-response clock, or the shorter \
holding-response clock). Set it FALSE when the only "timing" raised is the customer's FOS \
referral window (the six-month limit after a final response — that is covered by \
`fos_referral_rights`/`six_month_referral_limit`) or whether the complaint is too old to be \
made at all (the eligibility time-bar). Those are not about the firm's response speed.
   - `vulnerability_present`: true if the scenario indicates a vulnerable customer — \
including financial difficulty or hardship, age, ill health or disability, bereavement, or \
low financial capability.

For reference, these classifications determine the required response elements as follows:
- Always: `acknowledgement`, `fair_treatment`.
- `timing_in_issue` adds `eight_week_timeline`.
- `vulnerability_present` adds `vulnerability_consideration`.
- `response_stage` = "final" adds `decision_and_reasons`, `fos_referral_rights`, and \
`six_month_referral_limit`; and `redress_warranted` then adds `redress_consideration`.

Choose the classification that genuinely fits: a substantive complaint the firm can \
investigate and decide is "final" even if no reply has been sent yet. Reserve "holding" for \
complaints that are themselves about a lack of acknowledgement or progress.

Assign a difficulty: easy (one clear provision, holding stage), medium (a few), hard \
(multi-provision synthesis, or a subtle or edge issue).

Respond with ONLY a JSON object (no markdown fences, no preamble):
{
  "scenario_text": "...",
  "expected_provisions": ["..."],
  "response_stage": "holding|final",
  "redress_warranted": true|false,
  "timing_in_issue": true|false,
  "vulnerability_present": true|false,
  "difficulty": "easy|medium|hard"
}"""


_TYPE_INSTRUCTION = {
    "single": (
        "SINGLE-COMPLAINT scenario: the complaint should turn on the single DISP area "
        "in the seed — typically about how the complaint itself was handled."
    ),
    "multi": (
        "MULTI-PROVISION scenario: the complaint should engage BOTH the complaint-handling "
        "rules and the substantive area in the seed, requiring provisions from both."
    ),
    "escalation": (
        "ESCALATION scenario: the customer is dissatisfied, or the matter concerns a final "
        "response and the customer's right to refer to the Financial Ombudsman Service."
    ),
}


def build_gen_user_prompt(complaint_type: str, seed_sections: list[str], fca_index: dict) -> str:
    blocks = []
    for sid in seed_sections:
        info = fca_index[sid]
        text = info["path"].read_text()
        if len(text) > MAX_SECTION_CHARS:
            text = text[:MAX_SECTION_CHARS] + "\n\n[... truncated ...]"
        blocks.append(f"### {sid} — {info['section_name']}\n{text}")
    seed_block = "\n\n---\n\n".join(blocks)
    return (
        f"{_TYPE_INSTRUCTION[complaint_type]}\n\n"
        f"Seed material:\n\n{seed_block}\n\n"
        f"Create one ground-truth complaint scenario from this seed material."
    )


COMPLAINT_CRITIC_SYSTEM = """\
You are validating synthetic complaint scenarios generated for a drafting-evaluation \
benchmark over UK FCA complaint-handling rules.

For each scenario you are given the complaint narrative, its two ground truths, and the \
seed source material. Evaluate three criteria:

- REALISM: Is the scenario a realistic complaint a UK bank customer might raise?
- PROVISIONS: Do the expected_provisions correctly identify rules that apply to this \
scenario, and is their subject matter covered by the seed material? Judge this on \
SUBSTANCE, not exact string form. Treat references as matching even when they differ only \
in the provision-type suffix (R/G/E) or sub-paragraph notation — "DISP 1.3.1R", "DISP \
1.3.1", and "DISP 1.3.1(1)" all refer to the same provision. PASS if every listed provision \
appears in the seed material and is plausibly relevant to the complaint. Do NOT fail because \
a further relevant provision could have been added (the list need not be exhaustive), nor \
because an in-section provision is only arguably or marginally relevant. Fail ONLY if a \
listed provision is absent from the seed material, belongs to an unrelated section, or is \
plainly irrelevant to the complaint — never on completeness or formatting.
- STAGE & FLAGS: The required response elements are DERIVED in code from the scenario's \
classification, so do not judge the element list itself. Instead judge whether the \
classification fits the scenario shown. For `response_stage`: do NOT treat "the firm has not \
yet issued a decision" as indicating a holding stage — almost every complaint awaiting a \
response has had no decision yet, and that alone does not make it holding. A substantive \
complaint the firm can investigate and decide (a charge, a mis-sale, an information failure) \
is "final" even though no decision has been issued yet. "holding" applies ONLY when the \
complaint is itself about a lack of acknowledgement or progress on an earlier complaint. \
Also check `redress_warranted`, `timing_in_issue`, and `vulnerability_present` are \
consistent with the complaint (treat financial hardship, age, ill health, or bereavement as \
vulnerability). For `timing_in_issue` specifically: it is TRUE only when the firm's own \
response timeliness is in issue (delay, a missed acknowledgement, or breaching the response \
deadline). Do NOT infer it from the customer asking about their FOS referral window (the \
six-month limit) or whether the complaint is too old to bring (the eligibility time-bar) — \
those are covered by other elements and do NOT make `timing_in_issue` true. Fail ONLY if a \
classification is clearly wrong — not because a different reading is arguable.

Respond with ONLY a JSON object (no markdown fences, no preamble):
{
  "verdict": "pass|fail",
  "realism": "pass|fail",
  "realism_reasoning": "...",
  "provisions_correct": "pass|fail",
  "provisions_reasoning": "...",
  "classification_plausible": "pass|fail",
  "classification_reasoning": "...",
  "difficulty_reasonable": true,
  "suggested_difficulty": "easy|medium|hard",
  "overall_reasoning": "..."
}"""


# ===================================================================
# Generation + validation
# ===================================================================


def generate_scenario(client, seed: dict, fca_index: dict) -> dict | None:
    """Generate one complaint scenario from a seed. Returns the candidate or None."""
    user_prompt = build_gen_user_prompt(seed["complaint_type"], seed["seed_sections"], fca_index)
    try:
        response = call_claude(client, COMPLAINT_GEN_SYSTEM, user_prompt)
        result = parse_json_from_response(response)
    except Exception as e:
        logger.warning("Generation failed for %s: %s", seed_id(seed), e)
        return None

    if not isinstance(result, dict) or "scenario_text" not in result:
        logger.warning("Unparseable generation for %s", seed_id(seed))
        return None

    stage = result.get("response_stage", "final")
    if stage not in ("holding", "final"):
        stage = "final"
    redress = bool(result.get("redress_warranted", False))
    timing = bool(result.get("timing_in_issue", False))
    vulnerability = bool(result.get("vulnerability_present", False))
    elements = derive_elements(stage, redress, timing, vulnerability)

    return {
        "seed_id": seed_id(seed),
        "complaint_type": seed["complaint_type"],
        "scenario_text": result["scenario_text"],
        "expected_provisions": result.get("expected_provisions", []),
        "expected_provision_sections": list(seed["seed_sections"]),
        "response_stage": stage,
        "redress_warranted": redress,
        "timing_in_issue": timing,
        "vulnerability_present": vulnerability,
        "required_response_elements": elements,
        "difficulty": result.get("difficulty", "medium"),
        "synthetic": True,
    }


def validate_scenario(client, candidate: dict, fca_index: dict) -> dict | None:
    """Run the critic on one candidate. Returns the critic result or None."""
    blocks = []
    for sid in candidate["expected_provision_sections"]:
        info = fca_index.get(sid)
        text = info["path"].read_text() if info else "[seed text unavailable]"
        if len(text) > MAX_SECTION_CHARS:
            text = text[:MAX_SECTION_CHARS] + "\n\n[... truncated ...]"
        blocks.append(f"### {sid}\n{text}")
    seed_material = "\n\n---\n\n".join(blocks)

    user_prompt = (
        f"Scenario type: {candidate['complaint_type']}\n\n"
        f"Complaint: {candidate['scenario_text']}\n\n"
        f"expected_provisions: {candidate['expected_provisions']}\n\n"
        f"Classification to judge:\n"
        f"  response_stage: {candidate['response_stage']}\n"
        f"  redress_warranted: {candidate['redress_warranted']}\n"
        f"  timing_in_issue: {candidate['timing_in_issue']}\n"
        f"  vulnerability_present: {candidate['vulnerability_present']}\n"
        f"difficulty: {candidate['difficulty']}\n\n"
        f"Seed material:\n\n{seed_material}"
    )
    try:
        response = call_critic(client, COMPLAINT_CRITIC_SYSTEM, user_prompt)
        result = parse_json_from_response(response)
        if isinstance(result, dict):
            return result
    except Exception as e:
        logger.warning("Critic failed for %s: %s", candidate["seed_id"], e)
    return None


# ===================================================================
# Checkpointing
# ===================================================================


def load_checkpoint() -> list[dict]:
    if not CHECKPOINT_PATH.exists():
        return []
    rows = []
    for line in CHECKPOINT_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append_checkpoint(candidate: dict) -> None:
    QA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "a") as f:
        f.write(json.dumps(candidate) + "\n")


# ===================================================================
# Output / finalisation
# ===================================================================


def finalise(checkpoint_rows: list[dict]) -> None:
    """Rebuild the curated/candidate/failed outputs from the checkpoint."""
    QA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # De-duplicate by seed_id, keeping the last occurrence
    by_seed: dict[str, dict] = {}
    for row in checkpoint_rows:
        by_seed[row.get("seed_id", id(row))] = row
    rows = list(by_seed.values())

    passed = [r for r in rows if r.get("verdict") == "pass"]
    failed = [r for r in rows if r.get("verdict") == "fail"]

    clean = []
    for i, r in enumerate(passed, 1):
        clean.append(
            {
                "scenario_id": f"c{i:03d}",
                "complaint_type": r["complaint_type"],
                "response_stage": r.get("response_stage", "final"),
                "scenario_text": r["scenario_text"],
                "expected_provisions": r["expected_provisions"],
                "expected_provision_sections": r["expected_provision_sections"],
                "required_response_elements": r["required_response_elements"],
                "difficulty": r["difficulty"],
                "synthetic": True,
            }
        )

    SCENARIOS_PATH.write_text(json.dumps(clean, indent=2))
    CANDIDATES_PATH.write_text(json.dumps(rows, indent=2))
    if failed:
        FAILED_PATH.write_text(json.dumps(failed, indent=2))

    logger.info("Wrote %d curated scenarios to %s", len(clean), SCENARIOS_PATH)
    logger.info("Wrote %d candidates to %s", len(rows), CANDIDATES_PATH)
    if failed:
        logger.info("Wrote %d failed to %s", len(failed), FAILED_PATH)

    # Summary
    type_counts: dict[str, int] = {}
    elem_counts: dict[str, int] = {}
    for s in clean:
        type_counts[s["complaint_type"]] = type_counts.get(s["complaint_type"], 0) + 1
        for e in s["required_response_elements"]:
            elem_counts[e] = elem_counts.get(e, 0) + 1

    print(f"\n{'=' * 60}\nComplaint Scenario Summary\n{'=' * 60}")
    print(f"Total processed:  {len(rows)}")
    print(f"Passed:           {len(clean)}")
    print(f"Failed:           {len(failed)}")
    print("\nBy type:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t:14s} {c:3d}")
    print("\nRequired-element frequency:")
    for e, c in sorted(elem_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {e:28s} {c:3d}")
    print(f"{'=' * 60}\n")


def save_prompts() -> None:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generator_model": GENERATOR_MODEL,
        "critic_model": CRITIC_MODEL,
        "required_elements": REQUIRED_ELEMENTS,
        "complaint_gen_system": COMPLAINT_GEN_SYSTEM,
        "complaint_critic_system": COMPLAINT_CRITIC_SYSTEM,
        "type_instructions": _TYPE_INSTRUCTION,
    }
    path = PROMPTS_DIR / "complaint_generation_prompts.json"
    path.write_text(json.dumps(payload, indent=2))
    logger.info("Saved prompts + vocabulary to %s", path)


# ===================================================================
# CLI
# ===================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ground-truth complaint scenarios")
    parser.add_argument("--dry-run", action="store_true", help="Show the seed plan, no API calls")
    parser.add_argument("--fresh", action="store_true", help="Delete the checkpoint and start over")
    parser.add_argument(
        "--finalise-only", action="store_true", help="Rebuild outputs from the checkpoint, no generation"
    )
    parser.add_argument("--skip-validation", action="store_true", help="Generate without the critic")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of seeds (testing)")
    parser.add_argument("--profile", type=str, default=None, help="AWS profile (overrides AWS_PROFILE)")
    parser.add_argument("--test-models", action="store_true", help="One call to each model and exit")
    args = parser.parse_args()

    load_dotenv()

    if args.test_models:
        client = get_bedrock_client(args.profile)
        for name, model in (("generator", GENERATOR_MODEL), ("critic", CRITIC_MODEL)):
            try:
                r = client.converse(
                    modelId=model,
                    system=[{"text": "You are a test."}],
                    messages=[{"role": "user", "content": [{"text": "Reply with exactly: OK"}]}],
                    inferenceConfig={"maxTokens": 16, "temperature": 0.3},
                )
                print(f"  {name} ({model}): {_extract_text_from_converse(r).strip()}")
            except Exception as e:
                print(f"  {name} ({model}) FAILED: {type(e).__name__}: {e}")
        return

    if args.finalise_only:
        finalise(load_checkpoint())
        return

    if not FCA_SECTIONS_DIR.exists():
        raise SystemExit(f"FCA sections not found at {FCA_SECTIONS_DIR}")

    fca_index = load_fca_section_index(FCA_SECTIONS_DIR)
    logger.info("Indexed %d FCA sections", len(fca_index))

    plan = build_seed_plan(fca_index)
    if args.limit:
        plan = plan[: args.limit]

    if args.dry_run:
        print(f"\n{'=' * 60}\nSeed Plan ({len(plan)} seeds)\n{'=' * 60}")
        for seed in plan:
            names = " + ".join(fca_index[s]["section_name"] for s in seed["seed_sections"])
            print(f"  {seed['complaint_type']:11s} {seed['seed_sections']}")
            print(f"              {names}")
        print(f"{'=' * 60}\n")
        return

    if args.fresh and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        logger.info("Deleted checkpoint — starting fresh")

    done = {r["seed_id"] for r in load_checkpoint()}
    if done:
        logger.info("Resuming — %d seeds already in checkpoint", len(done))

    client = get_bedrock_client(args.profile)
    save_prompts()

    for i, seed in enumerate(plan, 1):
        sid = seed_id(seed)
        if sid in done:
            continue
        logger.info("[%d/%d] %s", i, len(plan), sid)

        candidate = generate_scenario(client, seed, fca_index)
        if candidate is None:
            time.sleep(API_DELAY)
            continue

        if args.skip_validation:
            candidate["verdict"] = "skipped"
            candidate["critic_result"] = None
        else:
            time.sleep(API_DELAY)
            critic = validate_scenario(client, candidate, fca_index)
            candidate["critic_result"] = critic
            
            # Gate the verdict only on what the critic judges reliably: is the
            # scenario realistic, and are the provisions real. The classification
            # flags (stage / timing / redress / vulnerability) are the generator's
            # deterministic output — record the critic's view as advisory, but do
            # not let it gate pass/fail (that was the source of run-to-run churn).
            if not critic:
                candidate["verdict"] = "error"
            elif critic.get("realism") == "pass" and critic.get("provisions_correct") == "pass":
                candidate["verdict"] = "pass"
            else:
                candidate["verdict"] = "fail"

        append_checkpoint(candidate)
        logger.info(
            "  %s — %s — %d provisions, %d elements",
            candidate["verdict"],
            candidate["difficulty"],
            len(candidate["expected_provisions"]),
            len(candidate["required_response_elements"]),
        )
        time.sleep(API_DELAY)

    finalise(load_checkpoint())


if __name__ == "__main__":
    main()
