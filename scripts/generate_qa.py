"""Generate ground-truth Q&A pairs for the RAG evaluation framework.

Reads FCA whole-section markdown and synthetic bank policies, generates Q&A
pairs using Claude Opus via Bedrock, validates with gpt-oss-120b as critic,
and outputs curated pairs to data/qa/qa_pairs.json.

Three question types:
  - Single-module:       ~30% — one FCA section answers the question
  - Cross-module:        ~40% — two FCA sections from different modules required
  - Policy + regulation: ~30% — synthetic policy + FCA section pairing

Usage:
    uv run python scripts/generate_qa.py                    # Full run
    uv run python scripts/generate_qa.py --dry-run          # Show section pool, no generation
    uv run python scripts/generate_qa.py --skip-validation  # Generate without critic pass
    uv run python scripts/generate_qa.py --generate-only    # Generate candidates, stop
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
POLICIES_DIR = Path("data/synthetic/policies")
QA_OUTPUT_DIR = Path("data/qa")
PROMPTS_DIR = Path("data/prompts")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
GENERATOR_MODEL = "global.anthropic.claude-opus-4-6-v1"
CRITIC_MODEL = "openai.gpt-oss-120b-1:0"

# ---------------------------------------------------------------------------
# Target counts (overshoot by ~25% to allow filtering)
# ---------------------------------------------------------------------------
TARGET_SINGLE = 25  # ~30% of 75
TARGET_CROSS = 35  # ~40% of 75
TARGET_POLICY = 25  # ~30% of 75

# Delay between Bedrock calls (seconds)
API_DELAY = 1.0


# ===================================================================
# Section pool building
# ===================================================================


def load_fca_section_index(sections_dir: Path) -> dict[str, dict]:
    """Build an index of all FCA sections: {section_id: {metadata, path}}.

    Reads every .metadata.json sidecar in the sections directory.
    """
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


def extract_fca_refs_from_policy(policy_path: Path) -> list[str]:
    """Extract FCA module/section references from a synthetic policy's Regulatory Context table.

    Parses the markdown table looking for patterns like:
      SYSC 10.1, PRIN 2, COBS 4.2, MAR 1, FIT 1.3, COCON, DISP 1.6, etc.

    Returns normalised references like: ["SYSC 10.1", "PRIN 2", "COBS 4.2"]
    """
    text = policy_path.read_text()

    # Find the Regulatory Context section
    reg_match = re.search(
        r"##\s+\d+\.\s+Regulatory Context(.*?)(?=\n##\s+\d+\.|\Z)",
        text,
        re.DOTALL,
    )
    if not reg_match:
        return []

    reg_section = reg_match.group(1)

    # Extract FCA module references from the table
    # Patterns: "SYSC 10.1", "PRIN 2A", "PRIN 2 (Principle 8)", "SYSC 10.1.10R",
    #           "MAR 1", "COCON", "FIT 1.3", "DISP 1.6", "COBS 2.1.1R"
    # We want the module + chapter.section level, not individual provision IDs.
    refs = set()
    _modules_re = (
        r"SYSC|PRIN|COBS|CASS|DISP|COCON|MAR|FIT|BCOBS|MCOB|ICOBS|PROD"
        r"|GEN|TC|ESG|SUP|FEES|APER|COND|FINMAR"
    )
    # Match "MODULE chapter.section" — including alphanumeric chapters like 2A, 10A
    for m in re.finditer(
        rf"\b({_modules_re})\s+(\d+[A-Za-z]?(?:\.\d+)?)",
        reg_section,
    ):
        module = m.group(1)
        nums = m.group(2)
        refs.add(f"{module} {nums}")

    # Also catch bare module names (e.g. "COCON" without a number)
    for m in re.finditer(
        rf"\*\*\b({_modules_re})\b\*\*",
        reg_section,
    ):
        refs.add(m.group(1))

    return sorted(refs)


def fca_ref_to_section_ids(ref: str, fca_index: dict[str, dict]) -> list[str]:
    """Map an FCA reference like 'SYSC 10.1' to matching section IDs.

    'SYSC 10.1' → sections matching chapter 'sysc10' with section starting 's1'
    'SYSC 10'   → all sections in chapter 'sysc10'
    'PRIN 2A'   → all sections in chapter 'prin2a' (Consumer Duty)
    'PRIN 2'    → all sections in chapter 'prin2' (The Principles)
    'COCON'     → all sections in module 'cocon'
    """
    ref = ref.strip()

    # Split into module and numbers
    parts = ref.split(None, 1)
    module = parts[0].lower()

    if len(parts) == 1:
        # Bare module name — match all sections in this module
        prefix = module
        return [sid for sid in fca_index if sid.startswith(prefix)]

    nums = parts[1]

    # Check if this is a chapter-only ref with alpha suffix (e.g. "2A")
    chapter_only_match = re.fullmatch(r"(\d+[A-Za-z]?)", nums)
    if chapter_only_match and "." not in nums:
        # "SYSC 10" → chapter sysc10; "PRIN 2A" → chapter prin2a
        chapter = f"{module}{chapter_only_match.group(1).lower()}"
        return [sid for sid in fca_index if fca_index[sid]["chapter"] == chapter]

    # Has a dot — "SYSC 10.1" or "COBS 4.2"
    num_parts = nums.split(".")
    if len(num_parts) >= 2:
        # "SYSC 10.1" → section-level → match sysc10s1
        chapter_part = num_parts[0].lower()
        section_num = num_parts[1]
        section_id = f"{module}{chapter_part}s{section_num}"
        if section_id in fca_index:
            return [section_id]
        # Try partial match (e.g. 10.1 might not exist but 10.1a does)
        return [sid for sid in fca_index if sid.startswith(section_id)]

    return []


def build_policy_fca_map(policies_dir: Path, fca_index: dict[str, dict]) -> dict[str, list[str]]:
    """Map each synthetic policy to the FCA section IDs it references.

    Returns: {policy_filename: [section_id, ...]}
    """
    mapping = {}
    for policy_path in sorted(policies_dir.glob("*.md")):
        if policy_path.name.endswith(".metadata.json"):
            continue
        refs = extract_fca_refs_from_policy(policy_path)
        section_ids = []
        for ref in refs:
            section_ids.extend(fca_ref_to_section_ids(ref, fca_index))
        # Deduplicate preserving order
        seen = set()
        unique = []
        for sid in section_ids:
            if sid not in seen:
                seen.add(sid)
                unique.append(sid)
        mapping[policy_path.name] = unique
    return mapping


def select_section_pool(
    fca_index: dict[str, dict],
    policy_fca_map: dict[str, list[str]],
) -> dict:
    """Select the sections to use for Q&A generation.

    Returns a dict with:
      - policy_pairs: [(policy_filename, [fca_section_ids])] for policy+reg questions
      - fca_sections: [section_ids] for single/cross-module questions (union of
        all referenced FCA sections, plus a curated supplement)
      - module_groups: {module: [section_ids]} for cross-module pairing
    """
    # All FCA sections referenced by policies
    referenced = set()
    for sids in policy_fca_map.values():
        referenced.update(sids)

    # Curated supplementary sections — high-value sections that policies
    # reference conceptually but the regex doesn't capture, plus key sections
    # needed for module diversity in cross-module questions.
    # Only include section IDs that actually exist in the index.
    supplementary = [
        # PRIN — The Principles (all in prin2s1), plus Consumer Duty (PRIN 2A)
        "prin2as1",  # Consumer Duty: the Consumer Principle
        "prin2as2",  # Consumer Duty: cross-cutting obligations
        "prin2as3",  # Consumer Duty: products and services outcome
        "prin2as4",  # Consumer Duty: price and value outcome
        "prin2as5",  # Consumer Duty: consumer understanding outcome
        "prin2as6",  # Consumer Duty: consumer support outcome
        "prin3s1",  # Fees, charges and commission disclosure
        "prin4s1",  # Approved persons
        # COBS — Conduct of Business substantive rules
        "cobs4s1",  # Financial promotions — application
        "cobs4s2",  # Fair, clear and not misleading communications
        "cobs6s1",  # Product information — application
        "cobs9s1",  # Suitability — application
        "cobs9s2",  # Suitability — assessing suitability
        "cobs14s1",  # Client agreements — application
        "cobs15s1",  # Cancellation
        "cobs2s2",  # Acting honestly, fairly, professionally
        "cobs2s3",  # Inducements relating to MiFID business
        # SYSC — governance sections not always captured by policy refs
        "sysc8s1",  # Outsourcing — requirements
        "sysc9s1",  # Record-keeping
        "sysc18s1",  # Whistleblowing — FCA requirements
    ]
    for sid in supplementary:
        if sid in fca_index:
            referenced.add(sid)

    # Filter to core regulatory modules (skip fees, schedules, transitional provisions)
    core_modules = {
        "sysc",
        "prin",
        "cobs",
        "cass",
        "disp",
        "cocon",
        "mar",
        "fit",
        "bcobs",
        "mcob",
        "icobs",
        "prod",
        "gen",
        "tc",
        "esg",
        "cond",
    }

    core_referenced = {
        sid
        for sid in referenced
        if fca_index[sid]["module"] in core_modules
        and "sch" not in sid  # skip schedule sections
        and "tp" not in sid  # skip transitional provisions
        and "app" not in sid  # skip appendices
    }

    # Group by module for cross-module pairing
    module_groups: dict[str, list[str]] = {}
    for sid in sorted(core_referenced):
        mod = fca_index[sid]["module"]
        module_groups.setdefault(mod, []).append(sid)

    # Build policy pairs (policy + its FCA sections)
    policy_pairs = []
    for policy_name, sids in sorted(policy_fca_map.items()):
        core_sids = [s for s in sids if s in core_referenced]
        if core_sids:
            policy_pairs.append((policy_name, core_sids))

    return {
        "policy_pairs": policy_pairs,
        "fca_sections": sorted(core_referenced),
        "module_groups": module_groups,
    }


# ===================================================================
# Bedrock API helpers
# ===================================================================


def get_bedrock_client(profile: str | None = None) -> boto3.client:
    """Create a Bedrock runtime client with appropriate timeout."""
    session = boto3.Session(
        profile_name=profile or os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "eu-west-1"),
    )
    return session.client(
        "bedrock-runtime",
        config=boto3.session.Config(read_timeout=300),
    )


def _extract_text_from_converse(response: dict) -> str:
    """Extract text from a Bedrock converse API response.

    Handles different content block formats across model providers:
    - Anthropic: {"text": "..."}
    - OpenAI on Bedrock: may use {"text": "..."} or other structures
    """
    content_blocks = response["output"]["message"]["content"]
    for block in content_blocks:
        if isinstance(block, dict) and "text" in block:
            return block["text"]
    # Fallback: try to stringify whatever we got
    if content_blocks:
        block = content_blocks[0]
        if isinstance(block, str):
            return block
        # Last resort: dump the block so the caller can debug
        return json.dumps(block)
    msg = f"No text content in response: {response['output']['message']}"
    raise ValueError(msg)


def call_claude(
    client,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Call Claude Opus via Bedrock converse API. Returns the text response."""
    response = client.converse(
        modelId=GENERATOR_MODEL,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": temperature,
        },
    )
    return _extract_text_from_converse(response)


