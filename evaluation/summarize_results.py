"""
summarize_results.py — turn raw experiment JSON into the results tables.

Reads evaluation/results/retrieval_experiments.json (produced by an actual run)
and writes evaluation/results/RESULTS.md. Every number is copied from the run
file; nothing is recomputed by hand and nothing is estimated.

Precision@K ceiling
-------------------
Most queries in the test set have a single gold section, so Precision@5 cannot
exceed |gold|/5 = 0.2 for those queries. Reporting a raw P@5 of 0.18 without
that context reads like a failure when it is in fact close to the maximum
attainable. The ceiling is therefore computed from the test set and reported
alongside, together with P@5 normalised by it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402

RESULTS_DIR = config.BASE_DIR / "evaluation" / "results"
RUN_PATH = RESULTS_DIR / "retrieval_experiments.json"
TEST_QUERIES = config.BASE_DIR / "evaluation" / "test_queries.json"
BUILD_STATS = RESULTS_DIR / "index_build_stats.json"
OUT_MD = RESULTS_DIR / "RESULTS.md"


def precision_ceilings(queries: list[dict], ks: list[int]) -> dict[int, float]:
    """Max attainable macro-averaged P@k given how many gold sections exist."""
    scored = [q for q in queries if q["category"] != "out_of_corpus"]
    ceilings = {}
    for k in ks:
        ceilings[k] = sum(min(q["gold_count"], k) / k for q in scored) / len(scored)
    return ceilings


def main() -> int:
    if not RUN_PATH.exists():
        print(f"missing {RUN_PATH}; run: python -m evaluation.evaluate_retrieval")
        return 1

    run_data = json.loads(RUN_PATH.read_text(encoding="utf-8"))
    queries = json.loads(TEST_QUERIES.read_text(encoding="utf-8"))
    build_stats = json.loads(BUILD_STATS.read_text()) if BUILD_STATS.exists() else []

    ks = run_data["metric_ks"]
    ceilings = precision_ceilings(queries, ks)
    runs = run_data["runs"]

    lines: list[str] = []
    add = lines.append

    add("# Retrieval Experiment Results\n")
    add(f"Generated from `{RUN_PATH.name}` — run at {run_data['generated_at']}.\n")
    add("All values are measured. No number in this file is estimated or hand-entered.\n")

    # ── corpus ──────────────────────────────────────────────────────────────
    add("## Corpus\n")
    add("| Property | Value |")
    add("| --- | --- |")
    statute_dir = config.BASE_DIR / "data" / "processed" / "statutes"
    for doc_id in ("IPC", "BNS"):
        path = statute_dir / f"{doc_id}_sections.json"
        if path.exists():
            add(f"| {doc_id} sections parsed | {len(json.loads(path.read_text())):,} |")
    chunk_dir = config.BASE_DIR / "data" / "processed" / "chunks"
    for chunk_file in sorted(chunk_dir.glob("statutes_*.json")):
        strategy = chunk_file.stem.replace("statutes_", "")
        add(f"| chunks — {strategy} | {len(json.loads(chunk_file.read_text())):,} |")
    scored = [q for q in queries if q["category"] != "out_of_corpus"]
    add(f"| test queries (total) | {len(queries)} |")
    add(f"| test queries (scored for ranking) | {len(scored)} |")
    add(f"| test queries (abstention only) | {len(queries) - len(scored)} |")
    add(f"| gold labels | {sum(q['gold_count'] for q in queries)} |")
    add("")

    # ── embeddings actually stored ──────────────────────────────────────────
    if build_stats:
        add("## Embeddings in ChromaDB\n")
        add("| Collection | Chunks | Embeddings | Dim | Build time |")
        add("| --- | ---: | ---: | ---: | ---: |")
        for stat in sorted(build_stats, key=lambda s: s["collection"]):
            seconds = stat.get("embed_seconds")
            timing = f"{seconds:.1f}s" if isinstance(seconds, (int, float)) else "cached"
            match = "" if stat["chunks"] == stat["embeddings"] else "  ⚠ MISMATCH"
            add(
                f"| `{stat['collection']}` | {stat['chunks']:,} | "
                f"{stat['embeddings']:,}{match} | {stat.get('dim', '')} | {timing} |"
            )
        add("")

    # ── main comparison ─────────────────────────────────────────────────────
    add("## Retrieval configurations\n")
    add(f"Precision@5 ceiling for this test set: **{ceilings[5]:.3f}** "
        "(most queries have a single gold section, so P@5 is capped at |gold|/5).\n")
    add("| Cfg | Configuration | Model | Chunking | P@5 | P@5 / ceiling | R@5 | MRR | nDCG@5 | Hit@5 | p50 ms | p95 ms |")
    add("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for run in runs:
        m = run["metrics"]
        lat = run["latency"]
        normalised = m["precision@5"] / ceilings[5] if ceilings[5] else 0.0
        add(
            f"| {run['config']} | {run['label']} | {run['embedding_model']} | "
            f"{run['chunking_strategy']} | {m['precision@5']:.3f} | {normalised:.3f} | "
            f"{m['recall@5']:.3f} | {m['mrr']:.3f} | {m['ndcg@5']:.3f} | "
            f"{m['hit_rate@5']:.3f} | {lat['p50_ms']:.1f} | {lat['p95_ms']:.1f} |"
        )
    add("")

    # ── full metric sweep ───────────────────────────────────────────────────
    add("## Metrics at every K\n")
    for run in runs:
        m = run["metrics"]
        add(f"### {run['config']} — {run['label']} ({run['embedding_model']})\n")
        add("| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |")
        add("| ---: | ---: | ---: | ---: | ---: |")
        for k in ks:
            add(
                f"| {k} | {m[f'precision@{k}']:.3f} | {m[f'recall@{k}']:.3f} | "
                f"{m[f'hit_rate@{k}']:.3f} | {m[f'ndcg@{k}']:.3f} |"
            )
        add(f"\nMRR: **{m['mrr']:.3f}**\n")

    # ── per category ────────────────────────────────────────────────────────
    add("## Hit rate@5 by query category\n")
    categories = sorted({c for run in runs for c in run["metrics_by_category"]})
    add("| Cfg | Model | " + " | ".join(categories) + " |")
    add("| --- | --- | " + " | ".join("---:" for _ in categories) + " |")
    for run in runs:
        cells = []
        for category in categories:
            value = run["metrics_by_category"].get(category, {}).get("hit_rate@5")
            cells.append(f"{value:.3f}" if value is not None else "—")
        add(f"| {run['config']} | {run['embedding_model']} | " + " | ".join(cells) + " |")
    add("")

    # ── latency ─────────────────────────────────────────────────────────────
    add("## Retrieval latency\n")
    add("End-to-end per query: query embedding + vector search + BM25 + fusion + reranking. "
        "Measured on the host that ran the experiment; no warm-up runs were discarded.\n")
    add("| Cfg | Model | mean | p50 | p95 | min | max |")
    add("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for run in runs:
        lat = run["latency"]
        add(
            f"| {run['config']} | {run['embedding_model']} | {lat['mean_ms']:.1f} | "
            f"{lat['p50_ms']:.1f} | {lat['p95_ms']:.1f} | {lat['min_ms']:.1f} | {lat['max_ms']:.1f} |"
        )
    add("")

    # ── abstention ──────────────────────────────────────────────────────────
    add("## Abstention probe (out-of-corpus queries)\n")
    add("These five queries have no answer anywhere in the corpus. A retrieval score "
        "close to the in-corpus range means similarity alone cannot be used as an "
        "abstention signal — the generator has to be told to abstain.\n")
    add("| Cfg | Query | Top dense similarity |")
    add("| --- | --- | ---: |")
    for run in runs:
        for row in run["per_query"]:
            if row["category"] == "out_of_corpus":
                add(f"| {run['config']} | {row['query'][:58]} | {row.get('top_dense_similarity', float('nan')):.4f} |")
    add("")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"  {len(runs)} runs summarised")
    print(f"  P@5 ceiling = {ceilings[5]:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
