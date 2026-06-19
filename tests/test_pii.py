"""Tests for src.pii -- reversible, session-scoped PII redaction.

These exercise the redaction module in isolation: no app, no pipeline, no AWS.
``redact`` / ``rehydrate`` are pure over an injected Vault, so referential
integrity, validator behaviour, and round-trip fidelity all test cleanly without
the web layer.

Run from the repo root:
    uv run pytest tests/test_pii.py -v
"""

from __future__ import annotations

import pytest

from src.pii import (
    Detection,
    RedactionResult,
    Vault,
    _looks_like_name,
    _luhn_ok,
    _nino_ok,
    redact,
    rehydrate,
)

# ---------------------------------------------------------------------------
# Vault: token allocation and referential integrity
# ---------------------------------------------------------------------------


def test_token_for_allocates_first_token():
    vault = Vault()
    assert vault.token_for("NAME", "Jane Doe") == "[NAME_1]"


def test_token_for_is_stable_for_same_value():
    vault = Vault()
    first = vault.token_for("NAME", "Jane Doe")
    second = vault.token_for("NAME", "Jane Doe")
    assert first == second == "[NAME_1]"


def test_token_for_casefolds_the_key():
    vault = Vault()
    assert vault.token_for("NAME", "Jane Doe") == vault.token_for("NAME", "jane doe")


def test_token_for_increments_within_a_type():
    vault = Vault()
    assert vault.token_for("NAME", "Jane Doe") == "[NAME_1]"
    assert vault.token_for("NAME", "John Roe") == "[NAME_2]"


def test_token_for_counters_are_per_type():
    vault = Vault()
    assert vault.token_for("NAME", "Jane Doe") == "[NAME_1]"
    assert vault.token_for("EMAIL", "a@b.com") == "[EMAIL_1]"


def test_token_for_populates_reverse_map():
    vault = Vault()
    token = vault.token_for("EMAIL", "a@b.com")
    assert vault.token_to_value[token] == "a@b.com"


# ---------------------------------------------------------------------------
# Detection of each entity type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, entity_type",
    [
        ("Contact john.smith@example.com please", "EMAIL"),
        ("NI number AB123456C on file", "NINO"),
        ("card 4242 4242 4242 4242 ending", "CARD"),
        ("sort code 12-34-56 for transfer", "SORT_CODE"),
        ("call them on 07911 123456 today", "PHONE"),
        ("they live at SW1A 1AA now", "POSTCODE"),
        ("the property at 10 Downing Street is", "ADDRESS"),
        ("Mr John Smith complained", "NAME"),
        ("Jane Doe was unhappy", "NAME"),
    ],
)
def test_detects_entity_type(text, entity_type):
    result = redact(text, Vault())
    assert entity_type in {d.entity_type for d in result.detections}


def test_account_number_is_keyword_anchored():
    # Only the digits are masked; the 'account number' anchor text is preserved.
    result = redact("Please refund account number 12345678 today", Vault())
    assert "ACCOUNT" in {d.entity_type for d in result.detections}
    assert "account number" in result.clean_text
    assert "12345678" not in result.clean_text


def test_bare_eight_digits_not_masked_as_account():
    # Without the anchor, an 8-digit run is too common to mask blindly.
    result = redact("reference 12345678 attached", Vault())
    assert "ACCOUNT" not in {d.entity_type for d in result.detections}
    assert "12345678" in result.clean_text


# ---------------------------------------------------------------------------
# Validators veto false positives
# ---------------------------------------------------------------------------


def test_luhn_valid_and_invalid():
    assert _luhn_ok("4242424242424242")
    assert not _luhn_ok("1234567890123456")


def test_card_failing_luhn_is_not_masked():
    result = redact("number 1234 5678 9012 3456 here", Vault())
    assert "CARD" not in {d.entity_type for d in result.detections}


def test_nino_valid():
    assert _nino_ok("AB123456C")


@pytest.mark.parametrize(
    "bad",
    [
        "DA123456C",  # invalid first letter (D)
        "AO123456C",  # invalid second letter (O)
        "BG123456C",  # disallowed prefix
        "AB12345C",  # too few digits
        "AB1234567C",  # too many digits
        "AB123456E",  # suffix outside A-D
    ],
)
def test_nino_invalid(bad):
    assert not _nino_ok(bad)


