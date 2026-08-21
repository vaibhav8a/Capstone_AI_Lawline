"""
fetch_judgments.py — build and extend a criminal-law Supreme Court corpus.

Source
------
`indian-supreme-court-judgments` on the AWS Registry of Open Data
(https://registry.opendata.aws/indian-supreme-court-judgments/):

  * Original source : the official eCourts portal, judgments.ecourts.gov.in
  * Licence         : CC-BY-4.0
  * Maintainer      : Dattam Labs
  * Coverage        : Supreme Court of India, 1950-present
  * Layout          : year-partitioned Parquet metadata + year-partitioned PDFs

This is preferred over scraping sci.gov.in directly. It carries an explicit open
licence, is published for bulk access, and every record keeps the official
citation and neutral citation so any judgment can be checked against the court's
own portal. Nothing here is invented: `case_name`, `citation`, `judgment_date`,
`judge` and `court` all come from the published metadata, and section references
are extracted from the judgment text itself.

Selection is defined in `corpus_selection.py` and documented in
docs/corpus_selection.md.

Incremental by construction
---------------------------
The first version of this harvester started from an empty list every run and
overwrote judgments.json wholesale, so a second run destroyed the first one's
corpus. It is now additive:

  1. the existing corpus is LOADED and its records are never rewritten
  2. a candidate ledger records every judgment ever examined, with the reason it
     was kept or rejected, so a re-run never re-downloads a known reject
  3. duplicates are caught on five keys — source URL, neutral citation, official
     citation, (year, PDF path) and SHA-256 of the extracted text. The first
     four are checked BEFORE downloading, so a duplicate costs no bandwidth.
  4. retained PDFs are kept on disk with their own checksum, so text extraction
     can be reproduced and re-verified without re-fetching
  5. corpus, ledger and provenance are written atomically via a temp file and
     rename — an interrupted run can never leave a half-written corpus

Usage
-----
    python -m backend.ingestion.fetch_judgments --dry-run   # no network at all
    python -m backend.ingestion.fetch_judgments --target 800
    python -m backend.ingestion.fetch_judgments --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import signal
import ssl
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.ingestion.corpus_selection import (  # noqa: E402
    MIN_CRIMINAL_SCORE,
    MIN_TEXT_CHARS,
    SECTION_EXTRACTION_VERSION,
    STRATA,
    TARGET_TOTAL,
    admit,
    criminal_score,
    extract_sections,
    order_candidates,
    primary_law,
    topics_in,
    unmet_floors,
)

logger = logging.getLogger(__name__)

BUCKET = "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com"

DATASET_ATTRIBUTION = {
    "dataset": "Indian Supreme Court Judgments",
    "registry": "https://registry.opendata.aws/indian-supreme-court-judgments/",
    "bucket": "indian-supreme-court-judgments (ap-south-1)",
    "licence": "CC-BY-4.0",
    "maintainer": "Dattam Labs",
    "original_source": "eCourts, https://judgments.ecourts.gov.in/",
    "court": "Supreme Court of India",
}

RAW_DIR = config.BASE_DIR / "data" / "raw" / "judgments"
PDF_DIR = RAW_DIR / "pdf"
OUT_DIR = config.BASE_DIR / "data" / "processed" / "judgments_sc"
JUDGMENTS_PATH = OUT_DIR / "judgments.json"
PROVENANCE_PATH = OUT_DIR / "provenance.json"
LEDGER_PATH = OUT_DIR / "candidate_ledger.json"

# A judgment is only citable if it carries the fields a citation is made of.
# The source dataset leaves `case_id` (the neutral citation) blank on a minority
# of rows; those rows are skipped rather than ingested without one, because a
# record that cannot be cited back to the court's own portal is not usable
# evidence no matter how relevant its text is. Checked before downloading, so an
# unusable candidate costs no bandwidth.
REQUIRED_METADATA = {
    "case_name": "title",
    "citation": "citation",
    "neutral_citation": "case_id",
    "judgment_date": "decision_date",
    "judge": "judge",
}

USER_AGENT = "LawLine-AI/1.0"
REQUEST_DELAY = 0.35        # polite pacing between object fetches
TIMEOUT = 90
SAVE_EVERY = 10             # retained judgments between checkpoint saves

_stop_requested = False


def _handle_signal(signum, _frame):
    global _stop_requested
    _stop_requested = True
    logger.info("\n[judgments] signal %s — finishing this candidate, then saving and stopping", signum)


# ── io helpers ──────────────────────────────────────────────────────────────


def _atomic_write(path: Path, text: str) -> None:
    """Write via temp file + rename. A crash leaves the previous file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(url: str, binary: bool = True):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=TIMEOUT, context=_ssl_context()) as response:
        return response.read() if binary else response.read().decode("utf-8", "replace")


