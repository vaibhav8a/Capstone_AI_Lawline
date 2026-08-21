"""
corpus_selector.py — decide which statute a query is about.

The IPC was repealed and replaced by the BNS on 2024-07-01. Both are in the
corpus, and answering an IPC question with BNS text (or the reverse) is a
substantive legal error, not a cosmetic one. So the corpus is never searched as
one undifferentiated blob.

Three outcomes:

    IPC        the query names the IPC explicitly  -> filter to IPC
    BNS        the query names the BNS explicitly  -> filter to BNS
    None       ambiguous -> search both, and TELL THE USER that both were
               searched and which framework each result belongs to

The ambiguous case deliberately does not guess. "What is the punishment for
theft?" is a legitimate question under either statute, and silently picking one
would hide a choice the reader needs to make.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Law = Literal["IPC", "BNS"]

# Explicit named references. Ordered longest-first so "Bharatiya Nyaya Sanhita"
# is matched before any shorter fragment.
IPC_PATTERNS = (
    r"\bindian\s+penal\s+code\b",
    r"\bi\.?\s*p\.?\s*c\.?\b",
    r"\bpenal\s+code\b",
    r"\bact\s+45\s+of\s+1860\b",
)

BNS_PATTERNS = (
    r"\bbharatiya\s+nyaya\s+sanhita\b",
    r"\bb\.?\s*n\.?\s*s\.?\b",
    r"\bnyaya\s+sanhita\b",
    r"\bact\s+45\s+of\s+2023\b",
    r"\bnew\s+criminal\s+(?:law|code)\b",
)

IPC_RE = re.compile("|".join(IPC_PATTERNS), re.I)
BNS_RE = re.compile("|".join(BNS_PATTERNS), re.I)

# "section 420", "s.420", "sec 302", "u/s 34"
SECTION_RE = re.compile(
    r"\b(?:u/?s|under\s+section|section|sec\.?|s\.)\s*(\d{1,3}[A-Za-z]{0,2})\b",
    re.I,
)

# "IPC 420", "BNS 103" — a statute abbreviation followed directly by a number is
# an unambiguous section reference even with no "section" keyword. Without this,
# "What is IPC 420?" was not recognised as a section lookup at all: the exact
# match never fired, and the short query's low absolute similarity then caused
# the retrieval gate to refuse a question the corpus answers perfectly well.
BARE_SECTION_RE = re.compile(
    r"\b(?:IPC|BNS|I\.P\.C\.?|B\.N\.S\.?)\s+(\d{1,3}[A-Za-z]{0,2})\b", re.I
)


@dataclass(frozen=True)
class CorpusChoice:
    law: Law | None
    # True when the user named a statute outright; False when it was inferred or
    # left open. Drives whether the UI says "you asked about X" or "both searched".
    explicit: bool
    reason: str
    section: str | None = None

    @property
    def is_ambiguous(self) -> bool:
        return self.law is None

    def to_dict(self) -> dict:
        return {
            "law": self.law,
            "explicit": self.explicit,
            "ambiguous": self.is_ambiguous,
            "reason": self.reason,
            "section": self.section,
        }


def _section_in(query: str) -> str | None:
    match = SECTION_RE.search(query) or BARE_SECTION_RE.search(query)
    return match.group(1).upper() if match else None


def select_corpus(query: str, override: str | None = None) -> CorpusChoice:
    """Choose the statute to search.

    `override` comes from the UI corpus selector and always wins — an explicit
    user choice is never second-guessed by the heuristics below.
    """
    section = _section_in(query)

    if override:
        normalised = override.strip().upper()
        if normalised in ("IPC", "BNS"):
            return CorpusChoice(
                law=normalised,  # type: ignore[arg-type]
                explicit=True,
                reason="selected by the user in the corpus selector",
                section=section,
            )
        if normalised in ("BOTH", "ALL", "ANY"):
            return CorpusChoice(
                law=None,
                explicit=True,
                reason="user asked for both statutes",
                section=section,
            )

    mentions_ipc = bool(IPC_RE.search(query))
    mentions_bns = bool(BNS_RE.search(query))

    if mentions_ipc and not mentions_bns:
        return CorpusChoice("IPC", True, "query names the Indian Penal Code", section)
    if mentions_bns and not mentions_ipc:
        return CorpusChoice("BNS", True, "query names the Bharatiya Nyaya Sanhita", section)
    if mentions_ipc and mentions_bns:
        # A comparison question — both are wanted, and that IS the answer shape.
        return CorpusChoice(
            None, True, "query names both statutes, so both were searched", section
        )

    return CorpusChoice(
        None,
        False,
        "query does not name a statute, so both the IPC (repealed 2024-07-01) "
        "and the BNS (in force) were searched",
        section,
    )


def chroma_filter(choice: CorpusChoice) -> dict | None:
    """Translate the choice into a ChromaDB metadata filter."""
    if choice.law is None:
        return None
    return {"law": choice.law}


def disclosure(choice: CorpusChoice) -> str:
    """One line for the UI stating which legal framework was consulted."""
    if choice.law == "IPC":
        return (
            "Searched the Indian Penal Code, 1860 — repealed with effect from "
            "1 July 2024 and replaced by the Bharatiya Nyaya Sanhita, 2023."
        )
    if choice.law == "BNS":
        return "Searched the Bharatiya Nyaya Sanhita, 2023 — in force since 1 July 2024."
    return (
        "No statute was named, so both were searched: the Indian Penal Code, 1860 "
        "(repealed 1 July 2024) and the Bharatiya Nyaya Sanhita, 2023 (in force). "
        "Each result below states which one it comes from."
    )
