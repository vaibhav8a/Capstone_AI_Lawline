"""
corpus_selection.py — the criteria that decide what enters the judgment corpus.

Why this is a separate module
-----------------------------
The harvester's job is mechanical: fetch, extract, deduplicate, persist. The
question of *which* judgments belong in a criminal-law corpus is an editorial
decision that has to be auditable and reviewable on its own. Keeping it here
means the selection rules can be read, diffed and argued about without wading
through download plumbing, and means every rejection the harvester records can
name the exact rule that produced it.

The full rationale, including the measurements the thresholds were set from, is
in docs/corpus_selection.md.

Summary of the regime
---------------------
* Base gate      — a candidate must be long enough to be a real judgment and
                   must score as criminal-law material at all.
* Two-tier score — a high score admits a candidate as general fill; a merely
                   passing score admits it only if it closes an unmet topic
                   quota. This stops bulk expansion drifting into weak matter.
* Topic ceilings — murder and bail already dominate the corpus. A candidate
                   whose only topics are over-ceiling is rejected, so the
                   expansion broadens coverage instead of deepening the same
                   two areas.
* Strata         — years are drawn in blocks chosen to fix identified gaps
                   (the BNS era, the 2001-2017 hole, then depth elsewhere).

Nothing here infers or invents metadata. Topic detection reads the judgment's
own text; it is used for *selection*, never written out as a legal conclusion.
"""

from __future__ import annotations

import re
from bisect import bisect_left

# ── base gate ───────────────────────────────────────────────────────────────

MIN_TEXT_CHARS = 3000
# Unchanged from the original harvester: the floor at which a document is
# criminal-law material at all. Retained so the existing 260 stay inside the
# published criteria rather than being retroactively excluded.
MIN_CRIMINAL_SCORE = 6
# General fill must clear a higher bar. Between the two, a candidate is admitted
# only when it closes a quota that is still short.
GENERAL_FILL_SCORE = 10

# ── statutes ────────────────────────────────────────────────────────────────

STATUTE_PATTERNS = {
    "IPC": re.compile(r"\b(?:indian\s+penal\s+code|I\.?P\.?C\.?|penal\s+code)\b", re.I),
    "BNS": re.compile(r"\b(?:bharatiya\s+nyaya\s+sanhita|B\.?N\.?S\.?)\b", re.I),
    "CrPC": re.compile(r"\b(?:code\s+of\s+criminal\s+procedure|Cr\.?\s?P\.?C\.?)\b", re.I),
    "BNSS": re.compile(r"\b(?:bharatiya\s+nagarik\s+suraksha\s+sanhita|B\.?N\.?S\.?S\.?)\b", re.I),
    "Evidence": re.compile(r"\b(?:indian\s+evidence\s+act|evidence\s+act|bharatiya\s+sakshya)\b", re.I),
    "NDPS": re.compile(r"\bnarcotic\s+drugs|N\.?D\.?P\.?S\.?\b", re.I),
    "POCSO": re.compile(r"\bPOCSO\b|protection\s+of\s+children\s+from\s+sexual\s+offences", re.I),
}

CRIMINAL_TERMS = re.compile(
    r"\b(accused|conviction|convicted|acquittal|acquitted|sentence|sentenced|"
    r"prosecution|bail|charge-?sheet|culpable|homicide|murder|offence|"
    r"criminal\s+appeal|FIR|investigating\s+officer|dying\s+declaration)\b",
    re.I,
)

# ── section extraction (v2) ─────────────────────────────────────────────────
#
# v1 matched any "Section <n>" and kept the number alone. Measured against the
# existing 260, its most frequent "statutory references" were 3, 4, 5, 2, 6 and
# 8 — almost all of them prose like "section 3 of the notification" or a
# paragraph number, not an offence provision. A reference with no statute is not
# a citation, so v2 keeps a section only when a statute name sits close enough
# to attribute it, and emits the attributed form ("IPC 302").
#
# References with no nearby statute are not discarded silently: they are
# returned separately as `unqualified` so the loss is visible and auditable.