def call_critic(
    client,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """Call gpt-oss-120b via Bedrock converse API. Returns the text response."""
    response = client.converse(
        modelId=CRITIC_MODEL,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": temperature,
        },
    )
    return _extract_text_from_converse(response)


def parse_json_from_response(text: str) -> dict | list | None:
    """Extract JSON from a model response, handling markdown code fences."""
    # Try to find JSON in code fences first
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try parsing the whole thing (or the extracted block)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object or array
    for pattern in [r"(\{.*\})", r"(\[.*\])"]:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

    return None


# ===================================================================
# Q&A generation prompts and functions
# ===================================================================

SINGLE_MODULE_SYSTEM = """\
You are a UK financial regulation expert generating ground-truth Q&A pairs for \
evaluating a RAG system over the FCA Handbook.

Your task: given an FCA Handbook section, generate a question-answer pair where \
the answer can be found entirely within this single section.

Requirements:
- The question should sound natural — like a compliance officer or trainee would ask it
- The answer must be factually correct and grounded in the provided text
- Include specific rule references (e.g. "SYSC 10.1.3R") in the answer where relevant
- Assign a difficulty level: easy (single explicit fact), medium (inference or \
regulatory knowledge needed), hard (synthesis or knowing what the section doesn't say)

Respond with ONLY a JSON object (no markdown fences, no preamble):
{
  "question": "...",
  "answer": "...",
  "source_sections": ["<section_id>"],
  "difficulty": "easy|medium|hard",
  "type": "single-module"
}"""

