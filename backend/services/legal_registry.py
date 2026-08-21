"""
legal_registry.py — the structured layer beneath vector search.

Two things embeddings cannot represent, both of which caused measured failures:

1.  EXISTENCE. "What is IPC Section 999?" retrieves ss.329/53/124A with a healthy
    similarity of 0.593, because a nonexistent section still has near neighbours.
    Similarity has no way to express "that section does not exist". A lookup
    table does.

2.  STATUTE IDENTITY. "How is maintenance calculated under Section 125 CrPC?"
    retrieves IPC s.125 and BNS s.125 — the right number in entirely the wrong
    statute. The corpus holds no CrPC, so the honest answer is that the evidence
    is absent, not that a same-numbered provision was found.

Both are exact-match problems over structured metadata, so they are solved here
rather than by tuning a threshold. The gate evaluation showed thresholds cannot
solve them: held-out adversarial accuracy was 0.125.

Also builds the statute↔judgment cross-reference map, derived from the section
numbers actually cited in each judgment's text — never inferred.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402

logger = logging.getLogger(__name__)

PARSED_STATUTE_DIR = config.BASE_DIR / "data" / "processed" / "statutes"
JUDGMENTS_PATH = config.BASE_DIR / "data" / "processed" / "judgments_sc" / "judgments.json"
XREF_PATH = config.BASE_DIR / "data" / "processed" / "cross_references.json"

# Statutes the corpus actually contains. Anything else named in a query is out of
# scope, and saying so is more useful than silently substituting a same-numbered
# provision from a statute we do happen to hold.
CORPUS_STATUTES = {"IPC", "BNS"}

# Statutes a user may reasonably name that we do NOT hold. Recognised so the
# system can say "not in this corpus" instead of retrieving a numeric collision.
KNOWN_ABSENT_STATUTES = {
    "CRPC": "Code of Criminal Procedure, 1973",
    "BNSS": "Bharatiya Nagarik Suraksha Sanhita, 2023",
    "EVIDENCE": "Indian Evidence Act, 1872",
    "BSA": "Bharatiya Sakshya Adhiniyam, 2023",
    "NDPS": "Narcotic Drugs and Psychotropic Substances Act, 1985",
    "POCSO": "Protection of Children from Sexual Offences Act, 2012",
    "IT": "Information Technology Act, 2000",
    "NI": "Negotiable Instruments Act, 1881",
    "PC": "Prevention of Corruption Act, 1988",
    "HMA": "Hindu Marriage Act, 1955",
    "CPC": "Code of Civil Procedure, 1908",
    "MV": "Motor Vehicles Act, 1988",
}

ABSENT_STATUTE_PATTERNS = [
    (re.compile(r"\b(?:cr\.?\s?p\.?\s?c\.?|code\s+of\s+criminal\s+procedure)\b", re.I), "CRPC"),
    (re.compile(r"\b(?:b\.?n\.?s\.?s\.?|bharatiya\s+nagarik\s+suraksha)\b", re.I), "BNSS"),
    (re.compile(r"\b(?:indian\s+)?evidence\s+act\b", re.I), "EVIDENCE"),
    (re.compile(r"\bbharatiya\s+sakshya\b", re.I), "BSA"),
    (re.compile(r"\b(?:n\.?d\.?p\.?s\.?|narcotic\s+drugs)\b", re.I), "NDPS"),
    (re.compile(r"\bpocso\b", re.I), "POCSO"),
    (re.compile(r"\b(?:i\.?t\.?\s+act|information\s+technology\s+act)\b", re.I), "IT"),
    (re.compile(r"\b(?:n\.?i\.?\s+act|negotiable\s+instruments)\b", re.I), "NI"),
    (re.compile(r"\bprevention\s+of\s+corruption\b", re.I), "PC"),
    (re.compile(r"\bhindu\s+marriage\s+act\b", re.I), "HMA"),
    (re.compile(r"\b(?:c\.?p\.?c\.?|code\s+of\s+civil\s+procedure)\b", re.I), "CPC"),
    (re.compile(r"\bmotor\s+vehicles?\s+act\b", re.I), "MV"),
]


@dataclass
class SectionRef:
    """A statute+section named in a query."""
    statute: str          # "IPC" | "BNS" | an absent-statute key | "" if unnamed
    section: str
    in_corpus_statute: bool
    exists: bool
    title: str = ""

    def to_dict(self) -> dict:
        return {
            "statute": self.statute,
            "section": self.section,
            "in_corpus_statute": self.in_corpus_statute,
            "exists": self.exists,
            "title": self.title,
        }


@dataclass
class PremiseCheck:
    """Verdict on the factual premises of a query."""
    ok: bool
    refs: list[SectionRef] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    absent_statutes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "refs": [r.to_dict() for r in self.refs],
            "problems": self.problems,
            "absent_statutes": self.absent_statutes,
        }


@lru_cache(maxsize=1)
def section_index() -> dict[str, dict[str, str]]:
    """{statute: {section: title}} for every section in the corpus."""
    index: dict[str, dict[str, str]] = {}
    for doc_id in sorted(CORPUS_STATUTES):
        path = PARSED_STATUTE_DIR / f"{doc_id}_sections.json"
        if not path.exists():
            logger.warning("[registry] %s missing", path)
            continue
        index[doc_id] = {
            str(r["section"]).upper(): r["title"]
            for r in json.loads(path.read_text(encoding="utf-8"))
        }
    return index


def section_exists(statute: str, section: str) -> bool:
    return section.upper() in section_index().get(statute.upper(), {})


def section_title(statute: str, section: str) -> str:
    return section_index().get(statute.upper(), {}).get(section.upper(), "")


# ── query premise parsing ───────────────────────────────────────────────────

# "<statute> section <n>" or "section <n> <statute>" — the statute may sit on
# either side of the number, and both forms are common in real questions.
_STATUTE_TOKEN = r"(IPC|BNS|I\.P\.C\.?|B\.N\.S\.?|Cr\.?\s?P\.?C\.?|BNSS|CPC|NDPS|POCSO|Evidence\s+Act|N\.?I\.?\s+Act|I\.?T\.?\s+Act)"
_SECTION_NUM = r"(\d{1,4}[A-Z]{0,2})"

PATTERNS = [
    re.compile(rf"{_STATUTE_TOKEN}\s+(?:section|sec\.?|s\.)?\s*{_SECTION_NUM}\b", re.I),
    re.compile(rf"(?:section|sec\.?|s\.|u/s)\s*{_SECTION_NUM}\s+(?:of\s+(?:the\s+)?)?{_STATUTE_TOKEN}", re.I),
]

BARE_SECTION = re.compile(r"(?:section|sec\.?|s\.|u/s)\s*(\d{1,4}[A-Z]{0,2})\b", re.I)


def _normalise_statute(token: str) -> str:
    compact = re.sub(r"[.\s]", "", token).upper()
    if compact in ("IPC",):
        return "IPC"
    if compact in ("BNS",):
        return "BNS"
    if compact.startswith("CRPC"):
        return "CRPC"
    if compact.startswith("BNSS"):
        return "BNSS"
    if compact.startswith("EVIDENCE"):
        return "EVIDENCE"
    for key in KNOWN_ABSENT_STATUTES:
        if compact.startswith(key):
            return key
    return compact


def check_premises(query: str) -> PremiseCheck:
    """Validate every statute/section a query asserts.

    Returns ok=False when the query names a section that does not exist, or names
    a statute the corpus does not hold. Both are grounds to refuse rather than to
    retrieve something numerically similar.
    """
    refs: list[SectionRef] = []
    problems: list[str] = []
    absent: list[str] = []
    seen: set[tuple[str, str]] = set()

    for pattern in PATTERNS:
        for match in pattern.finditer(query):
            groups = match.groups()
            # Group order differs between the two patterns.
            if re.fullmatch(_SECTION_NUM, groups[0] or "", re.I):
                section, statute_token = groups[0], groups[1]
            else:
                statute_token, section = groups[0], groups[1]
            statute = _normalise_statute(statute_token or "")
            key = (statute, section.upper())
            if key in seen:
                continue
            seen.add(key)

            in_corpus = statute in CORPUS_STATUTES
            exists = section_exists(statute, section) if in_corpus else False
            refs.append(
                SectionRef(statute, section.upper(), in_corpus, exists, section_title(statute, section))
            )

            if not in_corpus:
                name = KNOWN_ABSENT_STATUTES.get(statute, statute)
                if statute not in absent:
                    absent.append(statute)
                problems.append(
                    f"the query refers to {name}, which is not in this corpus "
                    f"(only the IPC and BNS are indexed)"
                )
            elif not exists:
                problems.append(
                    f"{statute} section {section.upper()} does not exist in the corpus"
                )

    # A statute named without any section number still matters: "what does the
    # Evidence Act say about X" cannot be answered from an IPC/BNS corpus.
    if not refs:
        for pattern, key in ABSENT_STATUTE_PATTERNS:
            if pattern.search(query):
                if key not in absent:
                    absent.append(key)
                    problems.append(
                        f"the query refers to {KNOWN_ABSENT_STATUTES[key]}, which is "
                        "not in this corpus (only the IPC and BNS are indexed)"
                    )

    return PremiseCheck(ok=not problems, refs=refs, problems=problems, absent_statutes=absent)


# ── statute ↔ judgment cross-references ─────────────────────────────────────

# A section number counts as belonging to a statute only when the statute is
# named close by. Window chosen to span the usual phrasings — "section 302 IPC",
# "under Section 302 of the Indian Penal Code", "IPC, section 302" — without
# reaching across unrelated sentences.
STATUTE_PROXIMITY_CHARS = 60

_STATUTE_NEAR = {
    "IPC": re.compile(r"(?:indian\s+penal\s+code|I\.?P\.?C\.?|penal\s+code)", re.I),
    "BNS": re.compile(r"(?:bharatiya\s+nyaya\s+sanhita|B\.?N\.?S\.?)", re.I),
}

_SECTION_MENTION = re.compile(
    r"(?:u/?s|under\s+section|sections?|ss?\.)\s*(\d{1,3}[A-Z]{0,2})", re.I
)


def sections_by_proximity(text: str) -> dict[str, set[str]]:
    """Section numbers attributed to a statute by textual proximity.

    Bare-number extraction cannot tell which Act a "section 4" belongs to, and in
    practice it usually is not the IPC. Measured on this corpus, bare extraction
    attributed 280 links to IPC ss.1-15 — the Code's definitional preliminaries,
    including "Gender" (24 judgments) and '"Public"' (15) — which criminal
    appeals essentially never turn on. Those are citations to other Acts.

    Requiring the statute to be named within STATUTE_PROXIMITY_CHARS of the
    section number removes that class of false link.
    """
    found: dict[str, set[str]] = {"IPC": set(), "BNS": set()}
    for match in _SECTION_MENTION.finditer(text):
        section = match.group(1).upper()
        start = max(0, match.start() - STATUTE_PROXIMITY_CHARS)
        stop = min(len(text), match.end() + STATUTE_PROXIMITY_CHARS)
        window = text[start:stop]
        for statute, pattern in _STATUTE_NEAR.items():
            if pattern.search(window):
                found[statute].add(section)
    return found


def build_cross_references() -> dict:
    """Map each statutory section to the judgments that cite it.

    A link is recorded only where the judgment's text places the section number
    within STATUTE_PROXIMITY_CHARS of the statute's name. Nothing is inferred
    from topic similarity, and nothing is invented.
    """
    if not JUDGMENTS_PATH.exists():
        raise FileNotFoundError(f"{JUDGMENTS_PATH} not found")

    judgments = json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))
    index = section_index()

    by_section: dict[str, list[dict]] = defaultdict(list)
    bare_link_count = 0
    for record in judgments:
        bare_link_count += len([
            s for s in record.get("sections_referred", [])
            if str(s).upper() in index.get("IPC", {})
        ])
        attributed = sections_by_proximity(record.get("text", ""))
        for statute, sections in attributed.items():
            for section in sections:
                if section not in index.get(statute, {}):
                    continue
                by_section[f"{statute}:{section}"].append({
                    "case_name": record["case_name"],
                    "citation": record["citation"],
                    "neutral_citation": record["neutral_citation"],
                    "judgment_date": record["judgment_date"],
                    "year": record["year"],
                    "source_url": record["source_url"],
                })

    total_links = sum(len(v) for v in by_section.values())
    payload = {
        "generated_from": (
            f"section numbers appearing within {STATUTE_PROXIMITY_CHARS} characters "
            "of the statute name in the judgment text"
        ),
        "judgments": len(judgments),
        "sections_with_judgments": len(by_section),
        "total_links": total_links,
        "bare_number_links_for_comparison": bare_link_count,
        "caveat": (
            "A link means the judgment cites that section, not that the judgment is "
            "substantively about it. Downstream ranking treats a cross-reference as "
            "one signal among several, never as proof of relevance."
        ),
        "map": {k: v for k, v in sorted(by_section.items())},
    }
    XREF_PATH.parent.mkdir(parents=True, exist_ok=True)
    XREF_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


@lru_cache(maxsize=1)
def cross_references() -> dict[str, list[dict]]:
    if not XREF_PATH.exists():
        build_cross_references()
    return json.loads(XREF_PATH.read_text(encoding="utf-8"))["map"]


def judgments_citing(statute: str, section: str) -> list[dict]:
    return cross_references().get(f"{statute.upper()}:{section.upper()}", [])


@lru_cache(maxsize=1)
def sections_by_judgment() -> dict[str, set[str]]:
    """Inverse of the cross-reference map: judgment key -> sections it cites.

    Exists so that ranking and evaluation share ONE definition of "cites section
    X". They previously differed: ranking used the judgment's raw
    `sections_referred` (bare numbers, no statute attribution) while gold labels
    used the proximity-filtered map. The scorer therefore rewarded judgments the
    gold set did not count, which depressed measured recall for reasons that had
    nothing to do with retrieval quality.
    """
    inverse: dict[str, set[str]] = defaultdict(set)
    for key, cases in cross_references().items():
        statute, section = key.split(":", 1)
        for case in cases:
            case_key = case.get("neutral_citation") or case.get("case_name", "")
            if case_key:
                inverse[case_key].add(section.upper())
    return dict(inverse)


def judgment_cites_section(case_key: str, section: str) -> bool:
    return section.upper() in sections_by_judgment().get(case_key, set())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    payload = build_cross_references()
    print(json.dumps({k: v for k, v in payload.items() if k != "map"}, indent=2))
    top = sorted(payload["map"].items(), key=lambda kv: len(kv[1]), reverse=True)[:12]
    print("\nmost-cited sections:")
    for key, cases in top:
        print(f"  {key:12} {len(cases):3} judgments")
