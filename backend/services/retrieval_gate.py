"""
retrieval_gate.py — decide whether the LLM is called at all.

This is the one safeguard that cannot be argued around by a cleverly-worded
question, because it operates *before* the model sees anything. If retrieval did
not produce usable evidence, the pipeline returns the refusal and never makes the
API call. The model cannot answer from memory if it is never asked.

Why this is separate from `abstention.py`
-----------------------------------------
`abstention.py` scores retrieval quality and produces an advisory confidence
level. This module turns that assessment into a binary, enforced decision with a
deliberately conservative bias: when the gate and the advisory disagree, the gate
wins and the system stays silent.

Measured caveat — read before trusting the gate
-----------------------------------------------
Retrieval similarity does NOT cleanly separate answerable from unanswerable legal
questions. Over 38 answerable queries and 18 unanswerable probes
(`evaluation/results/abstention_extended.json`) the distributions overlap: the
highest unanswerable score, 0.5949, exceeds the lowest answerable one, 0.4915.
Near-domain legal questions — family law, labour law, revenue, IP — pass a
similarity gate because they share vocabulary with criminal statutes.

So this gate reliably stops far-domain questions and does NOT reliably stop
near-domain ones. It is one layer of three, not the answer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.services.abstention import AbstentionDecision  # noqa: E402


@dataclass
class GateDecision:
    allow_generation: bool
    reason: str
    evidence_count: int
    peak_similarity: float

    def to_dict(self) -> dict:
        return {
            "allow_generation": self.allow_generation,
            "reason": self.reason,
            "evidence_count": self.evidence_count,
            "peak_similarity": self.peak_similarity,
        }


def evaluate_gate(
    sources: list[dict],
    abstention: AbstentionDecision,
    *,
    min_sources: int = 1,
    min_peak: float | None = None,
) -> GateDecision:
    """Fail closed: generation proceeds only on positive evidence."""
    min_peak = config.ABSTAIN_SIM_HARD if min_peak is None else min_peak

    peak = max((float(s.get("retrieval_score", 0.0)) for s in sources), default=0.0)

    if not sources:
        return GateDecision(False, "retrieval returned no evidence", 0, 0.0)

    if len(sources) < min_sources:
        return GateDecision(
            False,
            f"only {len(sources)} passage(s) retrieved; {min_sources} required",
            len(sources),
            peak,
        )

    if peak < min_peak:
        return GateDecision(
            False,
            f"best passage scored {peak:.3f}, below the evidence floor of {min_peak}",
            len(sources),
            peak,
        )

    if abstention.should_abstain:
        # The advisory assessment saw something the raw score does not capture —
        # wrong statute, or no candidate clearing the relevance threshold.
        return GateDecision(
            False,
            "retrieval quality assessment advises abstention: "
            + "; ".join(abstention.reasons[:2]),
            len(sources),
            peak,
        )

    return GateDecision(
        True, "sufficient retrieved evidence to attempt a grounded answer", len(sources), peak
    )