SECTION_RE = re.compile(
    r"\b(?:u/?s\.?|under\s+section|sections?|ss?\.)\s*"
    r"(\d{1,3}[A-Z]{0,2}(?:\s*(?:,|and|&|/)\s*\d{1,3}[A-Z]{0,2})*)",
    re.I,
)

# How far from a section number a statute name may sit and still be read as
# attributing it. Wide enough for "Section 302 read with 34 of the Indian Penal
# Code", narrow enough not to reach across unrelated sentences.
ATTRIBUTION_WINDOW = 120

# A statute named in the previous sentence does not attribute a section in this
# one. Without this guard "…of the Indian Penal Code. Clause 3 of the
# notification and section 5 of that circular…" yields a bogus "IPC 5", because
# the Penal Code mention is still inside the character window. Attribution may
# not cross a sentence boundary.
SENTENCE_BREAK_RE = re.compile(r"(?<!\bs)(?<!\bNo)(?<!\bv)[.;]\s+(?=[A-Z(\[])")

SECTION_EXTRACTION_VERSION = 2


def _statute_anchors(text: str) -> tuple[list[int], list[int], list[str]]:
    """Positions of every statute mention, sorted by start offset."""
    found: list[tuple[int, int, str]] = []
    for name, pattern in STATUTE_PATTERNS.items():
        for match in pattern.finditer(text):
            found.append((match.start(), match.end(), name))
    found.sort()
    return [f[0] for f in found], [f[1] for f in found], [f[2] for f in found]


def _nearest_statute(text: str, starts, ends, names,
                     span_start: int, span_end: int) -> str | None:
    """The statute mentioned closest to a span, if one is within the window."""
    if not starts:
        return None
    best_name, best_distance = None, ATTRIBUTION_WINDOW + 1
    index = bisect_left(starts, span_end)
    # Look at the anchors immediately before and after the span. Anything
    # further away is further still, so two probes suffice.
    for candidate in (index - 1, index):
        if not 0 <= candidate < len(starts):
            continue
        if ends[candidate] <= span_start:
            distance = span_start - ends[candidate]
        elif starts[candidate] >= span_end:
            distance = starts[candidate] - span_end
        else:
            distance = 0  # overlapping, e.g. "IPC section 302"
        if distance >= best_distance:
            continue
        gap = text[min(ends[candidate], span_end):max(starts[candidate], span_start)]
        if SENTENCE_BREAK_RE.search(gap):
            continue
        best_name, best_distance = names[candidate], distance
    return best_name if best_distance <= ATTRIBUTION_WINDOW else None


def extract_sections(text: str, limit: int = 40) -> tuple[list[str], list[str]]:
    """
    Statute-attributed section references, in first-appearance order.

    Returns (qualified, unqualified) where qualified entries look like
    "IPC 302" and unqualified are bare numbers no statute could be attached to.
    """
    starts, ends, names = _statute_anchors(text)
    qualified: list[str] = []
    unqualified: list[str] = []
    seen_q: set[str] = set()
    seen_u: set[str] = set()

    for match in SECTION_RE.finditer(text):
        statute = _nearest_statute(text, starts, ends, names, match.start(), match.end())
        for token in re.split(r"\s*(?:,|and|&|/)\s*", match.group(1), flags=re.I):
            token = token.strip().upper()
            if not re.fullmatch(r"\d{1,3}[A-Z]{0,2}", token):
                continue
            if statute:
                reference = f"{statute} {token}"
                if reference not in seen_q:
                    seen_q.add(reference)
                    qualified.append(reference)
                    if len(qualified) >= limit:
                        return qualified, unqualified
            elif token not in seen_u:
                seen_u.add(token)
                unqualified.append(token)
    return qualified, unqualified


# ── criminal-law scoring ────────────────────────────────────────────────────