CROSS_MODULE_SYSTEM = """\
You are a UK financial regulation expert generating ground-truth Q&A pairs for \
evaluating a RAG system over the FCA Handbook.

Your task: given TWO FCA Handbook sections from DIFFERENT modules, generate a \
question that requires information from BOTH sections to answer fully.

Requirements:
- The question must genuinely require both sections — not answerable from either alone
- Frame it as a practical compliance question spanning both regulatory domains
- The answer must cite specific provisions from both sections
- Assign difficulty: medium (two sections, straightforward synthesis) or hard \
(complex cross-module reasoning)

Respond with ONLY a JSON object (no markdown fences, no preamble):
{
  "question": "...",
  "answer": "...",
  "source_sections": ["<section_id_1>", "<section_id_2>"],
  "difficulty": "medium|hard",
  "type": "cross-module"
}"""

POLICY_REGULATION_SYSTEM = """\
You are a UK financial regulation expert generating ground-truth Q&A pairs for \
evaluating a RAG system that covers both FCA regulations and internal bank policies.

Your task: given a synthetic bank policy AND the FCA regulation it references, \
generate a question about how the policy aligns with or implements the regulation.

Requirements:
- The question should ask about the relationship between the policy and the regulation
- The answer must reference specific provisions from both the policy and the FCA section
- These are practical questions a compliance reviewer would ask
- Assign difficulty: medium (direct alignment check) or hard (gap analysis or \
interpretation needed)

Respond with ONLY a JSON object (no markdown fences, no preamble):
{
  "question": "...",
  "answer": "...",
  "source_sections": ["<fca_section_id>", "<policy_section_ref>"],
  "difficulty": "medium|hard",
  "type": "policy-regulation"
}"""