def test_invalid_nino_is_not_masked():
    result = redact("ref DA123456C noted", Vault())
    assert "NINO" not in {d.entity_type for d in result.detections}


# ---------------------------------------------------------------------------
# Name detection: stop-word guard
# ---------------------------------------------------------------------------


def test_looks_like_name_rejects_domain_phrases():
    assert not _looks_like_name("Financial Ombudsman")
    assert not _looks_like_name("Kind Regards")


def test_looks_like_name_accepts_a_plausible_name():
    assert _looks_like_name("Jane Doe")


def test_domain_phrase_not_masked_as_name():
    result = redact("The Financial Ombudsman replied", Vault())
    assert "NAME" not in {d.entity_type for d in result.detections}
    assert "Financial Ombudsman" in result.clean_text


def test_title_prefixed_name_is_masked():
    result = redact("complaint from Mr John Smith", Vault())
    assert "[NAME_1]" in result.clean_text
    assert "John Smith" not in result.clean_text


# ---------------------------------------------------------------------------
# Referential integrity across a redaction and across turns
# ---------------------------------------------------------------------------


def test_same_value_reuses_one_token_within_a_pass():
    result = redact("Jane Doe and Jane Doe again", Vault())
    assert result.clean_text.count("[NAME_1]") == 2
    assert "[NAME_2]" not in result.clean_text


def test_distinct_values_get_distinct_tokens():
    result = redact("Jane Doe met John Roe", Vault())
    assert "[NAME_1]" in result.clean_text
    assert "[NAME_2]" in result.clean_text


def test_token_is_stable_across_turns_with_one_vault():
    vault = Vault()
    redact("Jane Doe", vault)
    second = redact("Jane Doe again", vault)
    # The session Vault persists, so the same person keeps the same token.
    assert "[NAME_1]" in second.clean_text


# ---------------------------------------------------------------------------
# Round-trip fidelity: redact -> rehydrate restores the original
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Email john.smith@example.com now",
        "complaint from Mr John Smith",
        "card 4242 4242 4242 4242 ending",
        "account number 12345678 please",
        "they live at SW1A 1AA now",
    ],
)
def test_redact_then_rehydrate_round_trips(text):
    vault = Vault()
    result = redact(text, vault)
    assert rehydrate(result.clean_text, vault) == text


def test_rehydrate_leaves_unknown_tokens_untouched():
    assert rehydrate("[NAME_99] hello", Vault()) == "[NAME_99] hello"


# ---------------------------------------------------------------------------
# Idempotence and edge cases
# ---------------------------------------------------------------------------


def test_redacting_existing_tokens_is_a_no_op():
    result = redact("[NAME_1] emailed [EMAIL_1]", Vault())
    assert result.clean_text == "[NAME_1] emailed [EMAIL_1]"
    assert result.detections == []


def test_empty_input():
    result = redact("", Vault())
    assert result.clean_text == ""
    assert result.detections == []


def test_rehydrate_empty_input():
    assert rehydrate("", Vault()) == ""


def test_no_pii_text_is_unchanged():
    text = "DISP 1.6.2R sets the eight-week response clock."
    result = redact(text, Vault())
    assert result.clean_text == text
    assert result.detections == []


# ---------------------------------------------------------------------------
# Detections never carry raw values
# ---------------------------------------------------------------------------


def test_detection_carries_no_raw_value():
    result = redact("email john.smith@example.com", Vault())
    assert result.detections, "expected at least one detection"
    for detection in result.detections:
        dumped = detection.model_dump()
        assert set(dumped) == {"entity_type", "count"}
        assert "john.smith@example.com" not in str(dumped)


def test_multiple_entities_counted_separately():
    text = "Mr John Smith, email john@x.com, postcode SW1A 1AA"
    result = redact(text, Vault())
    counts = {d.entity_type: d.count for d in result.detections}
    assert counts.get("NAME") == 1
    assert counts.get("EMAIL") == 1
    assert counts.get("POSTCODE") == 1


def test_redaction_result_model_shape():
    result = redact("Jane Doe", Vault())
    assert isinstance(result, RedactionResult)
    assert all(isinstance(d, Detection) for d in result.detections)
