"""
legal_eval_sets.py — evaluation sets for judgment, combined and false-premise retrieval.

Kept entirely separate from `test_queries.json`, which is the frozen statute
benchmark behind `retrieval_experiments.json`. Nothing here touches that file or
its results.

Gold labels: how they were made, and what they are worth
--------------------------------------------------------
Judgment gold labels are derived by WEAK SUPERVISION, not human relevance
judgement. For a query about section X, the gold set is the judgments whose text
cites section X within 60 characters of the statute's name (see
`legal_registry.sections_by_proximity`).

That is an honest label for "cites the provision" and a *proxy* for "is relevant
to it". The two are not identical: a judgment can cite s.302 in passing while
being about something else, and a judgment can discuss murder doctrine without
the citation landing near the statute name. Recall@k computed against these
labels therefore measures citation retrieval, and should be reported as such —
never as "relevance accuracy".

The alternative was to invent relevance judgements by hand, which for 260
judgments would be neither reliable nor reproducible. A derived label whose
derivation is stated is more defensible than an undocumented manual one.

Splits
------
    DEV       used to tune case-relevance weights
    HELD_OUT  written before tuning, scored once, never tuned against

The project has already had one result overturned by evaluating on tuning data
(the 5-query abstention finding), so the split is enforced structurally: the
tuning script only ever loads DEV.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

# ── B. Judgment retrieval ───────────────────────────────────────────────────
# (query, statute, section) — gold = judgments citing that section.
# Sections chosen because the corpus contains several judgments citing each, so
# recall is measurable rather than all-or-nothing.

JUDGMENT_DEV: list[tuple[str, str, str]] = [
    ("What has the Supreme Court held about murder under IPC 302?", "IPC", "302"),
    ("Supreme Court judgments on common intention under section 34 IPC", "IPC", "34"),
    ("Cases on culpable homicide not amounting to murder, IPC 304", "IPC", "304"),
    ("What have the courts said about cheating under IPC 420?", "IPC", "420"),
    ("Judgments interpreting criminal conspiracy under IPC 120B", "IPC", "120B"),
    ("Supreme Court decisions on attempt to murder, IPC 307", "IPC", "307"),
    ("Case law on rioting under IPC 147", "IPC", "147"),
    ("Judgments about voluntarily causing hurt under IPC 323", "IPC", "323"),
]

JUDGMENT_HELD_OUT: list[tuple[str, str, str]] = [
    ("What has the Supreme Court said about rape under IPC 376?", "IPC", "376"),
    ("Judgments on unlawful assembly under section 149 IPC", "IPC", "149"),
    ("Supreme Court cases on abetment of suicide, IPC 306", "IPC", "306"),
    ("Case law on dowry death under IPC 304B", "IPC", "304B"),
    ("Decisions interpreting kidnapping under IPC 363", "IPC", "363"),
    ("What have courts held about criminal breach of trust, IPC 406?", "IPC", "406"),
    ("Judgments on grievous hurt under IPC 325", "IPC", "325"),
    ("Supreme Court rulings on robbery under IPC 392", "IPC", "392"),
]

# ── C. Combined: needs statute text AND judicial interpretation ─────────────
# gold_section must be retrieved among statutes; gold judgments are those citing it.

COMBINED_DEV: list[tuple[str, str, str]] = [
    ("What is IPC 302 and how have courts applied it?", "IPC", "302"),
    ("Explain cheating under IPC 420 with case law", "IPC", "420"),
    ("What does section 34 IPC say and how is common intention proved?", "IPC", "34"),
    ("Define criminal conspiracy and how the Supreme Court has construed it", "IPC", "120B"),
    ("What is culpable homicide under IPC 304 and its judicial interpretation?", "IPC", "304"),
]

COMBINED_HELD_OUT: list[tuple[str, str, str]] = [
    ("What is IPC 376 and how have the courts interpreted it?", "IPC", "376"),
    ("Explain abetment of suicide under IPC 306 with judicial authority", "IPC", "306"),
    ("What does IPC 304B provide and how is dowry death established?", "IPC", "304B"),
    ("Criminal breach of trust under IPC 406 and its case law", "IPC", "406"),
    ("What is unlawful assembly under IPC 149 and how have courts applied it?", "IPC", "149"),
]

# ── D. False premise ────────────────────────────────────────────────────────
# (query, category, why it is unsupported). Correct behaviour is refusal.

FALSE_PREMISE_DEV: list[tuple[str, str, str]] = [
    ("What is the punishment under IPC Section 999?", "nonexistent_section", "IPC has no s.999"),
    ("Explain IPC Section 888 on cyber fraud", "nonexistent_section", "IPC has no s.888"),
    ("What does BNS Section 1200 say?", "nonexistent_section", "BNS has no s.1200"),
    ("How is maintenance calculated under Section 125 CrPC?", "absent_statute", "CrPC not in corpus"),
    ("What does Section 138 of the Negotiable Instruments Act provide?", "absent_statute", "NI Act not in corpus"),
    ("What is Section 66A of the Information Technology Act?", "absent_statute", "IT Act not in corpus"),
    ("What are the grounds for divorce under the Hindu Marriage Act?", "absent_statute", "HMA not in corpus"),
    ("Explain section 27 of the Indian Evidence Act", "absent_statute", "Evidence Act not in corpus"),
]

FALSE_PREMISE_HELD_OUT: list[tuple[str, str, str]] = [
    ("What is IPC Section 750 about?", "nonexistent_section", "IPC has no s.750"),
    ("Explain BNS Section 999", "nonexistent_section", "BNS has no s.999"),
    ("What does IPC section 620 prescribe?", "nonexistent_section", "IPC has no s.620"),
    ("What is the penalty under section 420 of the NDPS Act?", "absent_statute", "NDPS not in corpus"),
    ("Explain section 12 of the POCSO Act", "absent_statute", "POCSO not in corpus"),
    ("What does section 100 CPC provide?", "absent_statute", "CPC not in corpus"),
    ("Section 173 of the Code of Criminal Procedure", "absent_statute", "CrPC not in corpus"),
    ("What is section 7 of the Prevention of Corruption Act?", "absent_statute", "PC Act not in corpus"),
    ("Under BNSS section 480, what is the bail procedure?", "absent_statute", "BNSS not in corpus"),
    ("What does the Motor Vehicles Act section 166 say?", "absent_statute", "MV Act not in corpus"),
]


def judgment_gold(statute: str, section: str) -> set[str]:
    """Judgments citing a section, keyed by neutral citation."""
    from backend.services.legal_registry import judgments_citing

    return {
        c.get("neutral_citation") or c.get("case_name", "")
        for c in judgments_citing(statute, section)
    }


def validate(split: list[tuple[str, str, str]], min_gold: int = 1) -> list[str]:
    """Report queries whose gold set is too small to measure anything."""
    problems = []
    for query, statute, section in split:
        gold = judgment_gold(statute, section)
        if len(gold) < min_gold:
            problems.append(f"{query!r} -> {statute} s.{section}: only {len(gold)} gold judgments")
    return problems
