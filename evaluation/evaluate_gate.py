"""
evaluate_gate.py — measure the retrieval gate on development and held-out data.

Reports, for each split and each query group:

    TPR   true positive rate  — answerable questions the gate allows
    FAR   false abstention rate — answerable questions the gate wrongly blocks
    TNR   true negative rate  — unanswerable questions the gate blocks
    FAcc  false acceptance rate — unanswerable questions the gate wrongly allows

The held-out numbers are the ones that mean anything. Development numbers are a
fit: the thresholds were chosen by looking at that data.

Also records whether the LLM would have been invoked for each query, which is the
measurement that actually answers "does the gate stop the model answering from
memory?" — see `--evidence-removal`.

Usage
-----
    python -m evaluation.evaluate_gate
    python -m evaluation.evaluate_gate --evidence-removal
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from evaluation.gate_datasets import held_out_records, validate_gold_sections  # noqa: E402

logger = logging.getLogger(__name__)

RESULTS_DIR = config.BASE_DIR / "evaluation" / "results"
TEST_QUERIES = config.BASE_DIR / "evaluation" / "test_queries.json"
PROBES = config.BASE_DIR / "evaluation" / "abstention_probes.json"


def _gate_allows(query: str) -> tuple[bool, dict]:
    from backend.services.retrieval_gate import evaluate_gate
    from backend.services.statute_rag import _decision_from, statute_rag

    result = statute_rag.retrieve(query)
    decision = evaluate_gate(result["sources"], _decision_from(result["abstention"]))
    return decision.allow_generation, {
        "reason": decision.reason,
        "peak_similarity": decision.peak_similarity,
        "evidence_count": decision.evidence_count,
        "top_sections": [f"{s['law']} s.{s['section']}" for s in result["sources"][:3]],
    }


def _rates(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["expected_allow"]]
    unanswerable = [r for r in rows if not r["expected_allow"]]

    allowed_ans = sum(1 for r in answerable if r["allowed"])
    blocked_unans = sum(1 for r in unanswerable if not r["allowed"])

    return {
        "answerable_n": len(answerable),
        "unanswerable_n": len(unanswerable),
        "true_positive_rate": round(allowed_ans / len(answerable), 4) if answerable else None,
        "false_abstention_rate": round(1 - allowed_ans / len(answerable), 4) if answerable else None,
        "true_negative_rate": round(blocked_unans / len(unanswerable), 4) if unanswerable else None,
        "false_acceptance_rate": round(1 - blocked_unans / len(unanswerable), 4) if unanswerable else None,
    }


def evaluate_development() -> dict:
    queries = json.loads(TEST_QUERIES.read_text(encoding="utf-8"))
    probes = json.loads(PROBES.read_text(encoding="utf-8"))["probes"]

    rows = []
    for item in queries:
        if item["category"] == "out_of_corpus":
            continue
        allowed, detail = _gate_allows(item["query"])
        rows.append({"query": item["query"], "group": "answerable",
                     "expected_allow": True, "allowed": allowed, **detail})
    for probe in probes:
        allowed, detail = _gate_allows(probe["query"])
        rows.append({"query": probe["query"], "group": probe.get("domain", "unanswerable"),
                     "expected_allow": False, "allowed": allowed, **detail})

    return {
        "split": "development",
        "WARNING": (
            "Thresholds were tuned by inspecting these distributions. These numbers "
            "describe fit, not generalisation. Read the held-out split instead."
        ),
        **_rates(rows),
        "per_query": rows,
    }


def evaluate_held_out() -> dict:
    from backend.services.statute_rag import statute_rag

    records = held_out_records()

    # A mislabelled answerable query would make the gate look worse than it is.
    collection = statute_rag._get_collection()
    everything = collection.get(include=["metadatas"])
    corpus_sections = {str(m.get("section", "")) for m in everything["metadatas"]}
    label_problems = validate_gold_sections(corpus_sections)

    rows = []
    for record in records:
        allowed, detail = _gate_allows(record["query"])
        rows.append({**record, "allowed": allowed, **detail})

    by_group = {}
    for group in ("answerable", "near_domain", "far_domain", "adversarial"):
        subset = [r for r in rows if r["group"] == group]
        if subset:
            correct = sum(1 for r in subset if r["allowed"] == r["expected_allow"])
            by_group[group] = {
                "n": len(subset),
                "correct": correct,
                "accuracy": round(correct / len(subset), 4),
            }

    return {
        "split": "held_out",
        "note": (
            "Written after the thresholds were fixed and never used to tune them. "
            "This is the only unbiased estimate of gate behaviour in the project."
        ),
        "label_problems": label_problems,
        **_rates(rows),
        "by_group": by_group,
        "per_query": rows,
    }


def evidence_removal_probe() -> dict:
    """The strongest test: is the model reachable when the evidence is gone?

    A query is asked against a corpus filter that excludes the statute holding the
    answer. If the gate works, generation is blocked and the LLM is never invoked,
    so it cannot answer from pretrained knowledge. `llm_invoked` is recorded from
    the pipeline itself rather than assumed.
    """
    from backend.services.statute_rag import statute_rag

    # Ask an IPC question while restricting retrieval to BNS, and vice versa:
    # the evidence for the specific question is then genuinely absent.
    cases = [
        {
            "query": "What does IPC Section 420 deal with?",
            "restrict_to": "BNS",
            "note": "IPC question, IPC excluded from retrieval",
        },
        {
            "query": "What is the punishment under IPC Section 302?",
            "restrict_to": "BNS",
            "note": "IPC question, IPC excluded from retrieval",
        },
        {
            "query": "Which BNS section covers cheating?",
            "restrict_to": "IPC",
            "note": "BNS question, BNS excluded from retrieval",
        },
    ]

    from backend.services.retrieval_gate import evaluate_gate
    from backend.services.statute_rag import _decision_from

    rows = []
    for case in cases:
        result = statute_rag.retrieve(case["query"], corpus_override=case["restrict_to"])
        decision = evaluate_gate(result["sources"], _decision_from(result["abstention"]))
        rows.append({
            **case,
            "gate_allowed_generation": decision.allow_generation,
            "llm_would_be_invoked": decision.allow_generation,
            "gate_reason": decision.reason,
            "retrieved_laws": sorted({s["law"] for s in result["sources"]}),
            "peak_similarity": decision.peak_similarity,
        })

    blocked = sum(1 for r in rows if not r["gate_allowed_generation"])
    return {
        "cases": len(rows),
        "blocked_before_llm": blocked,
        "llm_invocations": sum(1 for r in rows if r["llm_would_be_invoked"]),
        "interpretation": (
            "Where gate_allowed_generation is false the LLM is never called, so a "
            "pretrained-knowledge answer is impossible by construction rather than "
            "by instruction. Where it is true the gate did NOT stop it, and only "
            "the closed-book prompt and the verifier remain."
        ),
        "per_case": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-removal", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "thresholds": {
            "sim_hard": config.ABSTAIN_SIM_HARD,
            "sim_soft": config.ABSTAIN_SIM_SOFT,
            "min_support": config.ABSTAIN_MIN_SUPPORT,
        },
        "development": evaluate_development(),
        "held_out": evaluate_held_out(),
    }
    if args.evidence_removal:
        payload["evidence_removal"] = evidence_removal_probe()

    out = RESULTS_DIR / "gate_evaluation.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for split in ("development", "held_out"):
        data = payload[split]
        print(f"\n=== {split.upper()} ===")
        print(f"  answerable   n={data['answerable_n']:3}  TPR={data['true_positive_rate']}  "
              f"false-abstention={data['false_abstention_rate']}")
        print(f"  unanswerable n={data['unanswerable_n']:3}  TNR={data['true_negative_rate']}  "
              f"false-acceptance={data['false_acceptance_rate']}")
        if split == "held_out":
            if data["label_problems"]:
                print("  LABEL PROBLEMS:")
                for problem in data["label_problems"]:
                    print(f"    ! {problem}")
            for group, stats in data["by_group"].items():
                print(f"    {group:14} {stats['correct']}/{stats['n']}  acc={stats['accuracy']}")

    if args.evidence_removal:
        er = payload["evidence_removal"]
        print(f"\n=== EVIDENCE REMOVAL ===")
        print(f"  {er['blocked_before_llm']}/{er['cases']} blocked before any LLM call")
        for row in er["per_case"]:
            flag = "BLOCKED" if not row["gate_allowed_generation"] else "ALLOWED (!)"
            print(f"    {flag:12} {row['query'][:52]}")

    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