def policy_section_ref(policy_filename: str) -> str:
    """Convert a policy filename to a source_sections reference.

    'conflicts-of-interest-policy.md' → 'synthetic_conflicts-of-interest-policy'
    """
    return f"synthetic_{policy_filename.removesuffix('.md')}"


def generate_single_module_pair(
    client,
    section_id: str,
    section_text: str,
    section_name: str,
) -> dict | None:
    """Generate a single-module Q&A pair from one FCA section."""
    user_prompt = f"""\
FCA Section: {section_name} (ID: {section_id})

<section_content>
{section_text}
</section_content>

Generate a ground-truth Q&A pair based on this section."""

    try:
        response = call_claude(client, SINGLE_MODULE_SYSTEM, user_prompt)
        result = parse_json_from_response(response)
        if result and isinstance(result, dict):
            result["source_sections"] = [section_id]
            result["type"] = "single-module"
            return result
    except Exception as e:
        logger.warning(f"Failed to generate single-module pair for {section_id}: {e}")

    return None


def generate_cross_module_pair(
    client,
    section_a_id: str,
    section_a_text: str,
    section_a_name: str,
    section_b_id: str,
    section_b_text: str,
    section_b_name: str,
) -> dict | None:
    """Generate a cross-module Q&A pair from two FCA sections."""
    user_prompt = f"""\
Section A: {section_a_name} (ID: {section_a_id})

<section_a_content>
{section_a_text}
</section_a_content>

Section B: {section_b_name} (ID: {section_b_id})

<section_b_content>
{section_b_text}
</section_b_content>

Generate a ground-truth Q&A pair that requires BOTH sections to answer."""

    try:
        response = call_claude(client, CROSS_MODULE_SYSTEM, user_prompt)
        result = parse_json_from_response(response)
        if result and isinstance(result, dict):
            result["source_sections"] = [section_a_id, section_b_id]
            result["type"] = "cross-module"
            return result
    except Exception as e:
        logger.warning(
            f"Failed to generate cross-module pair for {section_a_id}+{section_b_id}: {e}"
        )

    return None


def generate_policy_regulation_pair(
    client,
    policy_filename: str,
    policy_text: str,
    fca_section_id: str,
    fca_section_text: str,
    fca_section_name: str,
) -> dict | None:
    """Generate a policy+regulation Q&A pair."""
    # Truncate policy if very long — keep first 4000 chars to stay within context
    if len(policy_text) > 6000:
        policy_text = policy_text[:6000] + "\n\n[... truncated for context ...]"

    user_prompt = f"""\
Bank Policy: {policy_filename}

<policy_content>
{policy_text}
</policy_content>

FCA Regulation: {fca_section_name} (ID: {fca_section_id})

<regulation_content>
{fca_section_text}
</regulation_content>

Generate a Q&A pair about how the bank policy aligns with or implements this regulation."""

    policy_ref = policy_section_ref(policy_filename)

    try:
        response = call_claude(client, POLICY_REGULATION_SYSTEM, user_prompt)
        result = parse_json_from_response(response)
        if result and isinstance(result, dict):
            result["source_sections"] = [fca_section_id, policy_ref]
            result["type"] = "policy-regulation"
            return result
    except Exception as e:
        logger.warning(
            f"Failed to generate policy-reg pair for {policy_filename}+{fca_section_id}: {e}"
        )

    return None


