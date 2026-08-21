"""
fetch_statutes.py — Step 1 of the ingestion pipeline: document acquisition.

Downloads each statute declared in `sources.py` from its official government URL
and records full provenance so every downstream chunk can be traced back to a
citable source.

    Internet / Public Legal Sources
                ↓
    >>> Document Acquisition  <<<   (this module)
                ↓
    Cleaning → Normalisation → Metadata → Chunking → Embedding → Index

Usage
-----
    python -m backend.ingestion.fetch_statutes            # fetch anything missing
    python -m backend.ingestion.fetch_statutes --force    # re-download everything
    python -m backend.ingestion.fetch_statutes --verify   # check cached files only

Politeness
----------
One request per document per run, a descriptive User-Agent, and a delay between
requests. Files already cached are skipped unless --force is passed, so repeated
runs generate no traffic at all.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.ingestion.sources import STATUTE_SOURCES, StatuteSource  # noqa: E402

logger = logging.getLogger(__name__)

RAW_STATUTE_DIR = config.BASE_DIR / "data" / "raw" / "statutes"
PROVENANCE_PATH = RAW_STATUTE_DIR / "provenance.json"

# Identifies this client honestly. Deliberately NOT a browser string — we do not
# impersonate a browser to get past filtering. A longer UA carrying a parenthetical
# description is rejected by the India Code WAF with HTTP 403, so this is kept to a
# plain name/version, which the server serves normally.
USER_AGENT = "LawLine-AI/1.0"
REQUEST_DELAY_SECONDS = 2.0
TIMEOUT_SECONDS = 120


def _ssl_context() -> ssl.SSLContext:
    """TLS context with a usable CA bundle.

    Python builds from python.org do not read the macOS system keychain, so the
    default context fails with CERTIFICATE_VERIFY_FAILED. We fall back to the
    `certifi` bundle. Certificate verification stays ON — these are government
    sources over TLS and an unverified fetch would defeat the point of recording
    provenance at all.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _local_path(source: StatuteSource) -> Path:
    return RAW_STATUTE_DIR / f"{source.doc_id}.pdf"


def fetch_one(source: StatuteSource, force: bool = False) -> dict:
    """Download a single statute PDF and return its provenance record."""
    dest = _local_path(source)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not force:
        logger.info("[fetch] %s already cached (%s)", source.doc_id, dest.name)
        return _provenance_record(source, dest, cached=True)

    logger.info("[fetch] downloading %s from %s", source.doc_id, source.url)
    request = Request(source.url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS, context=_ssl_context()) as response:
            content_type = response.headers.get("Content-Type", "")
            payload = response.read()
    except HTTPError as exc:
        raise RuntimeError(
            f"{source.doc_id}: server returned HTTP {exc.code} for {source.url}. "
            "The official URL may have moved — update backend/ingestion/sources.py."
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"{source.doc_id}: could not reach {source.url} ({exc.reason}). "
            "Check network connectivity."
        ) from exc

    if "pdf" not in content_type.lower():
        raise RuntimeError(
            f"{source.doc_id}: expected a PDF but server sent Content-Type "
            f"'{content_type}'. Refusing to save a non-PDF response."
        )
    if not payload.startswith(b"%PDF"):
        raise RuntimeError(
            f"{source.doc_id}: response body is not a PDF (missing %PDF header). "
            "Refusing to save; the URL may now serve an error page."
        )

    dest.write_bytes(payload)
    logger.info("[fetch] saved %s (%.1f KB)", dest.name, len(payload) / 1024)
    return _provenance_record(source, dest, cached=False)


def _provenance_record(source: StatuteSource, path: Path, cached: bool) -> dict:
    """Build the citation/provenance record stored alongside the raw file."""
    record = source.to_dict()
    record.update(
        {
            "local_path": str(path.relative_to(config.BASE_DIR)),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            # Retrieval date is part of a legal citation: statutes are amended, so
            # a reader needs to know which version of the text was consulted.
            "retrieved_at": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "reused_cache": cached,
        }
    )
    return record


def fetch_all(force: bool = False) -> list[dict]:
    records = []
    for index, source in enumerate(STATUTE_SOURCES):
        if index > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        records.append(fetch_one(source, force=force))

    RAW_STATUTE_DIR.mkdir(parents=True, exist_ok=True)
    PROVENANCE_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    logger.info("[fetch] provenance written to %s", PROVENANCE_PATH)
    return records


def verify() -> bool:
    """Re-hash cached files and compare against the recorded provenance."""
    if not PROVENANCE_PATH.exists():
        logger.error("[verify] no provenance file; run the fetcher first.")
        return False

    recorded = {r["doc_id"]: r for r in json.loads(PROVENANCE_PATH.read_text())}
    ok = True
    for source in STATUTE_SOURCES:
        entry = recorded.get(source.doc_id)
        path = _local_path(source)
        if entry is None:
            logger.error("[verify] %s missing from provenance", source.doc_id)
            ok = False
        elif not path.exists():
            logger.error("[verify] %s file missing at %s", source.doc_id, path)
            ok = False
        elif _sha256(path) != entry["sha256"]:
            logger.error("[verify] %s checksum mismatch — file changed on disk", source.doc_id)
            ok = False
        else:
            logger.info("[verify] %s OK (%s…)", source.doc_id, entry["sha256"][:12])
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if cached")
    parser.add_argument("--verify", action="store_true", help="verify checksums only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.verify:
        return 0 if verify() else 1

    records = fetch_all(force=args.force)
    print(f"\n{len(records)} statute(s) available:\n")
    for r in records:
        flag = "cached" if r["reused_cache"] else "downloaded"
        print(f"  {r['doc_id']:5} {r['title']}")
        print(f"        status={r['legal_status']}  {r['size_bytes'] / 1024:.0f} KB  ({flag})")
        print(f"        {r['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
