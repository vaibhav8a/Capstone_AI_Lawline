"""
evaluate_legal_retrieval.py — judgment, combined and false-premise benchmarks.

Writes to evaluation/results/legal_retrieval_*.json. Never touches
retrieval_experiments.json, index_build_stats.json, abstention*.json,
gate_evaluation.json or RESULTS.md.

    --tune       grid-search case weights on the DEV split only
    --benchmark  score the selected weights on DEV and HELD_OUT

Discipline: `--tune` refuses to read the held-out split, so the tuning loop
cannot see the data it will later be judged on.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from backend.services.legal_rag import CaseWeights, legal_rag  # noqa: E402
from evaluation.legal_eval_sets import (  # noqa: E402
    COMBINED_DEV,
    COMBINED_HELD_OUT,
    FALSE_PREMISE_DEV,
    FALSE_PREMISE_HELD_OUT,
    JUDGMENT_DEV,
    JUDGMENT_HELD_OUT,
    judgment_gold,
)
from evaluation.metrics_ir import ndcg_at_k, reciprocal_rank  # noqa: E402

logger = logging.getLogger(__name__)
RESULTS_DIR = config.BASE_DIR / "evaluation" / "results"


def _case_key(candidate: dict) -> str:
    return candidate.get("neutral_citation") or candidate.get("case_name", "")


def score_judgment_split(split, weights: CaseWeights) -> dict:
    """Recall@5 / MRR / nDCG@5 over judgment retrieval."""
    rows, recalls, mrrs, ndcgs, latencies = [], [], [], [], []

    for query, statute, section in split:
        gold = judgment_gold(statute, section)
        start = time.perf_counter()
        result = legal_rag.retrieve(query, weights=weights)
        latencies.append((time.perf_counter() - start) * 1000)

        ranked = [_case_key(j) for j in result["judgments"]]
        hits = [k for k in ranked if k in gold]

        # Recall@5 is capped by the 5-judgment display limit: with 72 gold cases
        # for s.302, perfect retrieval still yields 5/72. Recall@5 relative to
        # min(|gold|, 5) is the meaningful quantity and is reported alongside.
        recall = len(hits) / len(gold) if gold else 0.0
        capped = len(hits) / min(len(gold), 5) if gold else 0.0
        recalls.append(capped)
        mrrs.append(reciprocal_rank(ranked, gold))
        ndcgs.append(ndcg_at_k(ranked, gold, 5))

        rows.append({
            "query": query,
            "target": f"{statute} s.{section}",
            "gold_size": len(gold),
            "returned": len(ranked),
            "hits": len(hits),
            "recall_at_5_raw": round(recall, 4),
            "recall_at_5_capped": round(capped, 4),
            "mrr": round(reciprocal_rank(ranked, gold), 4),
            "top_cases": ranked[:5],
            "query_type": result["routing"]["query_type"],
        })

    n = len(split)
    return {
        "n": n,
        "recall_at_5_capped": round(sum(recalls) / n, 4),
        "mrr": round(sum(mrrs) / n, 4),
        "ndcg_at_5": round(sum(ndcgs) / n, 4),
        "mean_latency_ms": round(sum(latencies) / n, 2),
        "per_query": rows,
    }


def score_combined_split(split, weights: CaseWeights) -> dict:
    """Both the statute AND supporting judgments must come back."""
    rows, statute_hits, both_hits, latencies = [], 0, 0, []

    for query, statute, section in split:
        gold = judgment_gold(statute, section)
        start = time.perf_counter()
        result = legal_rag.retrieve(query, weights=weights)
        latencies.append((time.perf_counter() - start) * 1000)

        statute_found = any(
            s.get("law") == statute and str(s.get("section", "")).upper() == section.upper()
            for s in result["statutes"]
        )
        judgment_found = any(_case_key(j) in gold for j in result["judgments"])

        statute_hits += statute_found
        both_hits += statute_found and judgment_found

        rows.append({
            "query": query,
            "target": f"{statute} s.{section}",
            "statute_retrieved": statute_found,
            "supporting_judgment_retrieved": judgment_found,
            "both": statute_found and judgment_found,
            "n_statutes": len(result["statutes"]),
            "n_judgments": len(result["judgments"]),
            "query_type": result["routing"]["query_type"],
        })

    n = len(split)
    return {
        "n": n,
        "statute_hit_rate": round(statute_hits / n, 4),
        "both_hit_rate": round(both_hits / n, 4),
        "mean_latency_ms": round(sum(latencies) / n, 2),
        "per_query": rows,
    }


def score_false_premise(split) -> dict:
    """Correct behaviour is refusal. Anything else is a false acceptance."""
    rows, refused = [], 0
    by_category: dict[str, list[bool]] = {}

    for query, category, why in split:
        result = legal_rag.retrieve(query)
        did_refuse = not result["answerable"] and bool(result.get("premise_problems"))
        refused += did_refuse
        by_category.setdefault(category, []).append(did_refuse)
        rows.append({
            "query": query,
            "category": category,
            "expected": "refuse",
            "refused": did_refuse,
            "query_type": result["routing"]["query_type"],
            "detected": result.get("premise_problems", [])[:1],
            "why_unsupported": why,
        })

    n = len(split)
    return {
        "n": n,
        "refusal_rate": round(refused / n, 4),
        "false_acceptance_rate": round(1 - refused / n, 4),
        "by_category": {
            k: {"n": len(v), "refused": sum(v), "rate": round(sum(v) / len(v), 4)}
            for k, v in by_category.items()
        },
        "per_query": rows,
    }


def tune(grid_steps: int = 3) -> dict:
    """Grid-search case weights on DEV. Held-out is not loaded here."""
    section_values = [0.0, 0.25, 0.5, 0.75]
    xref_values = [0.0, 0.25, 0.5]
    law_values = [0.0, 0.15]

    trials = []
    best, best_score = None, -1.0
    for section, xref, law in itertools.product(section_values, xref_values, law_values):
        weights = CaseWeights(1.0, section, law, xref)
        result = score_judgment_split(JUDGMENT_DEV, weights)
        # Selection criterion stated up front: nDCG@5, which rewards putting
        # genuinely-citing judgments high rather than merely including them.
        objective = result["ndcg_at_5"]
        trials.append({
            "weights": weights.to_dict(),
            "ndcg_at_5": objective,
            "recall_at_5_capped": result["recall_at_5_capped"],
            "mrr": result["mrr"],
        })
        if objective > best_score:
            best, best_score = weights, objective

    trials.sort(key=lambda t: t["ndcg_at_5"], reverse=True)
    return {
        "split": "DEV only — held-out never loaded during tuning",
        "objective": "ndcg@5",
        "n_trials": len(trials),
        "best_weights": best.to_dict(),
        "best_ndcg_at_5": round(best_score, 4),
        "top_trials": trials[:8],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tune", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.tune:
        report = tune()
        (RESULTS_DIR / "case_weight_search.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "top_trials"}, indent=2))
        print("\ntop trials:")
        for trial in report["top_trials"][:5]:
            w = trial["weights"]
            print(f"  sec={w['section_match']:<5} xref={w['cross_reference']:<5} "
                  f"law={w['law_match']:<5} -> nDCG@5={trial['ndcg_at_5']:.4f} "
                  f"R@5={trial['recall_at_5_capped']:.4f}")
        return 0

    if args.benchmark:
        weights = CaseWeights()
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "weights_used": weights.to_dict(),
            "gold_label_caveat": (
                "Judgment gold labels are derived by weak supervision (a judgment "
                "cites the section near the statute name). They measure citation "
                "retrieval, not human-judged relevance."
            ),
            "judgment_dev": score_judgment_split(JUDGMENT_DEV, weights),
            "judgment_held_out": score_judgment_split(JUDGMENT_HELD_OUT, weights),
            "combined_dev": score_combined_split(COMBINED_DEV, weights),
            "combined_held_out": score_combined_split(COMBINED_HELD_OUT, weights),
            "false_premise_dev": score_false_premise(FALSE_PREMISE_DEV),
            "false_premise_held_out": score_false_premise(FALSE_PREMISE_HELD_OUT),
        }
        (RESULTS_DIR / "legal_retrieval_benchmark.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        print(f"\n{'benchmark':26} {'metric':22} {'dev':>9} {'held-out':>9}")
        print("-" * 70)
        jd, jh = payload["judgment_dev"], payload["judgment_held_out"]
        print(f"{'judgment retrieval':26} {'Recall@5 (capped)':22} {jd['recall_at_5_capped']:9.4f} {jh['recall_at_5_capped']:9.4f}")
        print(f"{'':26} {'MRR':22} {jd['mrr']:9.4f} {jh['mrr']:9.4f}")
        print(f"{'':26} {'nDCG@5':22} {jd['ndcg_at_5']:9.4f} {jh['ndcg_at_5']:9.4f}")
        cd, ch = payload["combined_dev"], payload["combined_held_out"]
        print(f"{'combined retrieval':26} {'statute hit rate':22} {cd['statute_hit_rate']:9.4f} {ch['statute_hit_rate']:9.4f}")
        print(f"{'':26} {'statute+case hit rate':22} {cd['both_hit_rate']:9.4f} {ch['both_hit_rate']:9.4f}")
        fd, fh = payload["false_premise_dev"], payload["false_premise_held_out"]
        print(f"{'false premise':26} {'refusal rate':22} {fd['refusal_rate']:9.4f} {fh['refusal_rate']:9.4f}")
        print(f"{'':26} {'false acceptance':22} {fd['false_acceptance_rate']:9.4f} {fh['false_acceptance_rate']:9.4f}")
        print(f"\nlatency (mean ms): judgment {jh['mean_latency_ms']}, combined {ch['mean_latency_ms']}")
        return 0

    parser.error("pass --tune or --benchmark")


if __name__ == "__main__":
    raise SystemExit(main())
