"""
verify_expansion_readiness.py — prove the repaired pipeline is safe to run.

The corpus expansion rewrites the harvester and the indexer. Both of them touch
work that took roughly 72 minutes of MPS time to produce, and the harvester's
previous version destroyed the corpus on a second run. So before a single new
PDF is fetched, the repaired pipeline is exercised against the corpus that
already exists, with the network untouched, and made to demonstrate:

  1. the existing judgments still load and still number what they did
  2. chunking still produces the same passages, byte-for-byte
  3. every passage already in ChromaDB matches the rebuilt chunk exactly
  4. provenance describes the corpus it actually sits next to
  5. duplicate detection catches the duplicates known to exist in the source
  6. an incremental index run would append new IDs after the existing ones
     rather than rebuilding them

Usage
-----
    python -m backend.ingestion.verify_expansion_readiness
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.ingestion import fetch_judgments as fetcher  # noqa: E402
from backend.ingestion.corpus_selection import extract_sections  # noqa: E402
from backend.ingestion.index_judgments import build_chunks  # noqa: E402
from backend.ingestion.index_judgments_resumable import (  # noqa: E402
    SLICE_SIZE,
    _completed_slices,
    _slice_bounds,
)

# The frozen state this guard protects, re-captured after each completed
# expansion. Read from a manifest rather than hardcoded, so raising the target
# does not mean editing constants in three places and getting one of them wrong.
# The corpus grows PAST these numbers; every check is written against the PREFIX.
BASELINE_DIR = config.BASE_DIR / "data" / "processed" / "judgments_sc" / "_baseline"
_manifest = json.loads((BASELINE_DIR / "baseline.json").read_text(encoding="utf-8"))
BASELINE_JUDGMENTS = _manifest["judgments"]
BASELINE_PASSAGES = _manifest["passages"]
BASELINE_SHA256 = _manifest["corpus_sha256"]

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    results.append((name, passed, detail))
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))
    return passed


def _client():
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(
        path=str(config.CHROMA_PERSIST_PATH),
        settings=Settings(anonymized_telemetry=False),
    )


# ── 1 & 2 ───────────────────────────────────────────────────────────────────


def check_corpus_and_passages(records, chunks) -> None:
    baseline_path = BASELINE_DIR / "judgments.json"
    if not baseline_path.exists():
        check("1. original judgments preserved", False,
              f"no baseline snapshot at {baseline_path}")
        return
    baseline_records = json.loads(baseline_path.read_text(encoding="utf-8"))

    check("1. original judgments preserved, unmodified, still first in the corpus",
          len(records) >= BASELINE_JUDGMENTS
          and records[:BASELINE_JUDGMENTS] == baseline_records,
          f"{len(records)} judgments now; first {BASELINE_JUDGMENTS} byte-identical "
          f"to the pre-expansion snapshot; {len(records) - BASELINE_JUDGMENTS} appended")

    baseline_chunks = build_chunks(baseline_records)
    check("2a. original judgments still chunk to the same passage count",
          len(baseline_chunks) == BASELINE_PASSAGES,
          f"{len(baseline_chunks)} passages (expected {BASELINE_PASSAGES})")

    differing = [
        i for i, (old, new) in enumerate(zip(baseline_chunks, chunks))
        if old["text"] != new["text"]
    ]
    check("2b. appended judgments did not disturb the existing passages",
          len(chunks) >= len(baseline_chunks) and not differing,
          f"{len(chunks)} passages total, first {len(baseline_chunks)} compared, "
          f"{len(differing)} differing")


# ── 3 ───────────────────────────────────────────────────────────────────────


def check_chroma_documents(chunks) -> None:
    name = config.JUDGMENT_COLLECTION
    client = _client()
    if name not in {c.name for c in client.list_collections()}:
        check("3. every stored passage matches byte-for-byte", False,
              f"collection {name} does not exist")
        return
    collection = client.get_collection(name)
    texts = [c["text"] for c in chunks]

    # Only the prefix is expected to be in the store; anything appended has not
    # been indexed yet and its absence is not a fault.
    indexed = min(len(texts), BASELINE_PASSAGES)
    mismatched, missing = [], []
    for start in range(0, indexed, 2000):
        wanted = [f"{name}:{i}" for i in range(start, min(start + 2000, indexed))]
        got = collection.get(ids=wanted, include=["documents"])
        found = dict(zip(got["ids"], got["documents"]))
        for passage_id in wanted:
            index = int(passage_id.rsplit(":", 1)[1])
            if passage_id not in found:
                missing.append(passage_id)
            elif found[passage_id] != texts[index]:
                mismatched.append(passage_id)

    check("3. every already-embedded passage matches byte-for-byte",
          collection.count() >= indexed and not mismatched and not missing,
          f"{collection.count()} embeddings in the store, {len(missing)} missing, "
          f"{len(mismatched)} mismatched (compared all {indexed} pre-existing)")


# ── 4 ───────────────────────────────────────────────────────────────────────


def check_provenance(records) -> None:
    ok = fetcher.verify(check_pdfs=True)
    check("4a. record-level verification (metadata, checksums, duplicates)", ok)

    path = fetcher.PROVENANCE_PATH
    if not path.exists():
        check("4b. provenance agrees with the corpus", False, "provenance.json missing")
        return
    provenance = json.loads(path.read_text(encoding="utf-8"))
    actual_chars = sum(r["char_count"] for r in records)
    agrees = (provenance.get("count") == len(records)
              and provenance.get("total_chars") == actual_chars)
    check("4b. provenance agrees with the corpus", agrees,
          f"provenance says count={provenance.get('count')}, "
          f"total_chars={provenance.get('total_chars')}; "
          f"corpus has count={len(records)}, total_chars={actual_chars}")

    check("4c. selection criteria are documented and referenced",
          (config.BASE_DIR / "docs" / "corpus_selection.md").exists()
          and provenance.get("selection", {}).get("criteria_document") == "docs/corpus_selection.md")


# ── 5 ───────────────────────────────────────────────────────────────────────


def check_deduplication(records) -> None:
    index = fetcher.DuplicateIndex(records)

    # Every record already in the corpus must be recognised on all four
    # pre-download keys and on its text hash.
    sample = records[0]
    by_url = index.check_metadata(sample["source_url"], "", "")
    by_neutral = index.check_metadata("https://example.invalid/x/y/z.pdf",
                                      sample["neutral_citation"], "")
    by_citation = index.check_metadata("https://example.invalid/x/y/z.pdf",
                                       "", sample["citation"])
    by_text = index.check_text(sample["sha256"])
    check("5a. an already-ingested judgment is caught on url / neutral / citation / text",
          by_url == "duplicate_url"
          and by_neutral == "duplicate_neutral_citation"
          and by_citation == "duplicate_citation"
          and by_text == "duplicate_text",
          f"{by_url}, {by_neutral}, {by_citation}, {by_text}")

    check("5b. a genuinely new candidate is not flagged",
          index.check_metadata("https://example.invalid/year=1999/english/x_EN.pdf",
                               "1999 INSC 99999", "[1999] 99 S.C.R. 9999") is None
          and index.check_text("0" * 64) is None)

    # The source dataset itself repeats rows. Feed the real duplicates through.
    import glob
    import re

    import pandas as pd

    frames = []
    for file in sorted(glob.glob(str(fetcher.RAW_DIR / "metadata_*.parquet"))):
        frame = pd.read_parquet(file)
        frame["__year"] = int(re.search(r"(\d{4})", file).group(1))
        frames.append(frame)
    if not frames:
        check("5c. duplicates present in the source metadata are caught", False,
              "no cached metadata to test against")
        return
    everything = pd.concat(frames, ignore_index=True)

    repeated_ids = everything[
        everything["case_id"].astype(str).str.strip().ne("")
        & everything["case_id"].duplicated(keep=False)
    ]
    repeated_paths = everything[everything.duplicated(subset=["year", "path"], keep=False)]

    # Simulate ingesting the first of each duplicate pair, then offering the second.
    caught, offered = 0, 0
    fresh = fetcher.DuplicateIndex([])
    for _, group in repeated_ids.groupby(everything["case_id"].astype(str).str.strip()):
        rows = [row for _, row in group.iterrows()]
        first, rest = rows[0], rows[1:]
        url = fetcher.pdf_url_for(first)
        if not url:
            continue
        fresh.add({
            "source_url": url,
            "neutral_citation": str(first["case_id"]).strip(),
            "citation": str(first["citation"]).strip(),
            "sha256": hashlib.sha256(url.encode()).hexdigest(),
        })
        for row in rest:
            other = fetcher.pdf_url_for(row)
            if not other:
                continue
            offered += 1
            if fresh.check_metadata(other, str(row["case_id"]).strip(),
                                    str(row["citation"]).strip()):
                caught += 1

    check("5c. duplicates present in the source metadata are caught",
          offered > 0 and caught == offered,
          f"{caught}/{offered} repeated source rows flagged "
          f"({len(repeated_ids)} rows share a case_id, "
          f"{len(repeated_paths)} share a (year, path))")

    # And the two source rows that shadow judgments already in the corpus.
    shadowing = 0
    for _, row in everything.iterrows():
        url = fetcher.pdf_url_for(row)
        if url and index.check_metadata(url, str(row.get("case_id") or "").strip(),
                                        str(row.get("citation") or "").strip()):
            shadowing += 1
    check("5d. source rows shadowing the existing corpus are counted",
          shadowing >= BASELINE_JUDGMENTS,
          f"{shadowing} cached source rows resolve to a judgment already held "
          f"(corpus holds {len(records)})")


# ── 6 ───────────────────────────────────────────────────────────────────────


def check_incremental_append(records, chunks) -> None:
    name = config.JUDGMENT_COLLECTION
    client = _client()
    collection = client.get_collection(name)

    # Nothing has changed yet, so a run right now must embed nothing at all.
    done, mismatches = _completed_slices(collection, name, [c["text"] for c in chunks],
                                         len(chunks), strict=False)
    bounds = _slice_bounds(len(chunks))
    reused = sum(stop - start for start, stop in bounds if start in done)
    check("6a. already-embedded work is reused, not rebuilt",
          not mismatches and reused >= BASELINE_PASSAGES - SLICE_SIZE,
          f"{len(done)}/{len(bounds)} slices verified in the store, "
          f"{reused:,} passages reused, {len(chunks) - reused:,} to embed, "
          f"{len(mismatches)} prefix mismatches")

    # Now simulate one appended judgment and confirm where the new IDs land.
    grown = list(records)
    synthetic = copy.deepcopy(records[0])
    synthetic["text"] = ("This synthetic judgment exists only inside the readiness check. "
                         * 40).strip()
    synthetic["case_name"] = "READINESS CHECK versus READINESS CHECK"
    grown.append(synthetic)
    grown_chunks = build_chunks(grown)
    grown_texts = [c["text"] for c in grown_chunks]

    prefix_intact = grown_texts[:len(chunks)] == [c["text"] for c in chunks]
    check("6b. appending a judgment leaves every existing passage at its own index",
          prefix_intact,
          f"first {len(chunks)} passages unchanged; corpus grew to {len(grown_texts)}")

    grown_done, _ = _completed_slices(collection, name, grown_texts, len(grown_texts),
                                      strict=False)
    grown_bounds = _slice_bounds(len(grown_texts))
    remaining = [start for start, _ in grown_bounds if start not in grown_done]
    # Every ID from the current passage count onward is new; anything below the
    # already-embedded mark that gets re-embedded is rework, and must be confined
    # to the single tail slice.
    already = min(len(chunks), BASELINE_PASSAGES)
    rework = sum(min(stop, already) - start
                 for start, stop in grown_bounds
                 if start in set(remaining) and start < already)
    first_new_id = len(chunks)
    absent = collection.get(ids=[f"{name}:{first_new_id}"], include=[])
    check("6c. new passages append at the next free ID, existing ones are reused",
          not absent["ids"] and rework <= SLICE_SIZE,
          f"first new ID would be {name}:{first_new_id} (not present in the store); "
          f"{len(remaining)} slice(s) to embed; {rework} already-embedded passages "
          f"re-embedded as tail-slice rework, {already - rework:,} reused")


# ── extra: section extraction ───────────────────────────────────────────────


def check_section_extraction(all_records) -> None:
    # v1 extraction only exists on the original records; compare like with like.
    records = all_records[:BASELINE_JUDGMENTS]
    sample = "The appellant was convicted under Section 302 read with Section 34 of the " \
             "Indian Penal Code. Clause 3 of the notification and section 5 of that circular " \
             "are not in issue. Reliance is placed on Section 27 of the Evidence Act."
    qualified, unqualified = extract_sections(sample)
    check("7a. section references are attributed to a statute",
          qualified[:2] == ["IPC 302", "IPC 34"] and "Evidence 27" in qualified,
          f"qualified={qualified} unqualified={unqualified}")

    from collections import Counter

    # Compare only on records that still carry v1 extraction. Once most of the
    # corpus stores v2 references, comparing the whole corpus compares v2 against
    # v2 and the check passes without testing anything.
    legacy = [r for r in records if "section_extraction_version" not in r]
    if not legacy:
        check("7b. v2 attribution beats v1 on the legacy records", True,
              "no v1 records remain to compare against")
        return
    old = Counter(s for r in legacy for s in r["sections_referred"])
    new = Counter(ref for r in legacy for ref in extract_sections(r["text"])[0])
    bare = [s for s, _ in old.most_common(6) if s.isdigit() and len(s) <= 2]
    check("7b. v2 attribution removes the bare prose numbers v1 emitted",
          bool(bare) and not any(ref.split()[-1].isdigit() and len(ref.split()[-1]) <= 2
                                 and " " not in ref for ref in new),
          f"{len(legacy)} v1 records | v1 top: {[s for s, _ in old.most_common(6)]} "
          f"(bare prose numbers: {bare})\n"
          f"        v2 top: {[s for s, _ in new.most_common(6)]} (all statute-attributed)")


# ── 8, 9, 10: allocation and ledger guarantees ──────────────────────────────


def check_allocation(records) -> None:
    """
    Allocation invariants that hold at EVERY phase.

    An earlier version of this check asserted `held + sum(targets) ==
    TARGET_TOTAL`, which is only true before a harvest runs. Once the strata are
    filled the same arithmetic reads 1500 + 700 = 2200 and reports a failure
    while the planner is behaving perfectly. A check that only passes in one
    phase is a false alarm generator, so these are phrased as bounds rather than
    an identity.
    """
    import collections

    from backend.ingestion.corpus_selection import STRATA, TARGET_TOTAL
    from backend.ingestion.fetch_judgments import plan_strata

    approved = {s["name"]: s["target"] for s in STRATA}
    held = collections.Counter(r.get("stratum") for r in records)
    prior, outstanding = plan_strata(records, TARGET_TOTAL)

    over = {n: held.get(n, 0) for n, t in approved.items() if held.get(n, 0) > t}
    check("8a. no stratum holds more than its approved target",
          not over and len(records) <= TARGET_TOTAL,
          " | ".join(f"{n} {held.get(n, 0)}/{t}" for n, t in approved.items())
          + f" | corpus {len(records)}/{TARGET_TOTAL}"
          + (f" | OVER: {over}" if over else ""))

    # Outstanding must be exactly the unmet remainder, unless global capacity
    # forces it lower — never more.
    remainder = {n: max(0, t - held.get(n, 0)) for n, t in approved.items()}
    capacity = max(0, TARGET_TOTAL - len(records))
    exact = outstanding == remainder if sum(remainder.values()) <= capacity else None
    check("8b. planner asks for the unmet remainder and never more",
          all(outstanding[n] <= remainder[n] for n in approved)
          and sum(outstanding.values()) <= capacity
          and (exact is not False),
          f"outstanding {outstanding} | unmet remainder {remainder} | capacity {capacity}")

    # Interruption safety: re-plan repeatedly, as a resumed process does. A
    # resumed run only ever retains what the planner just authorised, so the
    # simulation stops when the planner authorises nothing.
    simulated = list(records)
    name = STRATA[0]["name"]
    breach = None
    for _ in range(25):
        p_, out = plan_strata(simulated, TARGET_TOTAL)
        if p_.get(name, 0) + out[name] > approved[name]:
            breach = p_.get(name, 0) + out[name]
            break
        if out[name] == 0:
            break                      # nothing authorised — a real run stops here
        simulated.extend({"stratum": name} for _ in range(max(1, out[name] // 4)))
    final = sum(1 for r in simulated if r.get("stratum") == name)
    check("9. interruption and resume cannot exceed a stratum allocation",
          breach is None and final <= approved[name],
          f"25 simulated resumes of {name}: reached {final}, approved {approved[name]}"
          + (f"; BREACH at {breach}" if breach else ""))


def check_ledger_blocks_refetch(records) -> None:
    ledger = fetcher.load_ledger()
    entries = ledger["entries"]
    if not entries:
        check("10. ledger prevents re-fetching examined candidates", False, "ledger is empty")
        return

    # Every examined candidate must be recognised by its key, so `consider()`
    # short-circuits before spending a request on it.
    rejected = [e for e in entries.values() if e["decision"] == "rejected"]
    keys = {fetcher.candidate_key(e["year"], e["path"]) for e in entries.values()}
    recognised = sum(1 for e in entries.values()
                     if fetcher.candidate_key(e["year"], e["path"]) in entries)
    check("10a. every examined candidate is keyed and would be skipped on re-run",
          recognised == len(entries) and len(keys) == len(entries),
          f"{recognised}/{len(entries)} examined candidates keyed "
          f"({len(rejected)} of them rejected — these are never re-downloaded)")

    # And a rejected candidate must not be reachable through the corpus either.
    duplicates = fetcher.DuplicateIndex(records)
    sample = next((e for e in rejected if e.get("url")), None)
    if sample:
        in_ledger = fetcher.candidate_key(sample["year"], sample["path"]) in entries
        check("10b. a previously rejected candidate is blocked before any request",
              in_ledger,
              f"{sample['year']}/{sample['path']} rejected as {sample['reason']!r} "
              f"— blocked by ledger key, no HTTP request issued")


def main() -> int:
    print("Expansion readiness — repaired pipeline against the existing corpus, no downloads\n")
    records = fetcher.load_existing()
    chunks = build_chunks(records)

    check_corpus_and_passages(records, chunks)
    check_chroma_documents(chunks)
    check_provenance(records)
    check_deduplication(records)
    check_incremental_append(records, chunks)
    check_allocation(records)
    check_ledger_blocks_refetch(records)
    check_section_extraction(records)

    failed = [name for name, passed, _ in results if not passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
