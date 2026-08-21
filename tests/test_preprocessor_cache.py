"""Regression tests for the silent-cache data-loss bug.

The bug
-------
`preprocess_pdf` skipped any input whose MD5 matched the manifest. The manifest
is stored globally (`config.MANIFEST_PATH`), not per output folder, so once a
document had been processed into *any* output directory it was treated as
"unchanged" for every later run — and skipped even when the target directory
contained no output for it.

The document then vanished from the corpus with no error, no entry in the
failure tally, and a run summary that read as success. It is how
`University_Of_Kerala_...PDF` went missing from a run that reported "12 OK".

The fix makes an unchanged hash a reason to skip only when the expected output
file actually exists. These tests pin all four combinations of (hash matches,
output present).
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

import config
import preprocessor


def _json_name_for(pdf_name: str) -> str:
    """Mirror the output-name derivation in preprocess_pdf."""
    base = Path(pdf_name).stem
    return re.sub(r"[^a-zA-Z0-9_]", "_", base) + ".json"


@pytest.fixture
def fake_pdf(tmp_path):
    pdf = tmp_path / "Some_Case_vs_Another_on_1_January_2020.PDF"
    pdf.write_bytes(b"%PDF-1.4\nnot a real pdf body\n")
    return pdf


@pytest.fixture
def manifest_at(tmp_path, monkeypatch):
    """Point the global manifest at a temp file so tests never touch the real one."""
    path = tmp_path / "manifest.json"
    monkeypatch.setattr(config, "MANIFEST_PATH", path, raising=False)
    return path


def _write_manifest(path: Path, filename: str, file_hash: str):
    path.write_text(json.dumps({filename: file_hash}), encoding="utf-8")


def _hash_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


class TestSkipDecision:
    """The four states the cache can be in."""

    def test_hash_matches_and_output_present_skips(self, fake_pdf, tmp_path, manifest_at):
        out = tmp_path / "out"
        out.mkdir()
        (out / _json_name_for(fake_pdf.name)).write_text("{}", encoding="utf-8")
        _write_manifest(manifest_at, fake_pdf.name, _hash_of(fake_pdf))

        result = preprocessor.preprocess_pdf((str(fake_pdf), str(out), "eng"))
        assert result["status"] == "skipped"

    def test_hash_matches_but_output_missing_reprocesses(self, fake_pdf, tmp_path, manifest_at):
        """The regression. Previously returned 'skipped' and silently lost the doc."""
        out = tmp_path / "out"
        out.mkdir()
        _write_manifest(manifest_at, fake_pdf.name, _hash_of(fake_pdf))

        result = preprocessor.preprocess_pdf((str(fake_pdf), str(out), "eng"))
        assert result["status"] != "skipped", (
            "a cached hash must not skip a document whose output is absent — "
            "that is the silent data-loss bug"
        )

    def test_output_directory_changed_reprocesses(self, fake_pdf, tmp_path, manifest_at):
        """Same input, new output folder: the new folder must get its own output."""
        first = tmp_path / "run1"
        first.mkdir()
        (first / _json_name_for(fake_pdf.name)).write_text("{}", encoding="utf-8")
        _write_manifest(manifest_at, fake_pdf.name, _hash_of(fake_pdf))

        second = tmp_path / "run2"
        second.mkdir()
        result = preprocessor.preprocess_pdf((str(fake_pdf), str(second), "eng"))
        assert result["status"] != "skipped"

    def test_hash_differs_reprocesses(self, fake_pdf, tmp_path, manifest_at):
        out = tmp_path / "out"
        out.mkdir()
        (out / _json_name_for(fake_pdf.name)).write_text("{}", encoding="utf-8")
        _write_manifest(manifest_at, fake_pdf.name, "0" * 32)

        result = preprocessor.preprocess_pdf((str(fake_pdf), str(out), "eng"))
        assert result["status"] != "skipped"

    def test_no_manifest_entry_reprocesses(self, fake_pdf, tmp_path, manifest_at):
        out = tmp_path / "out"
        out.mkdir()
        manifest_at.write_text("{}", encoding="utf-8")

        result = preprocessor.preprocess_pdf((str(fake_pdf), str(out), "eng"))
        assert result["status"] != "skipped"


class TestCorpusCompleteness:
    """Every judgment PDF in Dataset/ must have a parsed output."""

    def test_all_dataset_pdfs_are_represented(self):
        dataset = config.BASE_DIR / "Dataset"
        processed = config.BASE_DIR / "data" / "processed" / "judgments"
        if not processed.exists():
            pytest.skip("judgment corpus not built")

        # _quarantine holds inputs deliberately excluded; see its README.
        pdfs = [p for p in dataset.glob("*.PDF") if "_quarantine" not in p.parts]
        outputs = {p.name for p in processed.glob("*.json")}

        missing = [p.name for p in pdfs if _json_name_for(p.name) not in outputs]
        assert not missing, f"judgment PDFs with no parsed output: {missing}"

    def test_quarantined_files_are_not_ingested(self):
        quarantine = config.BASE_DIR / "Dataset" / "_quarantine"
        if not quarantine.exists():
            pytest.skip("no quarantine directory")
        processed = config.BASE_DIR / "data" / "processed" / "judgments"
        if not processed.exists():
            pytest.skip("judgment corpus not built")

        outputs = {p.name for p in processed.glob("*.json")}
        for junk in quarantine.glob("*.pdf"):
            assert _json_name_for(junk.name) not in outputs