def statutes_in(text: str) -> list[str]:
    return [name for name, pattern in STATUTE_PATTERNS.items() if pattern.search(text)]


def criminal_score(text: str) -> tuple[int, list[str]]:
    """Score a judgment's criminal-law relevance. Higher is more relevant."""
    statutes = statutes_in(text)
    score = 0
    for statute in statutes:
        score += 4 if statute in ("IPC", "BNS", "CrPC", "BNSS") else 2
    # Vocabulary density, capped so a long judgment cannot pass on length alone.
    score += min(len(CRIMINAL_TERMS.findall(text)), 30) // 3
    return score, statutes


def primary_law(statutes: list[str]) -> str:
    for candidate in ("BNS", "IPC"):
        if candidate in statutes:
            return candidate
    return statutes[0] if statutes else "other"


# ── topics ──────────────────────────────────────────────────────────────────
#
# These are coverage buckets for balancing the corpus, not legal
# classifications. A judgment can sit in several. The patterns are the same ones
# the expansion audit measured the existing 260 with, so the "before" numbers in
# docs/corpus_selection.md and the quotas below are on one scale.

TOPIC_PATTERNS = {
    "murder": r"\b(?:section|s\.|u/s)\s*(?:299|300|302|304)\b|culpable homicide",
    "dowry_cruelty": r"\b304-?B\b|\b498-?A\b|dowry death",
    "attempt": r"\b(?:307|511)\b.{0,40}(?:IPC|penal)|attempt to (?:murder|commit)",
    "common_intention": r"\bsection\s*34\b|\b149\b.{0,30}(?:IPC|penal)|common intention|unlawful assembly",
    "conspiracy": r"\b120-?[AB]\b|criminal conspiracy",
    "abetment": r"\b(?:107|109|306)\b.{0,30}(?:IPC|penal)|abetment",
    "cheating": r"\b(?:415|417|420)\b.{0,30}(?:IPC|penal)|cheating",
    "breach_of_trust": r"\b(?:405|406|409)\b.{0,30}(?:IPC|penal)|criminal breach of trust",
    "theft_robbery": r"\b(?:378|379|380|392|395|397)\b.{0,30}(?:IPC|penal)|dacoity|\brobbery\b|\btheft\b",
    "sexual_offences": r"\b(?:354|375|376)\b.{0,30}(?:IPC|penal)|\brape\b|outraging.{0,20}modesty",
    "pocso": r"\bPOCSO\b|protection of children from sexual offences",
    "ndps": r"\bNDPS\b|narcotic drugs and psychotropic",
    "bail": r"\banticipatory bail\b|\b(?:437|438|439)\b.{0,30}(?:CrPC|Cr\.P\.C|code)|\bbail\b",
    "quashing": r"\b482\b.{0,30}(?:CrPC|Cr\.P\.C|code)|inherent (?:power|jurisdiction).{0,30}quash",
    "sentencing": r"rarest of rare|sentencing policy|mitigating circumstance|death sentence|life imprisonment",
    "dying_declaration": r"dying declaration",
    "confession_recovery": r"\bsection\s*27\b.{0,40}evidence|extra-?judicial confession|disclosure statement",
    "circumstantial": r"circumstantial evidence|chain of circumstances",
    "new_codes": r"bharatiya nyaya sanhita|bharatiya nagarik suraksha|bharatiya sakshya|\bBNS\b|\bBNSS\b",
    "juvenile": r"juvenile justice|\bJJ Act\b|child in conflict with law",
    "pmla": r"\bPMLA\b|prevention of money[- ]laundering",
    "uapa": r"\bUAPA\b|unlawful activities \(prevention\)|\bTADA\b|\bPOTA\b",
    "corruption": r"prevention of corruption act|\bPC Act\b",
    "arms": r"\barms act\b",
}

TOPIC_RE = {name: re.compile(pattern, re.I) for name, pattern in TOPIC_PATTERNS.items()}