def load_existing() -> list[dict]:
    if not JUDGMENTS_PATH.exists():
        return []
    return json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))


def load_ledger() -> dict:
    if not LEDGER_PATH.exists():
        return {"entries": {}, "runs": []}
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    data.setdefault("entries", {})
    data.setdefault("runs", [])
    return data


def save_ledger(ledger: dict) -> None:
    ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(LEDGER_PATH, json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")


def candidate_key(year: int, path: str) -> str:
    return f"{year}/{path}"


# ── duplicate detection ─────────────────────────────────────────────────────


class DuplicateIndex:
    """
    Five-key duplicate detection over the corpus as it stands.

    The source dataset is not itself clean: across the twelve years already
    cached it carries ~50 repeated `case_id` values and 3 repeated PDF paths, so
    a harvester that trusts the metadata to be unique will ingest the same
    judgment twice under two rows. Text-hash catches the remaining case where the
    same judgment is published under two different paths.
    """

    def __init__(self, records: list[dict]):
        self.urls: set[str] = set()
        self.neutral: set[str] = set()
        self.citations: set[str] = set()
        self.texts: set[str] = set()
        self.paths: set[str] = set()
        for record in records:
            self.add(record)

    def add(self, record: dict) -> None:
        if url := record.get("source_url"):
            self.urls.add(url)
            self.paths.add(self._path_key(url))
        if neutral := (record.get("neutral_citation") or "").strip():
            self.neutral.add(neutral)
        if citation := (record.get("citation") or "").strip():
            self.citations.add(citation)
        if sha := record.get("sha256"):
            self.texts.add(sha)

    @staticmethod
    def _path_key(url: str) -> str:
        """`year=2022/english/2022_6_817_818_EN.pdf` — identifies the object."""
        return "/".join(url.rsplit("/", 3)[-3:])

    def check_metadata(self, url: str, neutral: str, citation: str) -> str | None:
        """Pre-download duplicate check. Returns a reason, or None if new."""
        if url in self.urls:
            return "duplicate_url"
        if self._path_key(url) in self.paths:
            return "duplicate_pdf_path"
        if neutral and neutral in self.neutral:
            return "duplicate_neutral_citation"
        if citation and citation in self.citations:
            return "duplicate_citation"
        return None

    def check_text(self, sha: str) -> str | None:
        return "duplicate_text" if sha in self.texts else None


# ── record construction ─────────────────────────────────────────────────────


def missing_metadata(row) -> list[str]:
    """Required citation fields absent from this candidate's source metadata."""
    return [field for field, column in REQUIRED_METADATA.items()
            if not str(row.get(column) or "").strip()]


def pdf_url_for(row) -> str | None:
    path = str(row.get("path") or "").strip()
    if not path:
        return None
    return f"{BUCKET}/data/pdf/year={int(row['year'])}/english/{path}_EN.pdf"


def extract_text(pdf_bytes: bytes) -> str:
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def store_pdf(year: int, path: str, pdf_bytes: bytes) -> tuple[str, str, int]:
    """Persist a retained PDF. Returns (relative_path, sha256, byte_size)."""
    target = PDF_DIR / f"year={year}" / f"{path}_EN.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".pdf.tmp")
    tmp.write_bytes(pdf_bytes)
    tmp.replace(target)
    return (
        str(target.relative_to(config.BASE_DIR)),
        hashlib.sha256(pdf_bytes).hexdigest(),
        len(pdf_bytes),
    )


def build_record(row, text: str, url: str, score: int, statutes: list[str],
                 topics: list[str], stratum: str, pdf_info: tuple[str, str, int] | None) -> dict:
    """All provenance fields come from published metadata or the text itself."""
    # The checksum must cover the text that is actually STORED, not the raw
    # pre-normalisation extraction. Hashing the raw text while storing the
    # whitespace-normalised text made every record fail its own verification —
    # a checksum that can never validate is worse than no checksum, because it
    # looks like integrity coverage while providing none.
    normalised_text = " ".join(text.split())
    qualified, unqualified = extract_sections(text)
    record = {
        "case_name": str(row.get("title") or "").strip(),
        "petitioner": str(row.get("petitioner") or "").strip(),
        "respondent": str(row.get("respondent") or "").strip(),
        "court": str(row.get("court") or "Supreme Court of India").strip(),
        "judgment_date": str(row.get("decision_date") or "").strip(),
        "citation": str(row.get("citation") or "").strip(),
        "neutral_citation": str(row.get("case_id") or "").strip(),
        "judge": str(row.get("judge") or "").strip(),
        "disposal_nature": str(row.get("disposal_nature") or "").strip(),
        "source_url": url,
        "retrieval_date": datetime.now(timezone.utc).date().isoformat(),
        "document_type": "judgment",
        "law": primary_law(statutes),
        "statutes_referred": statutes,
        "sections_referred": qualified,
        "sections_unqualified": unqualified,
        "section_extraction_version": SECTION_EXTRACTION_VERSION,
        "topics": topics,
        "stratum": stratum,
        "criminal_score": score,
        "year": int(row["year"]),
        "text": normalised_text,
        "char_count": len(normalised_text),
        "sha256": hashlib.sha256(normalised_text.encode("utf-8")).hexdigest(),
        "dataset_attribution": DATASET_ATTRIBUTION,
    }
    if pdf_info:
        record["pdf_path"], record["pdf_sha256"], record["pdf_bytes"] = pdf_info
    return record


# ── metadata ────────────────────────────────────────────────────────────────


def load_year_metadata(year: int, allow_download: bool = True):
    """Fetch and parse one year's Parquet metadata (cached on disk)."""
    import pandas as pd

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    local = RAW_DIR / f"metadata_{year}.parquet"
    if not local.exists():
        if not allow_download:
            return None
        url = f"{BUCKET}/metadata/parquet/year={year}/metadata.parquet"
        try:
            local.write_bytes(_get(url))
        except HTTPError as exc:
            logger.warning("[judgments] no metadata for %s (HTTP %s)", year, exc.code)
            return None
        except URLError as exc:
            logger.warning("[judgments] metadata fetch failed for %s: %s", year, exc.reason)
            return None
    return pd.read_parquet(local)


# ── harvest ─────────────────────────────────────────────────────────────────


def plan_strata(records: list[dict], target_total: int) -> tuple[Counter, dict]:
    """
    How many judgments each stratum still needs, given what the corpus already
    holds and how much room is left under the global target.

    Stratum progress lives in the records themselves, not in a per-run counter.
    The first version counted only what the CURRENT process had retained, so a
    harvest resumed after an interruption started every stratum back at zero and
    overshot its allocation — `bns_era` reached 202 against a target of 120
    before this was caught.

    If the outstanding targets no longer fit under the global cap, they are
    scaled down in proportion rather than served first-come-first-served, so an
    overshoot in one stratum is absorbed evenly instead of starving whichever
    stratum happens to run last.
    """
    prior = Counter(record.get("stratum", "original_260") for record in records)
    outstanding = {s["name"]: max(0, s["target"] - prior.get(s["name"], 0)) for s in STRATA}
    capacity = max(0, target_total - len(records))
    wanted = sum(outstanding.values())
    if wanted > capacity:
        scale = capacity / wanted if wanted else 0.0
        outstanding = {name: int(count * scale) for name, count in outstanding.items()}
        logger.info("[judgments] outstanding stratum targets (%d) exceed remaining "
                    "capacity (%d) — scaled by %.3f to %s",
                    wanted, capacity, scale, outstanding)
    return prior, outstanding


def _topic_counts(records: list[dict]) -> Counter:
    """Coverage of the corpus as it stands. v1 records carry no `topics` field,
    so their topics are recomputed from text rather than assumed absent."""
    counts: Counter = Counter()
    for record in records:
        topics = record.get("topics")
        if topics is None:
            topics = topics_in(record["text"])
        counts.update(topics)
    return counts


def harvest(target_total: int, dry_run: bool = False, max_new: int | None = None) -> dict:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    records = load_existing()
    baseline = len(records)
    ledger = load_ledger()
    duplicates = DuplicateIndex(records)
    counts = _topic_counts(records)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    counters = Counter({
        "candidates_examined": 0,
        "skipped_known_ledger": 0,
        "skipped_duplicate_pre_download": 0,
        "pdfs_downloaded": 0,
        "download_failed": 0,
        "rejected_too_short": 0,
        "rejected_not_criminal": 0,
        "rejected_missing_metadata": 0,
        "rejected_duplicate_text": 0,
        "rejected_topic_ceiling": 0,
        "rejected_below_general_fill": 0,
        "retained": 0,
    })
    per_stratum: Counter = Counter()

    def save(final: bool = False) -> None:
        # A dry run must not mutate the corpus or the ledger. Writing ledger
        # entries for candidates it never downloaded would mark them "already
        # examined" and make the real run skip them.
        if not dry_run:
            _atomic_write(JUDGMENTS_PATH,
                          json.dumps(records, indent=2, ensure_ascii=False) + "\n")
            save_ledger(ledger)
        write_provenance(records, dict(counters), run_id, per_stratum,
                         final=final, dry_run=dry_run)

    def note(row, url, decision, reason, stratum, **extra) -> None:
        if dry_run:
            return
        ledger["entries"][candidate_key(int(row["year"]), str(row["path"]).strip())] = {
            "year": int(row["year"]),
            "path": str(row["path"]).strip(),
            "case_id": str(row.get("case_id") or "").strip(),
            "citation": str(row.get("citation") or "").strip(),
            "title": str(row.get("title") or "").strip()[:180],
            "url": url,
            "stratum": stratum,
            "decision": decision,
            "reason": reason,
            "examined_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": run_id,
            **extra,
        }

    def budget_exhausted() -> bool:
        return (len(records) >= target_total
                or _stop_requested
                or (max_new is not None and counters["retained"] >= max_new))

    def consider(row, stratum: str) -> bool:
        """Examine one candidate. Returns True if it was retained."""
        path = str(row.get("path") or "").strip()
        if not path:
            return False
        key = candidate_key(int(row["year"]), path)
        if key in ledger["entries"]:
            counters["skipped_known_ledger"] += 1
            return False

        url = pdf_url_for(row)
        if not url:
            return False

        counters["candidates_examined"] += 1

        neutral = str(row.get("case_id") or "").strip()
        citation = str(row.get("citation") or "").strip()
        if reason := duplicates.check_metadata(url, neutral, citation):
            counters["skipped_duplicate_pre_download"] += 1
            note(row, url, "rejected", reason, stratum)
            return False

        if missing := missing_metadata(row):
            counters["rejected_missing_metadata"] += 1
            note(row, url, "rejected", "missing_required_metadata", stratum,
                 missing_fields=missing)
            return False

        if dry_run:
            return False

        try:
            time.sleep(REQUEST_DELAY)
            pdf_bytes = _get(url)
            text = extract_text(pdf_bytes)
            counters["pdfs_downloaded"] += 1
        except HTTPError as exc:
            counters["download_failed"] += 1
            note(row, url, "rejected", f"download_failed_http_{exc.code}", stratum)
            return False
        except Exception as exc:  # malformed PDF, transient network
            counters["download_failed"] += 1
            note(row, url, "rejected", "download_or_parse_failed", stratum)
            logger.debug("skip %s: %s", url, exc)
            return False

        if len(text) < MIN_TEXT_CHARS:
            counters["rejected_too_short"] += 1
            note(row, url, "rejected", "too_short", stratum, text_chars=len(text))
            return False

        score, statutes = criminal_score(text)
        if score < MIN_CRIMINAL_SCORE:
            counters["rejected_not_criminal"] += 1
            note(row, url, "rejected", "not_criminal", stratum, criminal_score=score)
            return False

        normalised = " ".join(text.split())
        text_sha = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        if reason := duplicates.check_text(text_sha):
            counters["rejected_duplicate_text"] += 1
            note(row, url, "rejected", reason, stratum, text_sha256=text_sha)
            return False

        topics = topics_in(text)
        accepted, reason = admit(score, topics, counts, target_total)
        if not accepted:
            counters["rejected_topic_ceiling" if reason == "topic_ceiling"
                     else "rejected_below_general_fill"] += 1
            note(row, url, "rejected", reason, stratum,
                 criminal_score=score, topics=topics)
            return False

        pdf_info = store_pdf(int(row["year"]), path, pdf_bytes)
        record = build_record(row, text, url, score, statutes, topics, stratum, pdf_info)
        records.append(record)
        duplicates.add(record)
        counts.update(topics)
        counters["retained"] += 1
        per_stratum[stratum] += 1
        note(row, url, "retained", reason, stratum,
             criminal_score=score, topics=topics,
             text_chars=len(normalised), pdf_sha256=pdf_info[1], pdf_bytes=pdf_info[2])
        return True

    # ── stratum passes ──────────────────────────────────────────────────────
    prior, outstanding = plan_strata(records, target_total)
    for stratum in STRATA:
        name, years = stratum["name"], stratum["years"]
        # Counted against the corpus, so an interrupted run resumes where the
        # stratum actually stands rather than restarting its allocation.
        kept_here = prior.get(name, 0)
        stratum_target = kept_here + outstanding.get(name, 0)
        if outstanding.get(name, 0) == 0:
            logger.info("[judgments] %s: already at %d — nothing outstanding", name, kept_here)
            continue
        # Even per-year cap first, so the allocation spreads across the stratum
        # rather than being exhausted by its first year. A second uncapped pass
        # then makes up any shortfall from the years that had material left.
        caps = [max(1, -(-stratum_target // len(years))), stratum_target]
        for cap in caps:
            if kept_here >= stratum_target or budget_exhausted():
                break
            for year in years:
                if kept_here >= stratum_target or budget_exhausted():
                    break
                frame = load_year_metadata(year, allow_download=not dry_run)
                if frame is None or frame.empty:
                    continue
                year_kept = 0
                for _, row in enumerate_rows(frame):
                    if year_kept >= cap or kept_here >= stratum_target or budget_exhausted():
                        break
                    if consider(row, name):
                        year_kept += 1
                        kept_here += 1
                        if counters["retained"] % SAVE_EVERY == 0:
                            save()
                logger.info("[judgments] %s/%s: +%d (stratum %d/%d, corpus %d)",
                            name, year, year_kept, kept_here, stratum_target, len(records))
        per_stratum.setdefault(name, 0)

    # ── quota fill ──────────────────────────────────────────────────────────
    # Only runs if the strata under-delivered or floors are still short.
    if not budget_exhausted() and unmet_floors(counts):
        logger.info("[judgments] quota fill: %d short, unmet floors %s",
                    target_total - len(records), sorted(unmet_floors(counts)))
        for year in sorted(set(sum((s["years"] for s in STRATA), ())), reverse=True):
            if budget_exhausted() or not unmet_floors(counts):
                break
            frame = load_year_metadata(year, allow_download=not dry_run)
            if frame is None or frame.empty:
                continue
            for _, row in enumerate_rows(frame):
                if budget_exhausted() or not unmet_floors(counts):
                    break
                if consider(row, "quota_fill"):
                    per_stratum["quota_fill"] += 1
                    if counters["retained"] % SAVE_EVERY == 0:
                        save()

    ledger["runs"].append({
        "run_id": run_id,
        "stratum_plan": {"prior": dict(prior), "outstanding": outstanding},
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "stopped_early": _stop_requested,
        "corpus_before": baseline,
        "corpus_after": len(records),
        "counters": dict(counters),
        "by_stratum": dict(per_stratum),
    })
    save(final=True)

    return {
        "corpus_before": baseline,
        "corpus_after": len(records),
        "new": len(records) - baseline,
        "stopped_early": _stop_requested,
        "counters": dict(counters),
        "by_stratum": dict(per_stratum),
    }


def enumerate_rows(frame):
    """Rows in download-priority order (see corpus_selection.order_candidates)."""
    rows = [row for _, row in frame.iterrows()]
    return enumerate(order_candidates(rows))


# ── provenance ──────────────────────────────────────────────────────────────


def write_provenance(records: list[dict], counters: dict, run_id: str,
                     per_stratum: Counter, final: bool = False,
                     dry_run: bool = False) -> dict:
    """
    Provenance is written atomically and from the records themselves, so it can
    never drift from the corpus the way the previous version did (its reported
    character total disagreed with the file it described by 153k characters,
    because the two were written at different times by different runs).
    """
    previous = {}
    if PROVENANCE_PATH.exists():
        try:
            previous = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}

    cumulative = Counter(previous.get("harvest_counters_cumulative", {}))
    # A dry run downloads nothing, so its counters describe no acquisition work
    # and must not accumulate into the corpus-lifetime totals.
    if not dry_run:
        cumulative.update(counters)

    ledger = load_ledger()
    decisions = Counter(entry["decision"] for entry in ledger["entries"].values())
    reasons = Counter(entry["reason"] for entry in ledger["entries"].values())

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "dry_run": dry_run,
        "complete": final,
        "count": len(records),
        "attribution": DATASET_ATTRIBUTION,
        "selection": {
            "criteria_document": "docs/corpus_selection.md",
            "min_criminal_score": MIN_CRIMINAL_SCORE,
            "min_text_chars": MIN_TEXT_CHARS,
            "section_extraction_version": SECTION_EXTRACTION_VERSION,
            "note": (
                "Filtered for criminal-law relevance by statutes and vocabulary "
                "present in the judgment text. All metadata is from the published "
                "dataset; section references are extracted from the text and kept "
                "only when a statute can be attributed to them. Nothing is "
                "inferred or invented."
            ),
        },
        "harvest_counters_this_run": counters,
        "harvest_counters_cumulative": dict(cumulative),
        "candidate_ledger": {
            "path": str(LEDGER_PATH.relative_to(config.BASE_DIR)),
            "examined_total": len(ledger["entries"]),
            "retained": decisions.get("retained", 0),
            "rejected": decisions.get("rejected", 0),
            "rejection_reasons": dict(reasons),
        },
        "by_year": dict(sorted(Counter(r["year"] for r in records).items())),
        "by_law": dict(Counter(r["law"] for r in records)),
        "by_stratum": dict(Counter(r.get("stratum", "original_260") for r in records)),
        "statutes": dict(Counter(s for r in records for s in r["statutes_referred"])),
        "topics": dict(sorted(_topic_counts(records).items(),
                              key=lambda kv: -kv[1])),
        "pdfs_retained": sum(1 for r in records if r.get("pdf_sha256")),
        "total_chars": sum(r["char_count"] for r in records),
    }
    _atomic_write(PROVENANCE_PATH, json.dumps(summary, indent=2) + "\n")
    return summary


# ── verification ────────────────────────────────────────────────────────────


def prune_incomplete() -> dict:
    """
    Drop records that lack a citation field, and record why.

    The required-metadata gate was added after harvesting had begun, so a small
    number of records were ingested from source rows with a blank `case_id`.
    They are removed here rather than left in place: the corpus contract is that
    every judgment is traceable to the court's own portal. Their PDFs are
    deleted and their ledger entries are rewritten as rejections, so a later run
    does not fetch them again.
    """
    records = load_existing()
    ledger = load_ledger()

    keep, dropped = [], []
    for record in records:
        missing = [field for field in REQUIRED_METADATA
                   if not str(record.get(field) or "").strip()]
        (dropped if missing else keep).append((record, missing))

    for record, missing in dropped:
        if pdf_path := record.get("pdf_path"):
            (config.BASE_DIR / pdf_path).unlink(missing_ok=True)
        key = candidate_key(int(record["year"]),
                            Path(record["source_url"]).name.removesuffix("_EN.pdf"))
        entry = ledger["entries"].get(key, {})
        ledger["entries"][key] = entry | {
            "decision": "rejected",
            "reason": "missing_required_metadata",
            "missing_fields": missing,
            "pruned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        logger.info("[prune] %s — missing %s", record["case_name"][:60], missing)

    kept = [record for record, _ in keep]
    _atomic_write(JUDGMENTS_PATH, json.dumps(kept, indent=2, ensure_ascii=False) + "\n")
    save_ledger(ledger)
    write_provenance(kept, {}, f"prune-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
                     Counter(), final=True)
    return {"kept": len(kept), "pruned": len(dropped),
            "cases": [r["case_name"][:60] for r, _ in dropped]}


def verify(check_pdfs: bool = True) -> bool:
    if not JUDGMENTS_PATH.exists():
        logger.error("[verify] %s missing; run the harvester first", JUDGMENTS_PATH)
        return False
    records = json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))
    problems: list[str] = []

    seen_urls: dict[str, str] = {}
    seen_neutral: dict[str, str] = {}
    seen_citation: dict[str, str] = {}
    seen_text: dict[str, str] = {}

    for record in records:
        name = record.get("case_name") or record.get("source_url")
        for field in ("case_name", "judgment_date", "judge", "citation",
                      "neutral_citation", "source_url"):
            if not str(record.get(field) or "").strip():
                problems.append(f"{name}: missing {field}")
        if not str(record.get("source_url", "")).startswith("https://"):
            problems.append(f"{name}: source_url is not an https URL")
        if record.get("court") != "Supreme Court of India":
            problems.append(f"{name}: unexpected court")
        if hashlib.sha256(record["text"].encode("utf-8")).hexdigest() != record["sha256"]:
            problems.append(f"{name}: text checksum mismatch")

        for bucket, key, label in (
            (seen_urls, record.get("source_url"), "source_url"),
            (seen_neutral, (record.get("neutral_citation") or "").strip(), "neutral_citation"),
            (seen_citation, (record.get("citation") or "").strip(), "citation"),
            (seen_text, record.get("sha256"), "text sha256"),
        ):
            if key:
                if key in bucket:
                    problems.append(f"{name}: duplicate {label} shared with {bucket[key]}")
                bucket[key] = name

        if check_pdfs and record.get("pdf_path"):
            pdf = config.BASE_DIR / record["pdf_path"]
            if not pdf.exists():
                problems.append(f"{name}: retained PDF missing at {record['pdf_path']}")
            elif hashlib.sha256(pdf.read_bytes()).hexdigest() != record["pdf_sha256"]:
                problems.append(f"{name}: PDF checksum mismatch")

    for problem in problems[:20]:
        logger.error("[verify] %s", problem)
    with_pdf = sum(1 for r in records if r.get("pdf_sha256"))
    logger.info("[verify] %d judgments, %d with retained PDF, %d problems",
                len(records), with_pdf, len(problems))
    return not problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=TARGET_TOTAL,
                        help="total corpus size to reach (not the number of new judgments)")
    parser.add_argument("--max-new", type=int, default=None,
                        help="hard cap on judgments added this run")
    parser.add_argument("--dry-run", action="store_true",
                        help="exercise merge/dedup/ledger logic with zero downloads")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--prune-incomplete", action="store_true",
                        help="drop records lacking a required citation field")
    parser.add_argument("--no-pdf-check", action="store_true",
                        help="skip PDF checksum verification")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.prune_incomplete:
        print(json.dumps(prune_incomplete(), indent=2))
        return 0

    if args.verify:
        return 0 if verify(check_pdfs=not args.no_pdf_check) else 1

    report = harvest(args.target, dry_run=args.dry_run, max_new=args.max_new)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
