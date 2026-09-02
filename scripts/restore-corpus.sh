#!/usr/bin/env bash
# restore-corpus.sh — fetch the judgment corpus that belongs with this checkout.
#
# The corpus is published as a GitHub Release asset rather than committed, because
# git stores gzip blobs whole: every expansion would add another ~40 MB to the
# repository permanently, and a clone would carry every historical version of the
# data forever. A release keeps `git clone` small and makes each corpus version an
# explicit, checksummed download.
#
# What this restores:
#   data/processed/judgments_sc/judgments.json        the corpus itself
#   data/processed/judgments_sc/candidate_ledger.json every candidate ever examined
#
# What it does NOT restore — and does not need to:
#   outputs/chroma_db/    rebuild with ./scripts/index-judgments.sh (hours of MPS)
#   data/raw/judgments/pdf/  re-fetchable per record from its own `source_url`
#
# Usage:
#   ./scripts/restore-corpus.sh            # latest release
#   ./scripts/restore-corpus.sh v3000      # a specific corpus version

set -euo pipefail

REPO="vaibhav8a/Capstone_AI_Lawline"
TAG="${1:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/data/processed/judgments_sc"

command -v gh >/dev/null || { echo "error: gh CLI not found - https://cli.github.com"; exit 1; }

mkdir -p "$DEST"
cd "$DEST"

if [ -f judgments.json ]; then
  echo "judgments.json already exists here."
  read -r -p "overwrite it? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "left untouched."; exit 0; }
fi

echo "downloading corpus assets${TAG:+ ($TAG)} ..."
if [ -n "$TAG" ]; then
  gh release download "$TAG" --repo "$REPO" --pattern '*.gz' --pattern 'SHA256SUMS.txt' --clobber
else
  gh release download --repo "$REPO" --pattern '*.gz' --pattern 'SHA256SUMS.txt' --clobber
fi

# Verify before unpacking. A truncated download must fail loudly, not produce a
# corpus that silently fails its own integrity checks later.
if [ -f SHA256SUMS.txt ]; then
  echo "verifying checksums ..."
  shasum -a 256 -c SHA256SUMS.txt || { echo "CHECKSUM MISMATCH - not unpacking"; exit 1; }
fi

echo "unpacking ..."
gunzip -f judgments.json.gz
gunzip -f candidate_ledger.json.gz
rm -f SHA256SUMS.txt

python3 - <<'PY'
import json, pathlib
p = pathlib.Path("judgments.json")
records = json.loads(p.read_text(encoding="utf-8"))
chars = sum(r["char_count"] for r in records)
print(f"  restored {len(records):,} judgments, {chars:,} characters")
print(f"  years {min(r['year'] for r in records)}-{max(r['year'] for r in records)}")
PY

cat <<'NEXT'

Corpus restored. Next:
  python -m backend.ingestion.fetch_judgments --verify        re-check metadata + duplicates
  ./scripts/index-judgments.sh                                build the vector index
NEXT
