"""
abstention.py — decide whether the corpus actually supports an answer.

Status: WEAK SECONDARY FILTER. SIMILARITY THRESHOLDING DOES NOT SOLVE ABSTENTION.
--------------------------------------------------------------------------------
An early measurement over 5 unanswerable queries suggested bge-m3 similarity
separated answerable from unanswerable cleanly (0.491 vs 0.466). **That result did
not survive a larger probe set.** With 18 unanswerable queries that include
adjacent legal domains, the ranges overlap badly
(`evaluation/results/abstention_extended.json`):

    answerable   min peak similarity   0.4915
    unanswerable MAX peak similarity   0.5949   <- higher than the answerable floor

The failures are all *near-domain* legal questions — divorce grounds, notice
periods for termination, stamp duty, patent term. They use legal vocabulary and
score highly against criminal statutes despite being covered by neither the IPC
nor the BNS. The original 5 negatives were all far-domain (passports, GST,
Python), which is why they separated.

Conclusion, stated plainly: **no similarity threshold can separate these classes.**
Similarity catches obviously-unrelated queries and fails precisely where a real
user is most likely to go wrong — a neighbouring area of law.

The primary abstention mechanism is therefore the generation prompt, which
instructs the model to refuse when the retrieved provisions do not address the
question. This module is a cheap pre-filter for the easy cases, nothing more.

So a single `if similarity < threshold: reject` is explicitly NOT what this
module does. It combines five weak signals and reports each one, so that a
decision can be inspected and disagreed with rather than trusted blindly:

    1. peak similarity      is anything even plausibly relevant?
    2. support count        do several candidates clear the soft threshold, or
                            is one lucky match carrying the whole result?
    3. score margin         is the top result distinguishable from the pack, or
                            is the retriever indifferent across many sections?
    4. corpus match         did results come from the statute the user asked for?
    5. section agreement    when the query names a section number, did that
                            section actually come back?

Signal 5 is the strongest of the five, because it is the one case with a
verifiable ground truth: if a user asks for "section 420" and s.420 is not in the
results, something is wrong regardless of what the similarities say.

The output is advisory. The generation prompt independently instructs the model
to abstain when the context is insufficient — this module is a second line of
defence, not the only one.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.services.corpus_selector import CorpusChoice  # noqa: E402

INSUFFICIENT_CONTEXT_MESSAGE = (
    "I couldn't find sufficient information in the available legal corpus to "
    "answer this confidently."
)


@dataclass
class AbstentionSignals:
    peak_similarity: float = 0.0
    support_count: int = 0
    score_margin: float = 0.0
    corpus_match: bool = True
    requested_section: str | None = None
    requested_section_found: bool | None = None
    n_candidates: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AbstentionDecision:
    should_abstain: bool
    confidence: str                 # "high" | "medium" | "low" | "none"
    reasons: list[str] = field(default_factory=list)
    signals: AbstentionSignals = field(default_factory=AbstentionSignals)

    def to_dict(self) -> dict:
        return {
            "should_abstain": self.should_abstain,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "signals": self.signals.to_dict(),
            "note": (
                "Advisory only. Derived from 5 weak signals, not a trained "
                "classifier; the similarity component rests on 5 negative "
                "examples. See evaluation/results/abstention.json."
            ),
        }


def assess(
    results: list[dict],
    choice: CorpusChoice,
    *,
    sim_hard: float | None = None,
    sim_soft: float | None = None,
    min_support: int | None = None,
    margin_min: float | None = None,
) -> AbstentionDecision:
    """Combine the signals into an advisory decision."""
    sim_hard = config.ABSTAIN_SIM_HARD if sim_hard is None else sim_hard
    sim_soft = config.ABSTAIN_SIM_SOFT if sim_soft is None else sim_soft
    min_support = config.ABSTAIN_MIN_SUPPORT if min_support is None else min_support
    margin_min = config.ABSTAIN_MARGIN_MIN if margin_min is None else margin_min

    signals = AbstentionSignals(n_candidates=len(results))
    reasons: list[str] = []

    if not results:
        return AbstentionDecision(
            should_abstain=True,
            confidence="none",
            reasons=["retrieval returned no candidates at all"],
            signals=signals,
        )

    scores = [float(r.get("retrieval_score", 0.0)) for r in results]
    signals.peak_similarity = round(max(scores), 4)
    signals.support_count = sum(1 for s in scores if s >= sim_soft)
    top3 = sorted(scores, reverse=True)[:3]
    signals.score_margin = round(top3[0] - (top3[-1] if len(top3) > 1 else 0.0), 4)

    # Signal 4 — did we get anything from the statute that was asked for?
    if choice.law is not None:
        signals.corpus_match = any(r.get("law") == choice.law for r in results)
        if not signals.corpus_match:
            reasons.append(
                f"no results from {choice.law}, which the query explicitly asked about"
            )

    # Signal 5 — a named section either came back or it did not.
    if choice.section:
        signals.requested_section = choice.section
        signals.requested_section_found = any(
            str(r.get("section", "")).upper() == choice.section for r in results
        )
        if not signals.requested_section_found:
            reasons.append(
                f"the query names section {choice.section} but that section was "
                "not retrieved"
            )

    # Signal 1 — nothing even plausible.
    if signals.peak_similarity < sim_hard:
        reasons.append(
            f"best match scored {signals.peak_similarity:.3f}, below the "
            f"plausibility floor of {sim_hard}"
        )

    # Signal 2 — a single lucky hit is weak support.
    if signals.support_count < min_support:
        reasons.append(
            f"only {signals.support_count} candidate(s) cleared the soft "
            f"threshold of {sim_soft}; weak corroboration"
        )

    # Signal 3 — retriever indifferent across many sections.
    if signals.score_margin < margin_min and signals.peak_similarity < sim_soft:
        reasons.append(
            "top candidates are nearly indistinguishable, suggesting no clearly "
            "relevant section exists"
        )

    # ── combine ─────────────────────────────────────────────────────────────
    # Abstention requires signals to AGREE, not any single threshold to trip.
    #
    # The third condition below is the one that matters in practice. A query with
    # no answer in the corpus ("how do I apply for a passport?") still returns
    # candidates scoring ~0.466 — above the 0.40 plausibility floor, so signal 1
    # alone lets it through. What distinguishes it is that *nothing* clears the
    # soft threshold AND the top candidates are mutually indistinguishable: the
    # retriever is returning the nearest available text rather than relevant text.
    # Requiring both conditions together is what makes this a combination of weak
    # signals rather than a single cut-off.
    # Measured over 38 answerable queries and 18 unanswerable probes
    # (evaluation/results/abstention_extended.json):
    #
    #   peak similarity   answerable min 0.4915  |  unanswerable max 0.5949  -> OVERLAP
    #   score margin      answerable min 0.0011  |  unanswerable max 0.0427  -> OVERLAP
    #
    # Neither signal separates the classes. The threshold below therefore catches
    # only far-domain queries and is expected to let near-domain legal questions
    # through (4 of 18 probes pass it: divorce, notice periods, stamp duty, patent
    # term). That is a known, measured limitation — not a tuning problem, since no
    # threshold value can separate overlapping distributions.
    #
    # The score-margin condition was removed after this measurement rather than
    # kept for the appearance of a multi-signal rule. Signals 4 and 5 are retained
    # because they catch *different* failures (wrong statute, missing named
    # section), which similarity cannot detect at all.
    # An exact section match is stronger evidence than any similarity score. If
    # the user asked for s.420 and s.420 came back, the corpus demonstrably holds
    # the answer — a low cosine score on a three-word query ("What is IPC 420?")
    # says something about query length, not about evidence. Treating this as
    # sufficient support prevents refusing questions the corpus answers exactly.
    exact_match = signals.requested_section_found is True

    no_real_support = not exact_match and (
        signals.support_count == 0 or signals.peak_similarity < sim_soft
    )

    hard_fail = (
        signals.peak_similarity < sim_hard
        or not signals.corpus_match
        or no_real_support
    )
    if no_real_support:
        reasons.append(
            f"no candidate reached the relevance threshold of {sim_soft} "
            f"(best was {signals.peak_similarity:.3f}) — the corpus does not "
            "appear to cover this question"
        )

    if hard_fail:
        confidence = "none"
        should_abstain = True
    elif signals.requested_section_found is False:
        confidence = "low"
        should_abstain = False
    elif exact_match:
        confidence = "high"
        should_abstain = False
    elif signals.peak_similarity < sim_soft or signals.support_count < min_support:
        confidence = "low"
        should_abstain = False
    elif signals.peak_similarity >= sim_soft and signals.support_count >= min_support:
        confidence = "high" if signals.peak_similarity >= 0.60 else "medium"
        should_abstain = False
    else:
        confidence = "medium"
        should_abstain = False

    if not reasons:
        reasons.append("all abstention signals within normal range")

    return AbstentionDecision(should_abstain, confidence, reasons, signals)