# ===================================================================
# Critic validation
# ===================================================================

CRITIC_SYSTEM = """\
You are a validation expert reviewing Q&A pairs generated for a RAG evaluation \
benchmark over UK FCA regulations and bank policies.

For each Q&A pair, you will be given:
1. The question and answer
2. The source material the answer should be grounded in

Evaluate the pair on three criteria:
- CORRECTNESS: Is the answer factually accurate given the source material?
- SOURCE_COMPLETENESS: Does the source_sections list include all sections needed \
to answer the question? Are any missing?
- DIFFICULTY_RATING: Is the assigned difficulty (easy/medium/hard) reasonable?

Respond with ONLY a JSON object (no markdown fences, no preamble):
{
  "verdict": "pass|fail",
  "correctness": "pass|fail",
  "correctness_reasoning": "...",
  "source_completeness": "pass|fail",
  "source_completeness_reasoning": "...",
  "difficulty_reasonable": true|false,
  "suggested_difficulty": "easy|medium|hard",
  "overall_reasoning": "..."
}"""


def validate_pair(
    client,
    pair: dict,
    source_texts: dict[str, str],
) -> dict | None:
    """Run critic validation on a single Q&A pair."""
    # Build source material block
    source_blocks = []
    for sid in pair.get("source_sections", []):
        text = source_texts.get(sid, "[Source text not available]")
        source_blocks.append(f"### {sid}\n{text}")

    sources_combined = "\n\n---\n\n".join(source_blocks)

    user_prompt = f"""\
Q&A pair to validate:

Question: {pair["question"]}
Answer: {pair["answer"]}
Source sections: {pair["source_sections"]}
Difficulty: {pair.get("difficulty", "unknown")}
Type: {pair.get("type", "unknown")}

Source material:

{sources_combined}"""

    try:
        response = call_critic(client, CRITIC_SYSTEM, user_prompt)
        result = parse_json_from_response(response)
        if result and isinstance(result, dict):
            return result
    except Exception as e:
        logger.warning(f"Critic validation failed for {pair.get('question_id', '?')}: {e}")

    return None


# ===================================================================
# Cross-module pairing strategy
# ===================================================================


def generate_cross_module_pairings(
    module_groups: dict[str, list[str]],
    target_count: int,
) -> list[tuple[str, str]]:
    """Generate pairs of section IDs from different modules for cross-module questions.

    Prioritises pairings between modules that naturally interact:
    - SYSC + PRIN (governance + principles)
    - COBS + PRIN (conduct + principles)
    - SYSC + COBS (governance + conduct)
    - CASS + COBS (client assets + conduct)
    - MAR + SYSC (market abuse + systems/controls)
    - COCON + SYSC (individual conduct + organisational systems)
    - PROD + COBS (product governance + conduct)
    - DISP + COBS (complaints + conduct)
    """
    priority_pairs = [
        ("sysc", "prin"),
        ("cobs", "prin"),
        ("sysc", "cobs"),
        ("cass", "cobs"),
        ("mar", "sysc"),
        ("cocon", "sysc"),
        ("prod", "cobs"),
        ("disp", "cobs"),
        ("sysc", "mar"),
        ("cass", "sysc"),
        ("prod", "prin"),
        ("fit", "sysc"),
        ("bcobs", "prin"),
        ("icobs", "cobs"),
    ]

    pairings = []
    for mod_a, mod_b in priority_pairs:
        if mod_a not in module_groups or mod_b not in module_groups:
            continue
        sections_a = module_groups[mod_a]
        sections_b = module_groups[mod_b]

        # Take up to 3 pairings per module combination
        count = 0
        for sa in sections_a:
            if count >= 3 or len(pairings) >= target_count:
                break
            for sb in sections_b:
                if count >= 3 or len(pairings) >= target_count:
                    break
                pairings.append((sa, sb))
                count += 1

        if len(pairings) >= target_count:
            break

    return pairings[:target_count]


# ===================================================================
# Main pipeline
# ===================================================================


