"""
query_router.py — classify a question so retrieval can be shaped to it.

Different legal questions need different evidence. Asking "what is IPC 420?"
wants the provision first; asking "what has the Supreme Court said about
cheating?" wants judgments first. Retrieving the same mixture for both wastes
context on the wrong source type.

Five types, matching the project specification:

    A  direct_section     names a section        -> statute first, then cases on it
    B  natural_language   describes a concept    -> statute + explanatory cases
    C  case_law           asks about decisions   -> judgments first
    D  application        doctrinal/applied      -> statute + cases on the point
    E  unsupported        premise fails          -> abstain, do not retrieve

Type E is decided by `legal_registry.check_premises`, not by wording: a query is
unsupported when it names a section that does not exist, or a statute the corpus
does not hold. That is a structural fact, not a linguistic one.

Classification is rule-based on deliberate grounds. A learned classifier would
need labelled data this project does not have, and its errors would be opaque —
whereas a rule that misfires can be read, and is visible in the routing metadata
returned with every answer.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.services.legal_registry import PremiseCheck, check_premises  # noqa: E402

QueryType = Literal["direct_section", "natural_language", "case_law", "application", "unsupported"]

# Asking about what courts have decided.
CASE_LAW_CUES = re.compile(
    r"\b(?:supreme\s+court|high\s+court|court\s+(?:has|have|held|said|ruled|observed)"
    r"|case\s+law|judgment|judgement|precedent|ratio|held\s+in|decided|bench"
    r"|landmark|authorit(?:y|ies)|which\s+cases?|what\s+cases?)\b",
    re.I,
)

# Asking whether a rule applies to a situation.
APPLICATION_CUES = re.compile(
    r"\b(?:can\s+a\s+person|can\s+someone|is\s+it\s+(?:an\s+)?(?:offence|illegal)"
    r"|what\s+if|whether|would\s+it|does\s+it\s+(?:amount|constitute)"
    r"|amounts?\s+to|constitutes?|liable|convicted\s+(?:for|if|when)"
    r"|difference\s+between|distinguish)\b",
    re.I,
)

SECTION_MENTION = re.compile(
    r"\b(?:section|sec\.?|s\.|u/s)\s*\d{1,4}[A-Za-z]{0,2}\b"
    r"|\b(?:IPC|BNS)\s+\d{1,4}[A-Za-z]{0,2}\b",
    re.I,
)


@dataclass
class Routing:
    query_type: QueryType
    statute_weight: float
    judgment_weight: float
    reason: str
    premises: PremiseCheck
    # Sections the query explicitly names, used to boost cross-referenced cases.
    named_sections: list[tuple[str, str]]

    @property
    def should_retrieve(self) -> bool:
        return self.query_type != "unsupported"

    def to_dict(self) -> dict:
        return {
            "query_type": self.query_type,
            "statute_weight": self.statute_weight,
            "judgment_weight": self.judgment_weight,
            "reason": self.reason,
            "named_sections": [f"{s}:{n}" for s, n in self.named_sections],
            "premises": self.premises.to_dict(),
        }


def classify(query: str) -> Routing:
    premises = check_premises(query)

    # Type E first: a false premise makes the other distinctions irrelevant.
    if not premises.ok:
        return Routing(
            query_type="unsupported",
            statute_weight=0.0,
            judgment_weight=0.0,
            reason="; ".join(premises.problems),
            premises=premises,
            named_sections=[],
        )

    named = [(r.statute, r.section) for r in premises.refs if r.in_corpus_statute and r.exists]
    has_section = bool(named) or bool(SECTION_MENTION.search(query))
    asks_cases = bool(CASE_LAW_CUES.search(query))
    asks_application = bool(APPLICATION_CUES.search(query))

    # Order matters. A question naming a section AND asking what courts said is a
    # case-law question about that section, so case-law wins over direct lookup.
    if asks_cases:
        return Routing(
            "case_law", 0.25, 0.75,
            "asks what courts have decided, so judgments are weighted above statute text",
            premises, named,
        )
    if asks_application:
        return Routing(
            "application", 0.45, 0.55,
            "asks whether a rule applies, which needs both the provision and cases applying it",
            premises, named,
        )
    if has_section:
        return Routing(
            "direct_section", 0.75, 0.25,
            "names a specific section, so the provision itself leads",
            premises, named,
        )
    return Routing(
        "natural_language", 0.6, 0.4,
        "describes a concept without naming a provision",
        premises, named,
    )