def topics_in(text: str) -> list[str]:
    return [name for name, pattern in TOPIC_RE.items() if pattern.search(text)]


# Floors expressed as judgment counts in the FINAL 800-judgment corpus, chosen
# from the audit's measured coverage. A topic below its floor pulls candidates
# in; a topic at its floor stops exerting pull.
TOPIC_FLOORS = {
    # Revised down from 100 on measurement, not preference. BNS density among
    # retained judgments is 4.9% (2024), 6.2% (2025), 33.3% (2026) — and 2026
    # holds only ~102 unexamined rows in the whole dataset. Exhausting all of
    # 2024-2026 under the existing quality bar yields roughly 45-50. A floor of
    # 100 was unreachable from this source and would only have forced the
    # quota-rescue tier to admit weak matter chasing a number that does not
    # exist. See docs/corpus_selection.md §4.
    # Source-limited, not effort-limited. Measured ceiling from this dataset is
    # ~82, and reaching it would cost 577 judgments — 82% of the 700 budget — at
    # POCSO's expense. This floor states what a proportionate allocation
    # achieves; the harvest reports what was actually found.
    "new_codes": 75,
    "sexual_offences": 80,
    "sentencing": 80,
    "common_intention": 80,
    "cheating": 60,
    "theft_robbery": 60,
    "conspiracy": 60,
    "quashing": 60,
    "circumstantial": 60,
    "pocso": 80,           # measured 10
    "ndps": 60,            # measured 14
    "attempt": 50,
    "abetment": 50,
    "breach_of_trust": 50,
    "dowry_cruelty": 50,
    "dying_declaration": 50,
    "confession_recovery": 50,
    "corruption": 40,
    "uapa": 30,
    "juvenile": 25,
    "pmla": 25,
}

# Ceilings stop the two dominant topics absorbing the expansion. They are a
# SHARE of the corpus target rather than an absolute count: at 800 judgments a
# flat 400 meant "half the corpus", but carried unchanged to 1,500 the same 400
# would have meant "27%", binding early and rejecting sound murder and bail
# judgments for no reason other than that the target moved.
#
# A candidate is rejected only when EVERY topic it covers is already at its
# ceiling — a murder case that also turns on a dying declaration still enters.
TOPIC_CEILING_SHARE = {
    "murder": 0.50,
    "bail": 0.50,
}


def topic_ceilings(target_total: int) -> dict[str, int]:
    return {topic: round(share * target_total)
            for topic, share in TOPIC_CEILING_SHARE.items()}


def unmet_floors(counts: dict[str, int]) -> set[str]:
    return {topic for topic, floor in TOPIC_FLOORS.items() if counts.get(topic, 0) < floor}


def over_ceiling(counts: dict[str, int], target_total: int) -> set[str]:
    return {topic for topic, cap in topic_ceilings(target_total).items()
            if counts.get(topic, 0) >= cap}


def admit(score: int, topics: list[str], counts: dict[str, int],
          target_total: int) -> tuple[bool, str]:
    """
    Decide a candidate that has already cleared the base gate.

    Returns (accepted, reason). The reason is recorded in the candidate ledger
    either way, so every decision can be traced to a rule.
    """
    blocked = over_ceiling(counts, target_total)
    if topics and set(topics) <= blocked:
        return False, "topic_ceiling"

    if score >= GENERAL_FILL_SCORE:
        return True, "general_fill"

    closes = set(topics) & unmet_floors(counts)
    if closes:
        return True, f"quota:{sorted(closes)[0]}"
    return False, "below_general_fill_score"


# ── strata ──────────────────────────────────────────────────────────────────
#
# Targets are the approved allocation. They are soft: a stratum that runs out of
# admissible candidates hands its remainder to the quota-fill pass rather than
# forcing weak material in to hit a number.

