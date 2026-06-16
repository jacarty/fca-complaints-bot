"""Tests for src.provisions — the FCA provision reference normaliser.

The discriminating cases are the ones the JAM-274 requirement turns on:

- letter-ending identifiers keep their amendment letters while losing the status
  suffix (1.5.2AR -> 1.5.2A, 1.3.1AAR -> 1.3.1AA);
- the bare amendment letter D is not mistaken for a status suffix
  (1.3.1AD stays 1.3.1AD, while 1.3.1ADR -> 1.3.1AD);
- the same rule written with and without a suffix collapses to one key
  (1.2.1 == 1.2.1R), which is what lets the citation metric match the ground
  truth's own inconsistent spellings.
"""

import pytest

from src.provisions import (
    normalise_provision,
    normalise_provisions,
    parse_provision,
)

# ---------------------------------------------------------------------------
# Discriminating cases — explicit expected keys
# ---------------------------------------------------------------------------

EXPECTED = {
    # plain rule/guidance suffix stripped
    "DISP 1.2.1R": "DISP 1.2.1",
    "DISP 1.2.3G": "DISP 1.2.3",
    "DISP 1.6.2R": "DISP 1.6.2",
    # no suffix — unchanged
    "DISP 1.2.1": "DISP 1.2.1",
    "DISP 1.7.1": "DISP 1.7.1",
    "DISP 1.8.1": "DISP 1.8.1",
    "DISP 1.3.3": "DISP 1.3.3",
    "DISP 1.5.4": "DISP 1.5.4",
    "DISP 1.5.5": "DISP 1.5.5",
    # single amendment letter + suffix -> keep letter, drop suffix
    "DISP 1.3.1AR": "DISP 1.3.1A",
    "DISP 1.5.2AR": "DISP 1.5.2A",
    "DISP 1.5.2AG": "DISP 1.5.2A",
    "DISP 1.5.2A": "DISP 1.5.2A",
    # double amendment letters + suffix
    "DISP 1.3.1AAR": "DISP 1.3.1AA",
    "DISP 1.3.1ABR": "DISP 1.3.1AB",
    "DISP 1.3.1AA": "DISP 1.3.1AA",
    "DISP 1.3.1AB": "DISP 1.3.1AB",
    # the AD case — D must survive whether or not a suffix follows
    "DISP 1.3.1ADR": "DISP 1.3.1AD",
    "DISP 1.3.1AD": "DISP 1.3.1AD",
    # amendment letter B
    "MCOB 12.4.1BR": "MCOB 12.4.1B",
    "MCOB 12.4.1A": "MCOB 12.4.1A",
    "MCOB 12.4.1R": "MCOB 12.4.1",
    # chapter/section letters must not be touched (the A belongs to "2A"/"10A"/"5A")
    "BCOBS 2A.1.1": "BCOBS 2A.1.1",
    "BCOBS 2A.1.2": "BCOBS 2A.1.2",
    "COBS 10A.2.1R": "COBS 10A.2.1",
    "COBS 10A.3.1": "COBS 10A.3.1",
    "CONC 2.5A.2": "CONC 2.5A.2",
    "PRIN 2A.10.1": "PRIN 2A.10.1",
    "PRIN 2A.2.10": "PRIN 2A.2.10",
    "PRIN 2A.2.1R": "PRIN 2A.2.1",
    # multi-section modules
    "DISP 2.8.2R": "DISP 2.8.2",
    "DISP 2.7.1R": "DISP 2.7.1",
    "DISP 2.2.1G": "DISP 2.2.1",
}


@pytest.mark.parametrize("ref,key", EXPECTED.items())
def test_expected_keys(ref, key):
    assert normalise_provision(ref) == key


# ---------------------------------------------------------------------------
# Equivalence groups — references that must collapse to the same key
# ---------------------------------------------------------------------------

EQUIVALENCE_GROUPS = [
    ("DISP 1.2.1", "DISP 1.2.1R"),               # c008 vs c001/c015/c022
    ("DISP 1.7.1", "DISP 1.7.1R"),               # c013 vs c006
    ("DISP 1.8.1", "DISP 1.8.1R"),               # c014 vs c007/c021
    ("DISP 1.3.3", "DISP 1.3.3R"),               # c016 vs c009
    ("DISP 1.5.4", "DISP 1.5.4R"),               # c032 vs c004/c011/c018
    ("DISP 1.5.5", "DISP 1.5.5G"),               # c032 vs c004/c018/c025
    ("DISP 1.5.2A", "DISP 1.5.2AR", "DISP 1.5.2AG"),  # c032 vs c004/c011 vs c025
    ("DISP 1.3.1AA", "DISP 1.3.1AAR"),           # c016 vs c009/c023
    ("DISP 1.3.1AB", "DISP 1.3.1ABR"),
    ("DISP 1.3.1AD", "DISP 1.3.1ADR"),
]


@pytest.mark.parametrize("group", EQUIVALENCE_GROUPS)
def test_equivalence_groups(group):
    keys = {normalise_provision(ref) for ref in group}
    assert len(keys) == 1, f"{group} did not collapse: {keys}"


def test_ad_does_not_collapse_into_a():
    # The AD pair must stay distinct from the A / AAR keys.
    assert normalise_provision("DISP 1.3.1AD") != normalise_provision("DISP 1.3.1A")
    assert normalise_provision("DISP 1.3.1AD") != normalise_provision("DISP 1.3.1AA")


