"""
corpus_stats.py — describe the judgment corpus as it actually stands.

Reads judgments.json, the candidate ledger and the index stats and prints a
single report: size, era and topic coverage, provenance completeness, harvest
economics and index state. Everything is computed from the files themselves, so
the report cannot drift from the corpus the way a hand-maintained summary does.

Usage
-----
    python -m backend.ingestion.corpus_stats
    python -m backend.ingestion.corpus_stats --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.ingestion.corpus_selection import TOPIC_FLOORS, topics_in  # noqa: E402
from backend.ingestion.fetch_judgments import (  # noqa: E402
    JUDGMENTS_PATH,
    LEDGER_PATH,
    PROVENANCE_PATH,
)
from backend.ingestion.index_judgments import STATS_PATH, build_chunks  # noqa: E402

# The corpus before this expansion: everything at or below this index is
# original, everything after was added by the expansion.
ORIGINAL_COUNT = 260


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def collect(with_passages: bool = True) -> dict:
    records = _load(JUDGMENTS_PATH) or []
    ledger = _load(LEDGER_PATH) or {"entries": {}, "runs": []}
    provenance = _load(PROVENANCE_PATH) or {}
    index_stats = _load(STATS_PATH) or {}

    original = records[:ORIGINAL_COUNT]
    added = records[ORIGINAL_COUNT:]
    chars = [r["char_count"] for r in records]

    topics: Counter = Counter()
    for record in records:
        topics.update(record.get("topics") or topics_in(record["text"]))

    entries = ledger["entries"].values()
    decisions = Counter(e["decision"] for e in entries)
    reasons = Counter(e["reason"] for e in entries)

    report = {
        "corpus": {
            "judgments": len(records),
            "original": len(original),
            "added_by_expansion": len(added),
            "total_chars": sum(chars),
            "chars_mean": round(statistics.mean(chars)) if chars else 0,
            "chars_median": round(statistics.median(chars)) if chars else 0,
            "years_covered": len({r["year"] for r in records}),
            "year_range": [min(r["year"] for r in records), max(r["year"] for r in records)] if records else [],
        },
        "integrity": {
            "unique_source_urls": len({r["source_url"] for r in records}),
            "unique_neutral_citations": len({r["neutral_citation"] for r in records if r["neutral_citation"]}),
            "unique_citations": len({r["citation"] for r in records if r["citation"]}),
            "unique_text_hashes": len({r["sha256"] for r in records}),
            "records_with_retained_pdf": sum(1 for r in records if r.get("pdf_sha256")),
            "records_missing_any_core_field": sum(
                1 for r in records
                if not all(str(r.get(f) or "").strip() for f in
                           ("case_name", "citation", "neutral_citation", "judgment_date",
                            "judge", "source_url"))
            ),
        },
        "by_year": dict(sorted(Counter(r["year"] for r in records).items())),
        "by_law": dict(Counter(r["law"] for r in records).most_common()),
        "by_stratum": dict(Counter(r.get("stratum", "original_260") for r in records).most_common()),
        "statutes": dict(Counter(s for r in records for s in r["statutes_referred"]).most_common()),
        "topics": dict(topics.most_common()),
        "topic_floors": {
            topic: {"floor": floor, "have": topics.get(topic, 0),
                    "met": topics.get(topic, 0) >= floor}
            for topic, floor in sorted(TOPIC_FLOORS.items(), key=lambda kv: -kv[1])
        },
        "harvest": {
            "candidates_examined": len(ledger["entries"]),
            "retained": decisions.get("retained", 0),
            "rejected": decisions.get("rejected", 0),
            "acceptance_rate": round(decisions.get("retained", 0) / len(ledger["entries"]), 4)
            if ledger["entries"] else None,
            "reasons": dict(reasons.most_common()),
            "runs": len(ledger["runs"]),
            "counters_cumulative": provenance.get("harvest_counters_cumulative", {}),
        },
        "index": {
            "collection": index_stats.get("collection"),
            "judgments_indexed": index_stats.get("judgments"),
            "passages_indexed": index_stats.get("embeddings"),
            "complete": index_stats.get("complete"),
            "built_at": index_stats.get("built_at"),
        },
    }

    if with_passages:
        chunks = build_chunks(records)
        per_judgment = Counter(c["case_name"] for c in chunks)
        report["passages"] = {
            "total": len(chunks),
            "per_judgment_mean": round(statistics.mean(per_judgment.values()), 1),
            "per_judgment_median": round(statistics.median(per_judgment.values())),
            "original_corpus_passages": len(build_chunks(original)),
            "added_passages": len(chunks) - len(build_chunks(original)),
        }
    return report


def _bar(value: int, largest: int, width: int = 28) -> str:
    return "█" * max(1, round(width * value / largest)) if value and largest else ""


def render(report: dict) -> None:
    corpus, integrity = report["corpus"], report["integrity"]
    print("═" * 74)
    print("  JUDGMENT CORPUS — Supreme Court of India, criminal law")
    print("═" * 74)
    print(f"  judgments        {corpus['judgments']:>7,}   "
          f"({corpus['original']} original + {corpus['added_by_expansion']} added)")
    if passages := report.get("passages"):
        print(f"  passages         {passages['total']:>7,}   "
              f"({passages['original_corpus_passages']:,} original + {passages['added_passages']:,} added)")
        print(f"  per judgment     {passages['per_judgment_mean']:>7}   "
              f"mean, {passages['per_judgment_median']} median")
    print(f"  characters       {corpus['total_chars']:>7,}   "
          f"mean {corpus['chars_mean']:,}, median {corpus['chars_median']:,}")
    print(f"  years            {corpus['years_covered']:>7}   "
          f"{corpus['year_range'][0]}–{corpus['year_range'][1]}")

    print("\n  INTEGRITY")
    total = corpus["judgments"]
    for label, key in (("unique source URLs", "unique_source_urls"),
                       ("unique neutral citations", "unique_neutral_citations"),
                       ("unique official citations", "unique_citations"),
                       ("unique text hashes", "unique_text_hashes")):
        value = integrity[key]
        print(f"    {label:<28} {value:>6,} / {total:<6} {'ok' if value == total else 'MISMATCH'}")
    print(f"    {'records missing a core field':<28} {integrity['records_missing_any_core_field']:>6}")
    print(f"    {'retained PDFs on disk':<28} {integrity['records_with_retained_pdf']:>6,}")

    harvest = report["harvest"]
    print("\n  HARVEST")
    print(f"    candidates examined          {harvest['candidates_examined']:>6,}")
    print(f"    retained                     {harvest['retained']:>6,}")
    print(f"    rejected                     {harvest['rejected']:>6,}")
    if harvest["acceptance_rate"] is not None:
        print(f"    acceptance rate              {harvest['acceptance_rate'] * 100:>5.1f}%")
    print("    rejection reasons:")
    for reason, count in harvest["reasons"].items():
        print(f"      {reason:<32} {count:>6,}")

    print("\n  BY STRATUM")
    for stratum, count in report["by_stratum"].items():
        print(f"    {stratum:<24} {count:>5}")

    print("\n  TOPIC COVERAGE vs FLOOR")
    largest = max(report["topics"].values()) if report["topics"] else 1
    for topic, info in report["topic_floors"].items():
        mark = "ok " if info["met"] else "-- "
        print(f"    {mark}{topic:<22} {info['have']:>4} / {info['floor']:<4} "
              f"{_bar(info['have'], largest)}")
    uncapped = {t: c for t, c in report["topics"].items() if t not in report["topic_floors"]}
    for topic, count in uncapped.items():
        print(f"       {topic:<22} {count:>4}        {_bar(count, largest)}")

    index = report["index"]
    print("\n  INDEX")
    print(f"    collection      {index['collection']}")
    print(f"    passages        {index['passages_indexed']:,}" if index["passages_indexed"] else "    passages        —")
    print(f"    complete        {index['complete']}")
    print(f"    built           {index['built_at']}")
    print("═" * 74)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--no-passages", action="store_true",
                        help="skip chunking (faster, omits passage counts)")
    args = parser.parse_args()

    report = collect(with_passages=not args.no_passages)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