def run_generation(
    client,
    pool: dict,
    fca_index: dict[str, dict],
) -> list[dict]:
    """Run all three generation passes. Returns list of candidate Q&A pairs."""
    candidates = []
    question_counter = 1

    # --- Single-module questions ---
    logger.info(f"Generating ~{TARGET_SINGLE} single-module questions...")
    sections_for_single = pool["fca_sections"][:TARGET_SINGLE]

    for section_id in sections_for_single:
        info = fca_index[section_id]
        section_text = info["path"].read_text()

        # Skip very short sections (< 200 chars) — not enough substance
        if len(section_text) < 200:
            logger.debug(f"Skipping {section_id} — too short ({len(section_text)} chars)")
            continue

        pair = generate_single_module_pair(client, section_id, section_text, info["section_name"])
        if pair:
            pair["question_id"] = f"q{question_counter:03d}"
            candidates.append(pair)
            logger.info(
                f"  [{question_counter}] single-module: {section_id} — "
                f"{pair['difficulty']} — {pair['question'][:80]}..."
            )
            question_counter += 1

        time.sleep(API_DELAY)

    # --- Cross-module questions ---
    logger.info(f"Generating ~{TARGET_CROSS} cross-module questions...")
    pairings = generate_cross_module_pairings(pool["module_groups"], TARGET_CROSS)

    for section_a_id, section_b_id in pairings:
        info_a = fca_index[section_a_id]
        info_b = fca_index[section_b_id]
        text_a = info_a["path"].read_text()
        text_b = info_b["path"].read_text()

        # Truncate each section if very long to fit in context
        if len(text_a) > 5000:
            text_a = text_a[:5000] + "\n\n[... truncated ...]"
        if len(text_b) > 5000:
            text_b = text_b[:5000] + "\n\n[... truncated ...]"

        pair = generate_cross_module_pair(
            client,
            section_a_id,
            text_a,
            info_a["section_name"],
            section_b_id,
            text_b,
            info_b["section_name"],
        )
        if pair:
            pair["question_id"] = f"q{question_counter:03d}"
            candidates.append(pair)
            logger.info(
                f"  [{question_counter}] cross-module: {section_a_id}+{section_b_id} — "
                f"{pair['difficulty']} — {pair['question'][:80]}..."
            )
            question_counter += 1

        time.sleep(API_DELAY)

    # --- Policy + regulation questions ---
    logger.info(f"Generating ~{TARGET_POLICY} policy+regulation questions...")
    policy_count = 0

    for policy_filename, fca_section_ids in pool["policy_pairs"]:
        if policy_count >= TARGET_POLICY:
            break

        policy_path = POLICIES_DIR / policy_filename
        policy_text = policy_path.read_text()

        # Generate one question per policy, using the first substantive FCA section
        for fca_sid in fca_section_ids[:2]:  # Try up to 2 sections per policy
            if policy_count >= TARGET_POLICY:
                break

            info = fca_index[fca_sid]
            fca_text = info["path"].read_text()

            if len(fca_text) < 200:
                continue

            pair = generate_policy_regulation_pair(
                client,
                policy_filename,
                policy_text,
                fca_sid,
                fca_text,
                info["section_name"],
            )
            if pair:
                pair["question_id"] = f"q{question_counter:03d}"
                candidates.append(pair)
                logger.info(
                    f"  [{question_counter}] policy-reg: {policy_filename}+{fca_sid} — "
                    f"{pair['difficulty']} — {pair['question'][:80]}..."
                )
                question_counter += 1
                policy_count += 1

            time.sleep(API_DELAY)

    return candidates


