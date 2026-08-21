"""
evaluate_abstention.py — can retrieval similarity tell "no answer exists"?

A legal QA system must be able to say "the corpus does not support an answer"
rather than assemble a confident response from whatever came back highest. The
cheapest possible abstention mechanism is a threshold on the top retrieval
similarity. This script measures whether that mechanism is even viable, by
comparing the top similarity for queries that DO have an answer in the corpus
against queries that do not.

If the two ranges overlap, a threshold cannot separate them and abstention has to
be enforced at the generation step instead (prompt-level instruction plus a
grounding check), not by a similarity cut-off.

Results are written to evaluation/results/abstention.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from evaluation.evaluate_retrieval import DenseRetriever  # noqa: E402

logger = logging.getLogger(__name__)

RESULTS_DIR = config.BASE_DIR / "evaluation" / "results"
TEST_QUERIES = config.BASE_DIR / "evaluation" / "test_queries.json"
OUT_PATH = RESULTS_DIR / "abstention.json"


def evaluate(model_key: str, strategy: str = "section_whole") -> dict:
    retriever = DenseRetriever(strategy, model_key)
    queries = json.loads(TEST_QUERIES.read_text(encoding="utf-8"))

    in_corpus: list[dict] = []
    out_corpus: list[dict] = []
    for item in queries:
        top = retriever.search(item["query"], 1)
        score = float(top[0][1]) if top else 0.0
        row = {"query_id": item["query_id"], "query": item["query"], "top_similarity": round(score, 4)}
        (out_corpus if item["category"] == "out_of_corpus" else in_corpus).append(row)

    in_scores = [r["top_similarity"] for r in in_corpus]
    out_scores = [r["top_similarity"] for r in out_corpus]

    separable = min(in_scores) > max(out_scores)
    margin = min(in_scores) - max(out_scores)

    return {
        "model": model_key,
        "strategy": strategy,
        "in_corpus": {
            "n": len(in_scores),
            "min": round(min(in_scores), 4),
            "mean": round(statistics.mean(in_scores), 4),
            "max": round(max(in_scores), 4),
        },
        "out_of_corpus": {
            "n": len(out_scores),
            "min": round(min(out_scores), 4),
            "mean": round(statistics.mean(out_scores), 4),
            "max": round(max(out_scores), 4),
        },
        "threshold_separable": separable,
        "margin": round(margin, 4),
        # A viable threshold sits between the two ranges; only meaningful if separable.
        "suggested_threshold": round((min(in_scores) + max(out_scores)) / 2, 4) if separable else None,
        "caveat": (
            f"Based on {len(out_scores)} out-of-corpus queries only. A margin this "
            "size is suggestive, not robust — treat a similarity threshold as a "
            "secondary signal, never as the sole abstention mechanism."
        ),
        "per_query_out_of_corpus": out_corpus,
    }


def evaluate_production_decisions() -> dict:
    """Score the live multi-signal abstention rule on the same 43 queries.

    ⚠ THIS IS A FIT, NOT A GENERALISATION ESTIMATE.

    The thresholds in config.py were chosen after looking at the similarity
    ranges measured on this very test set. Scoring the rule on the same queries
    therefore measures whether the rule expresses what was observed — not whether
    it will hold on unseen queries. A held-out set would be needed for that, and
    there is not one. Report the number with this sentence attached or not at all.
    """
    from backend.services.statute_rag import statute_rag

    queries = json.loads(TEST_QUERIES.read_text(encoding="utf-8"))
    rows = []
    for item in queries:
        result = statute_rag.retrieve(item["query"])
        decision = result["abstention"]
        unanswerable = item["category"] == "out_of_corpus"
        rows.append(
            {
                "query_id": item["query_id"],
                "category": item["category"],
                "unanswerable": unanswerable,
                "abstained": decision["should_abstain"],
                "confidence": decision["confidence"],
                "correct": decision["should_abstain"] == unanswerable,
                "peak_similarity": decision["signals"]["peak_similarity"],
                "support_count": decision["signals"]["support_count"],
            }
        )

    answerable = [r for r in rows if not r["unanswerable"]]
    unanswerable = [r for r in rows if r["unanswerable"]]
    return {
        "rule": "multi-signal (see backend/services/abstention.py)",
        "thresholds": {
            "sim_hard": config.ABSTAIN_SIM_HARD,
            "sim_soft": config.ABSTAIN_SIM_SOFT,
            "min_support": config.ABSTAIN_MIN_SUPPORT,
            "margin_min": config.ABSTAIN_MARGIN_MIN,
        },
        "answerable_total": len(answerable),
        "answerable_correctly_answered": sum(1 for r in answerable if not r["abstained"]),
        "false_abstentions": sum(1 for r in answerable if r["abstained"]),
        "unanswerable_total": len(unanswerable),
        "unanswerable_correctly_abstained": sum(1 for r in unanswerable if r["abstained"]),
        "missed_abstentions": sum(1 for r in unanswerable if not r["abstained"]),
        "VALIDITY_WARNING": (
            "Thresholds were selected by inspecting similarity ranges on THIS test "
            "set, so this is a fit to the data, not an estimate of generalisation. "
            "Only 5 unanswerable queries exist. Do not report this as accuracy."
        ),
        "per_query": rows,
    }


PROBES_PATH = config.BASE_DIR / "evaluation" / "abstention_probes.json"


def evaluate_extended() -> dict:
    """Score the live rule against the 38 answerable queries + all 18 probes.

    ⚠ Thresholds in config.py were chosen by inspecting these same distributions.
    There is no held-out set, so the numbers below describe fit, not generalisation.
    They are still more informative than the original 5-negative measurement,
    which is the only reason this exists.
    """
    from backend.services.statute_rag import statute_rag

    answerable = [
        q for q in json.loads(TEST_QUERIES.read_text(encoding="utf-8"))
        if q["category"] != "out_of_corpus"
    ]
    probes = json.loads(PROBES_PATH.read_text(encoding="utf-8"))["probes"]

    rows = []
    for item in answerable:
        result = statute_rag.retrieve(item["query"])
        signals = result["abstention"]["signals"]
        rows.append({
            "id": item["query_id"], "query": item["query"], "answerable": True,
            "abstained": result["abstention"]["should_abstain"],
            "peak_similarity": signals["peak_similarity"],
            "score_margin": signals["score_margin"],
        })
    for probe in probes:
        result = statute_rag.retrieve(probe["query"])
        signals = result["abstention"]["signals"]
        rows.append({
            "id": probe["id"], "query": probe["query"], "answerable": False,
            "domain": probe.get("domain", ""),
            "abstained": result["abstention"]["should_abstain"],
            "peak_similarity": signals["peak_similarity"],
            "score_margin": signals["score_margin"],
        })

    yes = [r for r in rows if r["answerable"]]
    no = [r for r in rows if not r["answerable"]]
    in_peaks = [r["peak_similarity"] for r in yes]
    out_peaks = [r["peak_similarity"] for r in no]
    in_margins = [r["score_margin"] for r in yes]
    out_margins = [r["score_margin"] for r in no]

    false_abstentions = [r for r in yes if r["abstained"]]
    missed = [r for r in no if not r["abstained"]]

    return {
        "answerable_n": len(yes),
        "unanswerable_n": len(no),
        "false_abstentions": len(false_abstentions),
        "false_abstention_queries": [r["query"] for r in false_abstentions],
        "missed_abstentions": len(missed),
        "missed_abstention_queries": [r["query"] for r in missed],
        "peak_similarity": {
            "answerable_min": round(min(in_peaks), 4),
            "answerable_median": round(statistics.median(in_peaks), 4),
            "unanswerable_max": round(max(out_peaks), 4),
            "unanswerable_median": round(statistics.median(out_peaks), 4),
            "separable": min(in_peaks) > max(out_peaks),
            "gap": round(min(in_peaks) - max(out_peaks), 4),
        },
        "score_margin": {
            "answerable_min": round(min(in_margins), 4),
            "unanswerable_max": round(max(out_margins), 4),
            "separable": min(in_margins) > max(out_margins),
            "gap": round(min(in_margins) - max(out_margins), 4),
            "conclusion": (
                "Does not separate answerable from unanswerable queries. Removed "
                "from the abstention decision after this measurement."
            ),
        },
        "thresholds_used": {
            "sim_hard": config.ABSTAIN_SIM_HARD,
            "sim_soft": config.ABSTAIN_SIM_SOFT,
            "min_support": config.ABSTAIN_MIN_SUPPORT,
        },
        "VALIDITY_WARNING": (
            "Thresholds were selected by inspecting these distributions. No held-out "
            "set exists, so this measures fit, not generalisation. The separating gap "
            "is a few hundredths of a similarity point and is model-dependent — "
            "bge-base showed no separation at all. Treat abstention as a research "
            "feature, not a validated classifier."
        ),
        "per_query": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="bge-base,bge-m3")
    parser.add_argument("--strategy", default="section_whole")
    parser.add_argument(
        "--production",
        action="store_true",
        help="also score the live multi-signal rule (writes abstention_production.json)",
    )
    parser.add_argument(
        "--extended",
        action="store_true",
        help="score against the 18-probe negative set (writes abstention_extended.json)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    results = [evaluate(m.strip(), args.strategy) for m in args.models.split(",")]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runs": results},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"\n{'model':12} {'in-corpus min':>14} {'out-corpus max':>15} {'margin':>9}  separable")
    print("-" * 68)
    for row in results:
        print(
            f"{row['model']:12} {row['in_corpus']['min']:14.3f} "
            f"{row['out_of_corpus']['max']:15.3f} {row['margin']:9.3f}  "
            f"{'YES' if row['threshold_separable'] else 'NO — ranges overlap'}"
        )
    print(f"\nwrote {OUT_PATH}")

    if args.production:
        report = evaluate_production_decisions()
        production_path = RESULTS_DIR / "abstention_production.json"
        production_path.write_text(
            json.dumps(
                {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **report},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"\nproduction rule: "
            f"{report['answerable_correctly_answered']}/{report['answerable_total']} answerable answered, "
            f"{report['unanswerable_correctly_abstained']}/{report['unanswerable_total']} unanswerable abstained"
        )
        print(f"  ⚠ {report['VALIDITY_WARNING']}")
        print(f"wrote {production_path}")

    if args.extended:
        report = evaluate_extended()
        extended_path = RESULTS_DIR / "abstention_extended.json"
        extended_path.write_text(
            json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **report}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        peak = report["peak_similarity"]
        margin = report["score_margin"]
        print(
            f"\nextended ({report['answerable_n']} answerable / "
            f"{report['unanswerable_n']} unanswerable):"
        )
        print(f"  false abstentions : {report['false_abstentions']}")
        print(f"  missed abstentions: {report['missed_abstentions']}")
        print(
            f"  peak similarity   : answerable min {peak['answerable_min']} vs "
            f"unanswerable max {peak['unanswerable_max']}  separable={peak['separable']}"
        )
        print(
            f"  score margin      : answerable min {margin['answerable_min']} vs "
            f"unanswerable max {margin['unanswerable_max']}  separable={margin['separable']}"
        )
        for query in report["missed_abstention_queries"]:
            print(f"    MISSED: {query}")
        print(f"wrote {extended_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
