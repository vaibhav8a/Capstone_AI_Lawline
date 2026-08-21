"""
build_test_queries.py — construct the retrieval evaluation set.

Honesty note (read before citing any number produced from this set)
------------------------------------------------------------------
This is a *hand-written* query set with gold labels that are **verified against
the parsed corpus at build time**: the script fails if a gold section does not
exist in the corpus, and it prints the section title next to each query so the
label can be eyeballed. It is NOT expert-annotated, and it is not a community
benchmark. It should be described in any write-up as an author-constructed
evaluation set for an author-built corpus, with the obvious caveat that the same
person wrote the queries and the labels.

What it is good for: comparing retrieval configurations against each other on a
fixed, reproducible task. What it is not good for: claiming an absolute accuracy
number for Indian legal QA in general.

Query categories (mirroring the categories requested for the project)
---------------------------------------------------------------------
    direct_section      names the section number outright
    natural_language    describes an offence in lay terms
    paraphrase          restates a provision without its statutory vocabulary
    ambiguous           under-specified; several sections are defensible
    multi_section       genuinely requires more than one section
    out_of_corpus       answerable by no section in the corpus (abstention test)
    ipc_bns_mapping     asks about the IPC→BNS transition

`out_of_corpus` queries carry an empty gold set on purpose. They are excluded
from Precision/Recall/MRR/nDCG (those are undefined with no relevant document)
and are scored separately as an abstention test.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from backend.ingestion.parse_statutes import PARSED_STATUTE_DIR  # noqa: E402

OUT_CSV = config.BASE_DIR / "evaluation" / "test_queries.csv"
OUT_JSON = config.BASE_DIR / "evaluation" / "test_queries.json"

# (query_id, category, query, [(document, section), …])
# Gold labels are the sections a competent reader would expect to be retrieved.
QUERIES: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    # ── direct section lookup ────────────────────────────────────────────────
    ("d01", "direct_section", "What does IPC Section 420 say?", [("IPC", "420")]),
    ("d02", "direct_section", "Explain Section 302 of the Indian Penal Code", [("IPC", "302")]),
    ("d03", "direct_section", "IPC section 378 definition", [("IPC", "378")]),
    ("d04", "direct_section", "What is Section 124A IPC?", [("IPC", "124A")]),
    ("d05", "direct_section", "Section 498A of IPC", [("IPC", "498A")]),
    ("d06", "direct_section", "What is section 511 IPC about?", [("IPC", "511")]),
    ("d07", "direct_section", "IPC 304A explain", [("IPC", "304A")]),
    ("d08", "direct_section", "What does Section 34 IPC state?", [("IPC", "34")]),
    ("d09", "direct_section", "Section 120B Indian Penal Code", [("IPC", "120B")]),
    ("d10", "direct_section", "What is BNS section 103?", [("BNS", "103")]),

    # ── natural language offence descriptions ───────────────────────────────
    ("n01", "natural_language",
     "Someone tricked me into transferring my property to them by lying about who they were",
     [("IPC", "420")]),
    ("n02", "natural_language",
     "What is the punishment for intentionally killing another person?",
     [("IPC", "302")]),
    ("n03", "natural_language",
     "My neighbour took my bicycle from my garden without asking me",
     [("IPC", "378"), ("IPC", "379")]),
    ("n04", "natural_language",
     "A husband and his family are harassing a woman over dowry demands",
     [("IPC", "498A")]),
    ("n05", "natural_language",
     "A driver was speeding recklessly and killed a pedestrian without meaning to",
     [("IPC", "304A")]),
    ("n06", "natural_language",
     "Two people planned a crime together before committing it",
     [("IPC", "120A"), ("IPC", "120B")]),
    ("n07", "natural_language",
     "Someone threatened to publish private photos unless I paid them money",
     [("IPC", "383"), ("IPC", "384")]),
    # NOTE: bribery by a public servant (old ss.161-165A) was repealed from the IPC
    # by the Prevention of Corruption Act, 1988, so it has no gold label available
    # in this corpus. Replaced with a public-servant offence that IS in the corpus.
    ("n08", "natural_language",
     "A public servant deliberately disobeyed the law intending to harm someone",
     [("IPC", "166")]),
    ("n09", "natural_language",
     "Someone forged a document to cheat a bank",
     [("IPC", "463"), ("IPC", "465")]),
    ("n10", "natural_language",
     "A group of five people gathered with the common aim of causing a riot",
     [("IPC", "141"), ("IPC", "146")]),

    # ── paraphrase ──────────────────────────────────────────────────────────
    ("p01", "paraphrase",
     "Which provision covers dishonestly inducing a person to hand over property?",
     [("IPC", "420")]),
    ("p02", "paraphrase",
     "What provision deals with causing death by a rash or negligent act?",
     [("IPC", "304A")]),
    ("p03", "paraphrase",
     "Which section covers acts done by several persons in furtherance of a common intention?",
     [("IPC", "34")]),
    ("p04", "paraphrase",
     "What does the Code say about attempting to commit an offence but failing?",
     [("IPC", "511")]),
    ("p05", "paraphrase",
     "Which provision defines dishonest misappropriation of movable property?",
     [("IPC", "403")]),
    ("p06", "paraphrase",
     "What is the provision on wrongfully confining a person?",
     [("IPC", "340"), ("IPC", "342")]),

    # ── ambiguous / under-specified ─────────────────────────────────────────
    ("a01", "ambiguous", "What is the punishment for hurt?", [("IPC", "319"), ("IPC", "323"), ("IPC", "325")]),
    ("a02", "ambiguous", "cheating", [("IPC", "415"), ("IPC", "417"), ("IPC", "420")]),
    ("a03", "ambiguous", "theft related offences", [("IPC", "378"), ("IPC", "379"), ("IPC", "380")]),
    ("a04", "ambiguous", "criminal breach of trust", [("IPC", "405"), ("IPC", "406")]),

    # ── multi-section ───────────────────────────────────────────────────────
    ("m01", "multi_section",
     "What is the difference between culpable homicide and murder?",
     [("IPC", "299"), ("IPC", "300")]),
    ("m02", "multi_section",
     "How does the Code distinguish theft, extortion and robbery?",
     [("IPC", "378"), ("IPC", "383"), ("IPC", "390")]),
    ("m03", "multi_section",
     "What are the punishments for culpable homicide not amounting to murder and for murder?",
     [("IPC", "302"), ("IPC", "304")]),
    ("m04", "multi_section",
     "Explain criminal conspiracy and abetment",
     [("IPC", "107"), ("IPC", "120A"), ("IPC", "120B")]),

    # ── out of corpus (abstention test — no gold sections) ──────────────────
    ("o01", "out_of_corpus", "What are the filing requirements for a GST return?", []),
    ("o02", "out_of_corpus", "How do I apply for an Indian passport?", []),
    ("o03", "out_of_corpus", "What is the procedure for registering a trademark in India?", []),
    ("o04", "out_of_corpus", "What is the capital gains tax rate on equity shares?", []),
    ("o05", "out_of_corpus", "Write me a Python function to reverse a linked list", []),

    # ── IPC / BNS transition ────────────────────────────────────────────────
    ("b01", "ipc_bns_mapping", "What is the BNS provision for murder?", [("BNS", "103")]),
    ("b02", "ipc_bns_mapping", "Which BNS section deals with cheating and dishonestly inducing delivery of property?", [("BNS", "318")]),
    ("b03", "ipc_bns_mapping", "What does the Bharatiya Nyaya Sanhita say about theft?", [("BNS", "303")]),
    ("b04", "ipc_bns_mapping", "BNS provision on criminal conspiracy", [("BNS", "61")]),
]


def load_corpus_index() -> dict[tuple[str, str], dict]:
    index: dict[tuple[str, str], dict] = {}
    for doc_id in ("IPC", "BNS"):
        path = PARSED_STATUTE_DIR / f"{doc_id}_sections.json"
        for record in json.loads(path.read_text(encoding="utf-8")):
            index[(record["document"], record["section"])] = record
    return index


def main() -> int:
    corpus = load_corpus_index()

    rows = []
    missing: list[str] = []
    for query_id, category, query, gold in QUERIES:
        resolved = []
        for document, section in gold:
            record = corpus.get((document, section))
            if record is None:
                missing.append(f"{query_id}: {document} s.{section} not in corpus")
                continue
            resolved.append(
                {"document": document, "section": section, "title": record["title"]}
            )
        rows.append(
            {
                "query_id": query_id,
                "category": category,
                "query": query,
                "gold": resolved,
                "gold_count": len(resolved),
            }
        )

    if missing:
        print("GOLD LABELS NOT PRESENT IN CORPUS — fix these before trusting any metric:")
        for item in missing:
            print(f"  ! {item}")
        print(
            "\nA gold label that is absent from the corpus makes recall unreachable "
            "and silently depresses every metric. Refusing to write the test set."
        )
        return 1

    OUT_JSON.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query_id", "category", "query", "gold_sections", "gold_titles"])
        for row in rows:
            writer.writerow(
                [
                    row["query_id"],
                    row["category"],
                    row["query"],
                    "; ".join(f"{g['document']} s.{g['section']}" for g in row["gold"]),
                    "; ".join(g["title"] for g in row["gold"]),
                ]
            )

    from collections import Counter

    counts = Counter(r["category"] for r in rows)
    scored = [r for r in rows if r["category"] != "out_of_corpus"]
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_CSV}\n")
    print(f"{len(rows)} queries total — {len(scored)} scored for ranking metrics, "
          f"{counts['out_of_corpus']} abstention-only\n")
    for category, count in sorted(counts.items()):
        print(f"  {category:18} {count:3}")
    print(f"\n  gold labels total: {sum(r['gold_count'] for r in rows)}")
    print("\nAll gold sections verified present in the parsed corpus.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