def run_validation(
    client,
    candidates: list[dict],
    fca_index: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """Validate all candidates with the critic model.

    Returns (passed, failed) lists.
    """
    logger.info(f"Validating {len(candidates)} candidates with critic model...")

    # Build a source text lookup
    source_texts = {}
    for info in fca_index.values():
        source_texts[info["section_id"]] = info["path"].read_text()

    # Add synthetic policy texts
    for policy_path in POLICIES_DIR.glob("*.md"):
        if policy_path.name.endswith(".metadata.json"):
            continue
        ref = policy_section_ref(policy_path.name)
        source_texts[ref] = policy_path.read_text()

    passed = []
    failed = []

    for i, pair in enumerate(candidates):
        logger.info(f"  Validating [{i + 1}/{len(candidates)}] {pair['question_id']}...")

        result = validate_pair(client, pair, source_texts)
        if result:
            pair["critic_result"] = result
            if result.get("verdict") == "pass":
                # Apply critic's difficulty suggestion if it differs
                if (
                    result.get("suggested_difficulty")
                    and result["suggested_difficulty"] != pair.get("difficulty")
                    and not result.get("difficulty_reasonable", True)
                ):
                    pair["difficulty_original"] = pair["difficulty"]
                    pair["difficulty"] = result["suggested_difficulty"]
                passed.append(pair)
            else:
                failed.append(pair)
        else:
            # If critic call fails, keep the pair but flag it
            pair["critic_result"] = {"verdict": "error", "overall_reasoning": "Critic call failed"}
            passed.append(pair)  # Keep — human can review

        time.sleep(API_DELAY)

    return passed, failed


# ===================================================================
# Output
# ===================================================================


def write_outputs(
    passed: list[dict],
    failed: list[dict],
    all_candidates: list[dict],
) -> None:
    """Write Q&A pairs and candidates to data/qa/."""
    QA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Clean output: remove critic_result from the final pairs, keep it in candidates
    clean_pairs = []
    for pair in passed:
        clean = {
            "question_id": pair["question_id"],
            "question": pair["question"],
            "answer": pair["answer"],
            "source_sections": pair["source_sections"],
            "difficulty": pair["difficulty"],
            "type": pair["type"],
        }
        if "difficulty_original" in pair:
            clean["difficulty_original"] = pair["difficulty_original"]
        clean_pairs.append(clean)

    # Renumber question IDs sequentially in the final set
    for i, pair in enumerate(clean_pairs):
        pair["question_id"] = f"q{i + 1:03d}"

    qa_path = QA_OUTPUT_DIR / "qa_pairs.json"
    qa_path.write_text(json.dumps(clean_pairs, indent=2))
    logger.info(f"Wrote {len(clean_pairs)} curated Q&A pairs to {qa_path}")

    # Write full candidates (with critic results) for reproducibility
    candidates_path = QA_OUTPUT_DIR / "qa_candidates.json"
    candidates_path.write_text(json.dumps(all_candidates, indent=2))
    logger.info(f"Wrote {len(all_candidates)} total candidates to {candidates_path}")

    # Write failed pairs for review
    if failed:
        failed_path = QA_OUTPUT_DIR / "qa_failed.json"
        failed_path.write_text(json.dumps(failed, indent=2))
        logger.info(f"Wrote {len(failed)} failed pairs to {failed_path}")

    # Summary stats
    type_counts = {}
    diff_counts = {}
    for pair in clean_pairs:
        type_counts[pair["type"]] = type_counts.get(pair["type"], 0) + 1
        diff_counts[pair["difficulty"]] = diff_counts.get(pair["difficulty"], 0) + 1

    print(f"\n{'=' * 60}")
    print("Q&A Generation Summary")
    print(f"{'=' * 60}")
    print(f"Total candidates generated: {len(all_candidates)}")
    print(f"Passed validation:          {len(clean_pairs)}")
    print(f"Failed validation:          {len(failed)}")
    print("\nBy type:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t:25s} {c:4d}")
    print("\nBy difficulty:")
    for d, c in sorted(diff_counts.items()):
        print(f"  {d:25s} {c:4d}")
    print(f"{'=' * 60}\n")


# ===================================================================
# Prompt saving
# ===================================================================


def save_prompts() -> None:
    """Save generation and validation prompts for reproducibility."""
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    prompts = {
        "single_module_system": SINGLE_MODULE_SYSTEM,
        "cross_module_system": CROSS_MODULE_SYSTEM,
        "policy_regulation_system": POLICY_REGULATION_SYSTEM,
        "critic_system": CRITIC_SYSTEM,
        "generator_model": GENERATOR_MODEL,
        "critic_model": CRITIC_MODEL,
    }

    prompt_path = PROMPTS_DIR / "qa_generation_prompts.json"
    prompt_path.write_text(json.dumps(prompts, indent=2))
    logger.info(f"Saved generation prompts to {prompt_path}")


# ===================================================================
# CLI
# ===================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ground-truth Q&A pairs")
    parser.add_argument("--dry-run", action="store_true", help="Show section pool, no generation")
    parser.add_argument("--skip-validation", action="store_true", help="Skip critic validation")
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Generate candidates and save, but skip validation",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Load existing candidates from data/qa/qa_candidates.json and run critic",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="AWS profile name (overrides AWS_PROFILE in .env)",
    )
    parser.add_argument(
        "--test-models",
        action="store_true",
        help="Make one test call to each model (generator + critic) and exit",
    )
    args = parser.parse_args()

    load_dotenv()

    # --- Test models mode ---
    if args.test_models:
        profile = args.profile or os.getenv("AWS_PROFILE")
        client = get_bedrock_client(profile)
        test_prompt = "Reply with exactly: OK"

        print(f"Testing generator model: {GENERATOR_MODEL}")
        try:
            result = call_claude(client, "You are a test.", test_prompt, max_tokens=16)
            print(f"  ✓ Response: {result.strip()}")
        except Exception as e:
            print(f"  ✗ Failed: {e}")

        print(f"Testing critic model: {CRITIC_MODEL}")
        try:
            response = client.converse(
                modelId=CRITIC_MODEL,
                system=[{"text": "You are a test."}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": test_prompt}],
                    }
                ],
                inferenceConfig={"maxTokens": 16, "temperature": 0.3},
            )
            print(f"  Raw response keys: {list(response.keys())}")
            msg = response.get("output", {}).get("message", {})
            print(f"  Message content: {msg.get('content', 'MISSING')}")
            result = _extract_text_from_converse(response)
            print(f"  ✓ Extracted: {result.strip()}")
        except Exception as e:
            print(f"  ✗ Failed: {type(e).__name__}: {e}")

        return

    # --- Build section pool ---
    if not FCA_SECTIONS_DIR.exists():
        logger.error(
            f"FCA sections not found at {FCA_SECTIONS_DIR} — run convert_fca_to_sections.py first"
        )
        raise SystemExit(1)

    logger.info("Building FCA section index...")
    fca_index = load_fca_section_index(FCA_SECTIONS_DIR)
    logger.info(f"  {len(fca_index)} FCA sections indexed")

    # --- Validate-only mode: load existing candidates, run critic ---
    if args.validate_only:
        candidates_path = QA_OUTPUT_DIR / "qa_candidates.json"
        if not candidates_path.exists():
            logger.error(f"No candidates found at {candidates_path} — run --generate-only first")
            raise SystemExit(1)

        candidates = json.loads(candidates_path.read_text())
        logger.info(f"Loaded {len(candidates)} candidates from {candidates_path}")

        profile = args.profile or os.getenv("AWS_PROFILE")
        client = get_bedrock_client(profile)
        passed, failed = run_validation(client, candidates, fca_index)
        write_outputs(passed, failed, candidates)
        return

    logger.info("Extracting FCA references from synthetic policies...")
    policy_fca_map = build_policy_fca_map(POLICIES_DIR, fca_index)
    for policy, sids in policy_fca_map.items():
        logger.info(f"  {policy}: {len(sids)} FCA sections")

    pool = select_section_pool(fca_index, policy_fca_map)

    if args.dry_run:
        print(f"\n{'=' * 60}")
        print("Section Pool Summary (dry run)")
        print(f"{'=' * 60}")
        print(f"\nTotal FCA sections in pool: {len(pool['fca_sections'])}")
        print(f"Policy-FCA pairings:        {len(pool['policy_pairs'])}")
        print("\nModules represented:")
        for mod, sids in sorted(pool["module_groups"].items()):
            print(f"  {mod.upper():12s} {len(sids):4d} sections")
        print("\nCross-module pairings available:")
        pairings = generate_cross_module_pairings(pool["module_groups"], TARGET_CROSS)
        for sa, sb in pairings[:10]:
            print(f"  {sa} + {sb}")
        if len(pairings) > 10:
            print(f"  ... and {len(pairings) - 10} more")
        print("\nPolicy pairings:")
        for policy, sids in pool["policy_pairs"][:10]:
            print(f"  {policy}: {sids[:3]}{'...' if len(sids) > 3 else ''}")
        if len(pool["policy_pairs"]) > 10:
            print(f"  ... and {len(pool['policy_pairs']) - 10} more")
        print(f"{'=' * 60}\n")
        return

    # --- Generate ---
    profile = args.profile or os.getenv("AWS_PROFILE")
    client = get_bedrock_client(profile)

    candidates = run_generation(client, pool, fca_index)
    logger.info(f"Generated {len(candidates)} candidate Q&A pairs")

    # Save prompts
    save_prompts()

    if args.generate_only:
        # Save candidates without validation
        QA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        candidates_path = QA_OUTPUT_DIR / "qa_candidates.json"
        candidates_path.write_text(json.dumps(candidates, indent=2))
        logger.info(f"Wrote {len(candidates)} candidates to {candidates_path}")
        return

    # --- Validate ---
    if args.skip_validation:
        write_outputs(candidates, [], candidates)
    else:
        passed, failed = run_validation(client, candidates, fca_index)
        write_outputs(passed, failed, candidates)


if __name__ == "__main__":
    main()
