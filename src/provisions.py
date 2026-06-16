"""FCA Handbook provision reference normalisation.

Both the drafting-citation precision/recall metric and the retrieved-text
provision-recall diagnostic compare provision references that the ground truth,
the bot, and the Handbook text all write inconsistently. The same rule appears
as ``DISP 1.2.1R`` in one scenario and ``DISP 1.2.1`` in another; the bot may
cite ``DISP 1.5.2AR`` where the ground truth has ``DISP 1.5.2A``. Comparing the
raw strings would score genuine matches as misses.

``normalise_provision`` reduces a reference to a canonical comparison key by:

1. Stripping any trailing sub-paragraph markers — ``(2)``, ``(a)``, ``(2)(a)``.
2. Separating the module (leading alpha run, e.g. ``DISP``) from the identifier.
3. Stripping a single trailing status suffix from {R, G, E}, while keeping
   amendment letters that are part of the identifier (``A``, ``AA``, ``AB``,
   ``AD``, ``B`` ...).

So ``DISP 1.5.2AR`` and ``DISP 1.5.2AG`` and ``DISP 1.5.2A`` all collapse to the
key ``DISP 1.5.2A``; ``DISP 1.3.1ADR`` collapses to ``DISP 1.3.1AD`` while the
bare ``DISP 1.3.1AD`` is left intact (its ``D`` is an amendment letter, not a
status suffix).

Deliberate assumptions, valid for the JAM-274 provision universe and asserted in
tests/test_provisions.py:

- The strippable status set is {R, G, E}. ``D`` (direction) is treated as an
  amendment/identifier letter, because the only ``D`` in this corpus is the
  amendment letter in ``...AD`` and stripping it would corrupt the reference.
  There are no direction provisions in the corpus to lose by this choice.
- The status suffix is the *last* character of the identifier's trailing alpha
  run. A heavily-amended provision whose amendment letters happened to end in
  R/G/E (e.g. a hypothetical ``...AE``) would be mis-stripped. No such reference
  exists in the corpus; the whole-universe test would catch one if it appeared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Known FCA sourcebook prefixes seen in the corpus. Used by extract_* helpers;
# normalise_provision itself takes the leading alpha run and does not depend on
# this set, so an unlisted module still normalises.
MODULES: frozenset[str] = frozenset(
    {"DISP", "BCOBS", "COBS", "CONC", "ICOBS", "MCOB", "PRIN", "SYSC"}
)

# Status suffixes stripped during normalisation. D is intentionally excluded.
STATUS_SUFFIXES: frozenset[str] = frozenset({"R", "G", "E"})

_SUBPARA_RE = re.compile(r"(?:\([^()]*\))+\s*$")
_MODULE_RE = re.compile(r"^([A-Z]+)\s*(.+)$")


@dataclass(frozen=True)
class ParsedProvision:
    """A parsed provision reference.

    Attributes:
        raw: The original input string.
        module: The sourcebook module, e.g. ``DISP``.
        core: The identifier with sub-paragraphs and status suffix removed,
            e.g. ``1.5.2A``.
        status: The status suffix that was stripped (``R``/``G``/``E``), or None.
        normalised: The canonical comparison key, ``"{module} {core}"``.
    """

    raw: str
    module: str
    core: str
    status: str | None

    @property
    def normalised(self) -> str:
        return f"{self.module} {self.core}"


def _split_status_suffix(ident: str) -> tuple[str, str | None]:
    """Split an identifier into (core, status_suffix).

    The trailing run of alpha characters is the amendment letters plus an
    optional status suffix. If that run ends in a {R, G, E} character, that one
    character is the status suffix and is removed; the rest are amendment
    letters and are kept.
    """
    i = len(ident)
    while i > 0 and ident[i - 1].isalpha():
        i -= 1
    head, trail = ident[:i], ident[i:]

    if trail and trail[-1] in STATUS_SUFFIXES:
        return head + trail[:-1], trail[-1]
    return head + trail, None


def parse_provision(ref: str | None) -> ParsedProvision | None:
    """Parse a provision reference into its components, or None if unparseable."""
    if not ref or not isinstance(ref, str):
        return None

    s = ref.strip().upper()
    if not s:
        return None

    # 1. strip trailing sub-paragraph markers, e.g. "(2)", "(a)", "(2)(a)"
    s = _SUBPARA_RE.sub("", s).strip()

    # 2. separate module (leading alpha run) from the identifier
    m = _MODULE_RE.match(s)
    if not m:
        return None
    module, ident = m.group(1), m.group(2)
    ident = re.sub(r"\s+", "", ident)  # e.g. "1.6.2 R" -> "1.6.2R"

    # identifier must start with a chapter number; rejects appendices ("APP 1..")
    if not ident or not ident[0].isdigit():
        return None

    # 3. strip a single trailing status suffix from {R, G, E}
    core, status = _split_status_suffix(ident)
    if not core:
        return None

    return ParsedProvision(raw=ref, module=module, core=core, status=status)


def normalise_provision(ref: str | None) -> str | None:
    """Reduce a provision reference to its canonical comparison key.

    Returns ``"{module} {core}"`` (e.g. ``"DISP 1.5.2A"``) or None if the
    reference cannot be parsed.
    """
    parsed = parse_provision(ref)
    return parsed.normalised if parsed is not None else None


def normalise_provisions(refs) -> set[str]:
    """Normalise an iterable of references into a set of canonical keys.

    Unparseable references are dropped.
    """
    out: set[str] = set()
    for ref in refs:
        key = normalise_provision(ref)
        if key is not None:
            out.add(key)
    return out