# Years already represented before the 1,500 expansion began.
EXISTING_YEARS = tuple(range(1973, 2001)) + tuple(range(2001, 2027))

# The 2,200 -> 3,000 round (+800). Every stratum name here is NEW, so each
# target is exactly what this round should harvest. Reusing a previous round's
# name would make the target CUMULATIVE against the records already tagged with
# it — `mid_2001_2017` needed a target of 290 last round to request 70 more.
STRATA = (
    {
        "name": "mid_depth_2001_2017",
        "years": tuple(range(2001, 2018)),
        "target": 300,
        "rationale": "The reservoir: ~5,300 qualifying candidates still unexamined at "
                     "the highest acceptance rate measured (39.2%). General criminal-law "
                     "depth at the lowest cost per retained judgment.",
    },
    {
        "name": "pocso_depth",
        "years": (2018, 2019, 2020, 2021, 2022, 2023),
        "target": 200,
        "rationale": "POCSO sits at 63 — only 1.1x its floor, the thinnest topic in the "
                     "corpus — and is 0% before 2018. ~900 qualifying candidates remain "
                     "here. Also carries PMLA (2.0x) and juvenile justice (2.3x).",
    },
    {
        "name": "early_depth_1950_1972",
        "years": tuple(range(1950, 1973)),
        "target": 140,
        "rationale": "~1,900 qualifying candidates at 25.1% acceptance. Early IPC "
                     "jurisprudence, still the thinnest era by judgment count.",
    },
    {
        "name": "bns_final",
        "years": (2024, 2025, 2026),
        "target": 100,
        "rationale": "Only ~464 unexamined rows and ~180 qualifying candidates remain in "
                     "the entire 2024-2026 band. This round substantially exhausts the "
                     "only source of BNS/BNSS/BSA judgments that exists.",
    },
    {
        "name": "year_completion",
        # The last four years from which no candidate has ever been examined.
        "years": (1971, 1972, 1998, 1999),
        "target": 60,
        "rationale": "Closes the final gaps in year coverage. These four years have never "
                     "had a single candidate examined; ~2,150 rows sit behind them.",
    },
)

TARGET_TOTAL = 3000


# ── download ordering ───────────────────────────────────────────────────────
#
# Ordering only. The expansion audit measured a state-party title proxy at 38%
# hit rate but only 68.7% recall against the known-criminal 260 — it misses
# landmarks such as Gudikanti Narasimhulu v. Public Prosecutor and Nandini
# Satpathy v. Dani, and it breaks on typos present in the source data ("STARE OF
# GUJARAT"). So it is never used to exclude a candidate, only to decide what to
# try first. Non-matching candidates are interleaved in at a fixed ratio so the
# 31% the proxy cannot see still enter the corpus.

PRIORITY_TITLE_RE = re.compile(
    r"\bSTATE\b|\bC\.?B\.?I\.?\b|CENTRAL BUREAU|\bN\.?C\.?T\.?\b|UNION TERRITORY|"
    r"\bPOLICE\b|NARCOTICS|CUSTOMS|DIRECTORATE OF ENFORCEMENT|GOVERNMENT OF|\bGOVT",
    re.I,
)

# For every 2 proxy-matched candidates tried, 1 non-matching candidate is tried.
INTERLEAVE_RATIO = 2
SHUFFLE_SEED = 20260820


def order_candidates(rows: list) -> list:
    """Interleave proxy-matched and non-matched candidates, deterministically."""
    import random

    matched, other = [], []
    for row in rows:
        (matched if PRIORITY_TITLE_RE.search(str(row.get("title") or "")) else other).append(row)
    random.Random(SHUFFLE_SEED).shuffle(other)

    ordered: list = []
    i = j = 0
    while i < len(matched) or j < len(other):
        for _ in range(INTERLEAVE_RATIO):
            if i < len(matched):
                ordered.append(matched[i])
                i += 1
        if j < len(other):
            ordered.append(other[j])
            j += 1
    return ordered