# ---------------------------------------------------------------------------
# Sub-paragraphs, casing, whitespace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref,key",
    [
        ("DISP 1.6.2R(1)", "DISP 1.6.2"),
        ("DISP 1.3.1AR(2)", "DISP 1.3.1A"),
        ("DISP 1.6.2R(2)(a)", "DISP 1.6.2"),
        ("disp 1.6.2r", "DISP 1.6.2"),
        ("  DISP   1.6.2 R  ", "DISP 1.6.2"),
        ("DISP\t1.6.2R", "DISP 1.6.2"),
    ],
)
def test_subpara_case_whitespace(ref, key):
    assert normalise_provision(ref) == key


# ---------------------------------------------------------------------------
# Unparseable input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ref", [None, "", "   ", "not a provision", "DISP", "123", "DISP APP 1.1.1"])
def test_unparseable_returns_none(ref):
    assert normalise_provision(ref) is None


# ---------------------------------------------------------------------------
# Whole-universe properties: every ground-truth provision parses, and the
# normaliser is idempotent. This is the guard that would catch a future
# reference the {R,G,E} assumption mishandles.
# ---------------------------------------------------------------------------

# Distinct expected_provisions across all 35 JAM-274 scenarios.
UNIVERSE = [
    "DISP 1.2.1R", "DISP 1.2.3G", "DISP 1.2.4G", "DISP 1.2.1",
    "DISP 1.3.1R", "DISP 1.3.1AR", "DISP 1.3.1AAR", "DISP 1.3.1ABR", "DISP 1.3.1ADR",
    "DISP 1.3.3R", "DISP 1.3.1", "DISP 1.3.1AA", "DISP 1.3.1AB", "DISP 1.3.1AD", "DISP 1.3.3",
    "DISP 1.4.1R", "DISP 1.4.2G", "DISP 1.4.3G", "DISP 1.4.6G",
    "DISP 1.5.1R", "DISP 1.5.2AR", "DISP 1.5.2AG", "DISP 1.5.4R", "DISP 1.5.5G", "DISP 1.5.6G",
    "DISP 1.5.2A", "DISP 1.5.4", "DISP 1.5.5",
    "DISP 1.6.1R", "DISP 1.6.2R",
    "DISP 1.7.1R", "DISP 1.7.1", "DISP 1.7.2", "DISP 1.7.3",
    "DISP 1.8.1R", "DISP 1.8.1",
    "DISP 2.2.1G", "DISP 2.7.1R", "DISP 2.7.6R", "DISP 2.8.2R", "DISP 2.8.3G", "DISP 2.8.4G",
    "BCOBS 2A.1.1", "BCOBS 2A.1.2", "BCOBS 2.2.1", "BCOBS 2.2.5",
    "BCOBS 2.3.1", "BCOBS 2.3.5", "BCOBS 2.3.7", "BCOBS 2.4.2G",
    "CONC 11.1.1R", "CONC 11.1.2R", "CONC 11.1.5R", "CONC 11.2.3R", "CONC 11.2.4R",
    "CONC 2.10.1G", "CONC 2.10.4G", "CONC 2.10.5G", "CONC 2.10.6G", "CONC 2.10.7G", "CONC 2.10.8G",
    "CONC 2.5A.2", "CONC 2.5A.5", "CONC 2.5A.6",
    "COBS 10A.2.1R", "COBS 10A.2.3R", "COBS 10A.2.4R", "COBS 10A.2.5R",
    "COBS 10A.3.1", "COBS 10A.4.1R", "COBS 10A.4.2R",
    "PRIN 2A.10.1", "PRIN 2A.10.2", "PRIN 2A.10.5",
    "PRIN 2A.2.1R", "PRIN 2A.2.2R", "PRIN 2A.2.5R", "PRIN 2A.2.8R", "PRIN 2A.2.10",
    "PRIN 2A.3.4R", "PRIN 2A.3.7R", "PRIN 2A.3.8R",
    "MCOB 12.3.1R", "MCOB 12.3.4R", "MCOB 12.4.1R", "MCOB 12.4.1A", "MCOB 12.4.1BR",
    "MCOB 12.4.4R", "MCOB 12.5.1R", "MCOB 12.5.2R", "MCOB 12.5.3G", "MCOB 12.5.5R",
    "ICOBS 2.1.1G", "ICOBS 2.1.2R", "ICOBS 2.1.3G", "ICOBS 2.1.4G",
    "ICOBS 2.2.2R", "ICOBS 2.2.4G", "ICOBS 2.3.1G",
]


@pytest.mark.parametrize("ref", UNIVERSE)
def test_universe_parses(ref):
    assert normalise_provision(ref) is not None


@pytest.mark.parametrize("ref", UNIVERSE)
def test_idempotent(ref):
    once = normalise_provision(ref)
    assert normalise_provision(once) == once


def test_no_suffix_only_keys_lost_letters():
    # Every key keeps its module and at least chapter.section.paragraph.
    for ref in UNIVERSE:
        key = normalise_provision(ref)
        module, _, core = key.partition(" ")
        assert module.isalpha()
        assert core[0].isdigit()


# ---------------------------------------------------------------------------
# parse_provision diagnostics
# ---------------------------------------------------------------------------


def test_parse_exposes_status():
    assert parse_provision("DISP 1.6.2R").status == "R"
    assert parse_provision("DISP 1.5.5G").status == "G"
    assert parse_provision("DISP 1.5.2A").status is None
    assert parse_provision("DISP 1.3.1AD").status is None  # D is not a status suffix


def test_normalise_provisions_set_drops_unparseable():
    refs = ["DISP 1.2.1R", "DISP 1.2.1", "garbage", None, "DISP 1.5.2AG", "DISP 1.5.2AR"]
    result = normalise_provisions(refs)
    assert result == {"DISP 1.2.1", "DISP 1.5.2A"}
