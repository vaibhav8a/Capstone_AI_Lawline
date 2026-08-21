"""
Production-Grade Universal Legal Document Preprocessor
======================================================
Handles Supreme Court, High Court, District Court, Tribunal,
and mixed-format PDFs at scale (10,000+ files).

Features:
  - Adaptive header/footer removal
  - Universal metadata extraction with fallbacks
  - Document type classification
  - Language detection (EN, HI, KN, TA, TE)
  - Multi-format section detection with 40+ aliases
  - Semantic chunking for RAG ingestion
  - Universal citation detection and linking
  - Robust OCR fallback with error recovery
  - Memory-safe multiprocessing
  - Enhanced per-file logging
"""

import os
import re
import json
import hashlib
import time
import io
import argparse
import unicodedata
import logging
import cv2
import numpy as np
from collections import OrderedDict, Counter
from multiprocessing import Pool, cpu_count

import fitz
import pytesseract
from PIL import Image
from tqdm import tqdm

try:
    from unstructured.partition.pdf import partition_pdf
except ImportError:
    partition_pdf = None

try:
    import config
except ImportError:
    config = None

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    filename="preprocessing.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filemode="a",
)
log = logging.getLogger("preprocessor")

# ============================================================
# CONSTANTS — SECTION SYSTEM
# ============================================================

SECTION_ALIASES = {
    # Summary / Headnote
    "HEADNOTE": "SUMMARY", "HEAD NOTE": "SUMMARY",
    "SYNOPSIS": "SUMMARY", "ABSTRACT": "SUMMARY",
    # Facts
    "FACTS": "FACTS", "FACT": "FACTS",
    "BRIEF FACTS": "FACTS", "FACTS OF THE CASE": "FACTS",
    "FACTUAL BACKGROUND": "FACTS", "BACKGROUND": "FACTS",
    "FACTUAL MATRIX": "FACTS", "CASE OF THE PROSECUTION": "FACTS",
    "CASE OF THE COMPLAINANT": "FACTS", "PROSECUTION CASE": "FACTS",
    "RIVAL CONTENTIONS": "FACTS",
    # Arguments
    "ARGUMENTS": "ARGUMENTS", "ARGUMENT": "ARGUMENTS",
    "CONTENTIONS": "ARGUMENTS", "CONTENTION": "ARGUMENTS",
    "SUBMISSIONS": "ARGUMENTS", "SUBMISSION": "ARGUMENTS",
    "SUBMISSIONS ON BEHALF OF THE PETITIONER": "ARGUMENTS",
    "SUBMISSIONS ON BEHALF OF THE APPELLANT": "ARGUMENTS",
    "SUBMISSIONS ON BEHALF OF THE RESPONDENT": "ARGUMENTS",
    "SUBMISSIONS ON BEHALF OF STATE": "ARGUMENTS",
    "SUBMISSIONS ON BEHALF OF THE PROSECUTION": "ARGUMENTS",
    "ARGUMENTS ON BEHALF OF THE PETITIONER": "ARGUMENTS",
    "ARGUMENTS ON BEHALF OF THE RESPONDENT": "ARGUMENTS",
    "ARGUMENTS ON BEHALF OF STATE": "ARGUMENTS",
    "LEARNED COUNSEL FOR THE PETITIONER": "ARGUMENTS",
    "LEARNED COUNSEL FOR THE RESPONDENT": "ARGUMENTS",
    # Reasoning
    "JUDGMENT": "REASONING", "JUDGEMENT": "REASONING",
    "ANALYSIS": "REASONING", "DISCUSSION": "REASONING",
    "FINDINGS": "REASONING", "FINDING": "REASONING",
    "REASONS": "REASONING", "CONSIDERATION": "REASONING",
    "ANALYSIS AND FINDINGS": "REASONING",
    "ANALYSIS AND DISCUSSION": "REASONING",
    "DISCUSSION AND FINDINGS": "REASONING",
    "REASONING AND ORDER": "REASONING",
    "REASONS FOR THE ORDER": "REASONING",
    "CONSIDERATION OF THE COURT": "REASONING",
    # Decision / Order
    "DECISION": "DECISION", "ORDER": "ORDER",
    "DIRECTIONS": "ORDER", "DIRECTION": "ORDER",
    "DECREE": "ORDER", "RELIEF": "ORDER",
    "DISPOSITION": "ORDER", "FINAL ORDER": "ORDER",
    "OPERATIVE ORDER": "ORDER", "RESULT": "ORDER",
    # Key Points
    "HELD": "KEY_POINTS", "RATIO": "KEY_POINTS",
    "RATIO DECIDENDI": "KEY_POINTS",
    "CONCLUSION": "KEY_POINTS",
    # Issues
    "ISSUES": "ISSUES", "ISSUE": "ISSUES",
    "QUESTIONS": "ISSUES", "POINTS FOR DETERMINATION": "ISSUES",
    "QUESTIONS OF LAW": "ISSUES", "FRAMING OF ISSUES": "ISSUES",
    # Metadata-class
    "BENCH": "CASE_METADATA", "CITATION": "CASE_METADATA",
    "ACT": "CASE_METADATA",
    "CORAM": "CASE_METADATA",
    # INDEX is deliberately excluded — it produces noise sections
}

# Build regex dynamically from alias keys (longest first to avoid partial matches)
_section_keys = sorted(SECTION_ALIASES.keys(), key=len, reverse=True)
_section_alt = "|".join(re.escape(k) for k in _section_keys)
SECTION_PATTERN = re.compile(
    rf"(?im)^\s*(?:\d+[\.\)\-]\s*)?({_section_alt})\s*[:.\-]?\s*$"
)

# ============================================================
# CONSTANTS — HEADER / FOOTER NOISE
# ============================================================

HEADER_FOOTER_PATTERNS = [
    re.compile(r".*Indian\s+Kanoon.*", re.I),
    re.compile(r"Page\s+\d+\s+of\s+\d+", re.I),
    re.compile(r"https?://\S+", re.I),
    re.compile(r"www\.\S+", re.I),
    re.compile(r"Downloaded\s+from.*", re.I),
    re.compile(r"CITATOR\s+INFO.*", re.I),
    re.compile(r"SCC\s+Online\s+Web.*", re.I),
    re.compile(r"^\s*\d{1,3}\s*$"),
    re.compile(r"^\s*-\s*\d+\s*-\s*$"),
    re.compile(r"By:\s*\w+.*Signing\s+Date.*", re.I),
    re.compile(r"Digitally\s+signed\s+by.*", re.I),
    re.compile(r"Signature\s+Not\s+Verified", re.I),
    re.compile(r"^\s*Neutral\s+Citation.*", re.I),
]

# ============================================================
# CONSTANTS — OCR FIXES (30+ legal terms)
# ============================================================

OCR_COMMON_FIXES = {
    r"\bno where\b": "nowhere",
    r"\bany where\b": "anywhere",
    r"\bevery where\b": "everywhere",
    r"\bdeliritious\b": "deleterious",
    r"\bdiscretionery\b": "discretionary",
    r"\bageement\b": "agreement",
    r"\bdocumen\b": "document",
    r"\breciev\b": "receive",
    r"\bloan\s+ageement\b": "loan agreement",
    r"\bunder\s+stamping\b": "under-stamping",
    r"\bchar\s+erise\b": "characterise",
    r"\bion\s+taken\b": "action taken",
    r"\bf\s+that\b": "fact that",
    r"\battr\s+s\b": "attracts",
    r"\bpro\s+posed\b": "proposed",
    r"\ben\s+forced\b": "enforced",
    r"\bmis\s+take\b": "mistake",
    r"\bSecret\s+any\b": "Secretary",
    r"\bjudg\s+ment\b": "judgment",
    r"\bpeti\s+tioner\b": "petitioner",
    r"\brespon\s+dent\b": "respondent",
    r"\bappel\s+lant\b": "appellant",
    r"\bconsti\s+tution\b": "constitution",
    r"\bfunda\s+mental\b": "fundamental",
    r"\bjuris\s+diction\b": "jurisdiction",
    r"\bprocee\s+dings\b": "proceedings",
    r"\bcontem\s+pt\b": "contempt",
    r"\bexecu\s+tion\b": "execution",
    r"\binjunc\s+tion\b": "injunction",
    r"\brevision\s+al\b": "revisional",
}
_ocr_compiled = [(re.compile(p, re.I), r) for p, r in OCR_COMMON_FIXES.items()]

# ============================================================
# CONSTANTS — CITATIONS
# ============================================================

CITATION_PATTERNS = [
    re.compile(r"\(\d{4}\)\s*\d+\s*SCC\s*\d+"),
    re.compile(r"AIR\s*\d{4}\s*[A-Z]+\s*\d+"),
    re.compile(r"\(\d{4}\)\s*\d+\s*SCR\s*\d+"),
    re.compile(r"\d{4}\s*CriLJ\s*\d+"),
    re.compile(r"\(\d{4}\)\s*\d+\s*SCALE\s*\d+"),
    re.compile(r"\d{4}\s*SCC\s*\(?\d*\)?\s*\d+"),
    re.compile(r"\[\d{4}\]\s*\d+\s*All\s*ER\s*\d+"),
    re.compile(r"\[\d{4}\]\s*\d+\s*AC\s*\d+"),
    re.compile(r"\[\d{4}\]\s*\d+\s*WLR\s*\d+"),
    re.compile(r"MANU/[A-Z]{2}/\d+/\d{4}"),
    re.compile(r"\d{4}\s*SCR\s*Supl\.?\s*\(?\d*\)?\s*\d+"),
    re.compile(r"\(\d{4}\)\s*\d+\s*ILR\s*\d+"),
]

CASE_REGEX = re.compile(
    r"([A-Z][A-Za-z\.\s&\-()]{2,60}\s+v\.?\s+[A-Z][A-Za-z\.\s&\-()]{2,60})"
)

# ============================================================
# CONSTANTS — METADATA
# ============================================================

METADATA_PATTERNS = {
    "court": [
        r"IN\s+THE\s+(SUPREME\s+COURT\s+OF\s+INDIA)",
        r"IN\s+THE\s+(HIGH\s+COURT\s+OF\s+[A-Z][A-Z\s]+?)(?:\s+AT|\s*\n)",
        r"IN\s+THE\s+(.+?COURT[^\n]{0,60})",
        r"(NATIONAL\s+(?:GREEN|COMPANY\s+LAW)\s+TRIBUNAL[^\n]*)",
        r"(DEBT\s+RECOVERY\s+TRIBUNAL[^\n]*)",
    ],
    "case_number": [
        r"(?:Criminal|Civil)\s+Appeal\s*(?:No\.?|Nos?\.?)\s*([\d\/\-]+(?:\s*of\s*\d{4})?)",
        r"(?:Writ\s+Petition|W\.?P\.?)\s*\(?(?:C|Crl)?\)?\s*(?:No\.?|Nos?\.?)\s*([\d\/\-]+(?:\s*of\s*\d{4})?)",
        r"(?:SLP|Special\s+Leave\s+Petition)\s*\(?(?:C|Crl)?\)?\s*(?:No\.?|Nos?\.?)\s*([\d\/\-]+(?:\s*of\s*\d{4})?)",
        r"(?:Transfer\s+Petition|T\.?P\.?)\s*\(?(?:C|Crl)?\)?\s*(?:No\.?|Nos?\.?)\s*([\d\/\-]+(?:\s*of\s*\d{4})?)",
        r"(?:Case|C\.?C\.?|O\.?S\.?|Crl\.?A\.?)\s*No\.?\s*[:\-]?\s*([\w\d\/\-]+(?:\s*of\s*\d{4})?)",
        r"(?:I\.?A\.?|M\.?A\.?)\s*No\.?\s*([\d\/\-]+(?:\s*of\s*\d{4})?)",
    ],
    "date": [
        r"Judgment\s+pronounced\s+on\s*[:\-]?\s*([\d\s\./]+\d{4})",
        r"Judgment\s+reserved\s+on\s*[:\-]?\s*([\d\s\./]+\d{4})",
        r"DATE\s+OF\s+JUDGMENT\s*[:\-]?\s*(\d{1,2}[\s\/\-]\w+[\s\/\-]\d{4})",
        r"Date\s+of\s+Judgment\s*[:\-]?\s*(\d{1,2}[\s\.\/\-]\w+[\s\.\/\-]\d{4})",
        r"DATED?\s*[:\-]?\s*(?:THIS\s+THE\s+)?(\d{1,2}[\s\/\-]\w+[\s\/\-]\d{4})",
        r"Judgment\s+(?:dated?|delivered)\s*[:\-]?\s*(\d{1,2}[\s\/\-]\w+[\s\/\-]\d{4})",
        r"(\d{1,2}\.\d{1,2}\.\d{4})",
        r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})",
    ],
    "case_title": [
        r"PETITIONER:\s*([A-Z][A-Za-z\s\.&]+?)\s+Vs\.\s+RESPONDENT:\s*([A-Z][A-Za-z\s\.&]+?)(?=\s+DATE|\n|$)",
        # Party names must be captured GREEDILY and anchored to the start of a line.
        # The previous pattern used a lazy quantifier with a character class that
        # excluded '(', ')', digits and hyphens. On a header like
        #   "Justice K.S. Puttaswamy (Retd.) And Anr vs Union Of India And Ors"
        # it could not span "(Retd.)", so the shortest right-most match won and the
        # petitioner was recorded as "And Anr" — losing the party name on 455 chunks.
        # Anchoring to ^ and matching greedily keeps the full party name intact.
        r"^[ \t]*([A-Z][A-Za-z0-9\s\.\&,'\-\(\)]{3,90})\s+(?:vs?\.?|Vs?\.?|VERSUS|versus)\s+"
        r"([A-Z][A-Za-z0-9\s\.\&,'\-\(\)]{3,90}?)(?=\s+on\s+\d|\s+S/o|\s+W/o|\s*$)",
    ],
}

# Connective/procedural fragments that can never begin a real party name. Used to
# reject a mis-anchored title match rather than silently storing a truncated one.
TITLE_LEADING_JUNK = re.compile(
    r"^(?:and\s+(?:anr|ors|another|others)|anr|ors|another|others|etc)\b",
    re.I,
)

# ============================================================
# CONSTANTS — DOCUMENT TYPE
# ============================================================

DOC_TYPE_PATTERNS = [
    (re.compile(r"SUPREME\s+COURT\s+OF\s+INDIA", re.I), "SUPREME_COURT"),
    (re.compile(r"HIGH\s+COURT", re.I), "HIGH_COURT"),
    (re.compile(r"DISTRICT\s+(?:COURT|JUDGE|SESSIONS)", re.I), "DISTRICT_COURT"),
    (re.compile(r"TRIBUNAL|APPELLATE\s+TRIBUNAL", re.I), "TRIBUNAL"),
    (re.compile(r"CONSUMER\s+(?:DISPUTES?|FORUM)", re.I), "CONSUMER_FORUM"),
    (re.compile(r"NATIONAL\s+(?:GREEN|COMPANY)", re.I), "TRIBUNAL"),
]

# ============================================================
# TEXT CLEANING
# ============================================================

def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def apply_ocr_fixes(text: str) -> str:
    for pat, repl in _ocr_compiled:
        text = pat.sub(repl, text)
    return text


def fix_glued_words(text: str) -> str:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([.,;:])([A-Za-z])", r"\1 \2", text)
    return text


def reconstruct_lines(text: str) -> str:
    """Rejoin hyphenated line breaks common in PDF extraction."""
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return text


def remove_headers(text: str) -> str:
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        if any(pat.search(line) for pat in HEADER_FOOTER_PATTERNS):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def clean_leading_symbols(text: str) -> str:
    return re.sub(r"(?m)^[\s:,.\-;]+", "", text)


def normalize_text(text: str) -> str:
    text = normalize_unicode(text)
    text = reconstruct_lines(text)
    text = remove_headers(text)
    text = apply_ocr_fixes(text)
    text = fix_glued_words(text)
    text = clean_leading_symbols(text)
    # Collapse horizontal whitespace within lines, but PRESERVE newlines
    # so that section heading regex (^...$) can match line boundaries
    lines = text.split("\n")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in lines]
    # Remove blank lines but keep structure
    text = "\n".join(line for line in lines if line)
    return text.strip()


def detect_repeating_headers(pages: list, threshold: float = 0.7) -> list:
    """Detect lines that repeat across >threshold of pages (running headers)."""
    if len(pages) < 4:
        return []
    first_lines = []
    last_lines = []
    for p in pages:
        lines = p.get("raw_text", "").strip().split("\n")
        if lines:
            first_lines.append(lines[0].strip()[:80])
        if len(lines) > 1:
            last_lines.append(lines[-1].strip()[:80])

    repeating = []
    for line_list in [first_lines, last_lines]:
        counts = Counter(line_list)
        for line, cnt in counts.items():
            if cnt / len(pages) >= threshold and len(line) > 5:
                repeating.append(line)
    return repeating


def strip_repeating_headers(text: str, headers: list) -> str:
    for h in headers:
        text = text.replace(h, "")
    return text

# ============================================================
# LANGUAGE DETECTION
# ============================================================

_LANG_RANGES = {
    "hi": (0x0900, 0x097F),   # Devanagari
    "kn": (0x0C80, 0x0CFF),   # Kannada
    "ta": (0x0B80, 0x0BFF),   # Tamil
    "te": (0x0C00, 0x0C7F),   # Telugu
    "ml": (0x0D00, 0x0D7F),   # Malayalam
    "bn": (0x0980, 0x09FF),   # Bengali
    "gu": (0x0A80, 0x0AFF),   # Gujarati
    "pa": (0x0A00, 0x0A7F),   # Gurmukhi (Punjabi)
    "or": (0x0B00, 0x0B7F),   # Odia
}


def detect_language(text: str) -> dict:
    counts = {"en": 0}
    for lang, (lo, hi) in _LANG_RANGES.items():
        counts[lang] = 0
    for ch in text:
        cp = ord(ch)
        if 0x0041 <= cp <= 0x007A:
            counts["en"] += 1
        else:
            for lang, (lo, hi) in _LANG_RANGES.items():
                if lo <= cp <= hi:
                    counts[lang] += 1
                    break
    total = sum(counts.values()) or 1
    primary = max(counts, key=counts.get)
    secondary = [
        lang for lang, c in counts.items()
        if lang != primary and c / total > 0.05
    ]
    return {"primary": primary, "secondary": secondary}

# ============================================================
# DOCUMENT TYPE CLASSIFICATION
# ============================================================

def classify_document(text: str) -> str:
    for pat, doc_type in DOC_TYPE_PATTERNS:
        if pat.search(text[:3000]):
            return doc_type
    return "UNKNOWN"

# ============================================================
# METADATA EXTRACTION (Universal with Fallbacks)
# ============================================================

def _validate_court(val: str) -> bool:
    """Court name must contain COURT/TRIBUNAL, be short, and not look like a sentence."""
    if not (5 < len(val) < 100):
        return False
    upper = val.upper()
    if "COURT" not in upper and "TRIBUNAL" not in upper:
        return False
    # Reject sentence fragments (periods followed by lowercase = body text)
    if re.search(r"\. [a-z]", val):
        return False
    # Reject if it contains common body-text words
    if any(w in upper for w in ["CONCEDED", "SUBMITTED", "ARGUED", "STATED", "HELD THAT", "OBSERVED"]):
        return False
    return True

INDIAN_COURT_CITIES = (
    "DELHI", "NEW DELHI", "MUMBAI", "BOMBAY", "KOLKATA", "CALCUTTA", "CHENNAI",
    "MADRAS", "BENGALURU", "BANGALORE", "HYDERABAD", "AHMEDABAD", "PUNE",
    "LUCKNOW", "ALLAHABAD", "PATNA", "JAIPUR", "CHANDIGARH", "KOCHI", "ERNAKULAM",
    "GUWAHATI", "BHOPAL", "INDORE", "NAGPUR", "CUTTACK", "RANCHI", "SHIMLA",
    "SRINAGAR", "JAMMU", "PANAJI", "GANDHINAGAR", "THIRUVANANTHAPURAM",
)


def _extract_court_city(text: str) -> str | None:
    """Return the first recognised Indian court city mentioned in `text`.

    Used to build a canonical district-court name without dragging the presiding
    judge's name into the court field.
    """
    if not text:
        return None
    upper = text.upper()
    # Longest names first so "NEW DELHI" wins over "DELHI".
    for city in sorted(INDIAN_COURT_CITIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(city)}\b", upper):
            return city
    return None


def _validate_case_number(val: str) -> bool:
    return 3 < len(val) < 80 and any(c.isdigit() for c in val)

def _validate_date(val: str) -> bool:
    return 6 < len(val) < 30 and any(c.isdigit() for c in val)

def _validate_title(val: str) -> bool:
    if not (5 < len(val) < 200):
        return False
    # A title whose petitioner side starts with a connective ("And Anr v. X") means
    # the party name was truncated by a mis-anchored match. Reject so the caller
    # falls through to the next pattern / the filename-based fallback.
    petitioner = re.split(r"\s+(?:v\.?|vs\.?|VERSUS)\s+", val, maxsplit=1, flags=re.I)[0]
    if TITLE_LEADING_JUNK.match(petitioner.strip()):
        return False
    return True


def clean_case_title(title: str) -> str:
    """Strip advocate names, procedural labels, and junk from a raw case title."""
    # Flatten newlines first
    title = re.sub(r"\n+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()

    # Split on " v. " or " v " or " vs " or " vs. "
    parts = re.split(r"\s+(?:v\.?|vs\.?|V\.?|VS\.?)\s+", title, maxsplit=1)
    if len(parts) != 2:
        return title.strip()

    cleaned = []
    for part in parts:
        p = part.strip()
        # Remove "Through Sh./Smt./Mr./Ms." and everything before the party name
        p = re.sub(r"(?i)^.*?Through\s+(?:Sh\.?|Smt\.?|Mr\.?|Ms\.?|Mrs\.?|Shri\.?)\s+\S+.*", "", p)
        # If Through removed everything, skip further cleaning
        if not p.strip():
            p = part.strip()
            # Try extracting just the entity name (before "Through")
            before_through = re.match(r"(?i)(.+?)\s+Through\b", part)
            if before_through:
                p = before_through.group(1).strip()
            else:
                p = re.sub(r"(?i)Through\s+.*", "", part).strip()

        # Remove "Represented by..."
        p = re.sub(r"(?i)\bRepresented\s+by\s+.*", "", p)
        # Remove advocate designations
        p = re.sub(r"(?i),?\s*(?:Mr\.?|Ms\.?|Mrs\.?|Sh\.?|Shri\.?|Smt\.?)\s+[A-Z][A-Za-z\s\.]+,?\s*(?:Advs?\.?|Advocates?|Sr\.?\s*Adv|Counsel).*", "", p)
        # Remove standalone advocate/counsel fragments
        p = re.sub(r"(?i),?\s*(?:Advs?\.?|Advocates?|Counsel|Sr\.?\s*Counsel)\s*$", "", p)
        # Remove lines starting with Mr/Ms (lawyer names in multi-line)
        p = re.sub(r"(?i)(?:,\s*)?(?:Mr|Ms|Mrs|Sh|Smt)\.?\s+[A-Z][A-Za-z\s\.]+(?:,\s*(?:Mr|Ms|Mrs|Sh|Smt)\.?\s+[A-Z][A-Za-z\s\.]+)*\s*$", "", p)
        # Remove Plaintiff/Defendant/Petitioner/Respondent labels (with dots)
        p = re.sub(r"(?i)\s*\.{2,}\s*(?:Plaintiffs?|Defendants?|Petitioners?|Respondents?|Appellants?|Complainants?|Plainti)\s*", "", p)
        # Remove trailing "Respond" (truncated Respondent)
        p = re.sub(r"(?i)\s*\.{2,}\s*Respond\w*\s*$", "", p)
        # Remove "S/o", "W/o", "D/o" and everything after
        p = re.sub(r"(?i)\s+[SWDR]/[Oo]\s+.*", "", p)
        # Remove address lines (R/o, R/at)
        p = re.sub(r"(?i)\s+R/[Oo]\s+.*", "", p)
        # Remove age fragments
        p = re.sub(r"(?i),?\s*(?:Aged?|Age)\s+(?:about\s+)?\d+.*", "", p)
        # Remove Registered Office / Branch Office lines
        p = re.sub(r"(?i)\s*Registered\s+Office.*", "", p)
        # Remove dot-chains (3+ dots)
        p = re.sub(r"\.{3,}", "", p)
        # Remove trailing/leading junk
        p = re.sub(r"[\s.,\-:;]+$", "", p)
        p = re.sub(r"^[\s.,\-:;]+", "", p)
        p = re.sub(r"\s+", " ", p).strip()
        cleaned.append(p)

    # Validate both sides have content
    if len(cleaned[0]) < 3 or len(cleaned[1]) < 3:
        return title.strip()  # fallback to original if cleaning destroyed it

    return f"{cleaned[0]} v. {cleaned[1]}"


def extract_metadata(header_text: str, full_text: str) -> dict:
    """Extract metadata. header_text = first 3 pages, full_text = entire doc.
    Court, title, date, bench use header_text to avoid body contamination.
    Decision uses full_text since it appears at the end."""
    metadata = {}

    # Court — search header only
    for pat in METADATA_PATTERNS["court"]:
        m = re.search(pat, header_text, re.I)
        if m:
            val = m.group(1).strip()
            if _validate_court(val):
                metadata["court"] = val
                break

    # Supreme Court fallback for Indian Kanoon PDFs
    if "court" not in metadata:
        if re.search(r"PETITIONER", header_text) and re.search(r"RESPONDENT", header_text):
            metadata["court"] = "SUPREME COURT OF INDIA"

    # District Court fallback.
    # This must yield a *court*, not the presiding judge. The earlier version stored
    # the whole "BEFORE THE COURT OF SH. <JUDGE NAME>, DISTRICT JUDGE ..." string,
    # which put a judge's name in the court field and meant COURT_MULTIPLIERS never
    # matched it. Normalise to a canonical "<TIER> COURT, <CITY>" instead.
    if "court" not in metadata:
        dist = re.search(
            r"(?:BEFORE|COURT\s+OF)\s+.*?(DISTRICT|SESSIONS|ADDITIONAL)\s+(?:JUDGE|COURT)([^\n]*)",
            header_text,
            re.I,
        )
        if dist:
            tier = dist.group(1).upper()
            trailer = dist.group(2) or ""
            city = _extract_court_city(trailer) or _extract_court_city(header_text)
            metadata["court"] = f"{tier} COURT, {city}" if city else f"{tier} COURT"

    # Case number — header only
    for pat in METADATA_PATTERNS["case_number"]:
        m = re.search(pat, header_text, re.I)
        if m:
            val = m.group(1).strip()
            if _validate_case_number(val):
                metadata["case_number"] = val
                break

    # Date — header first, then full text
    for search_text in [header_text, full_text]:
        if "date" in metadata:
            break
        for pat in METADATA_PATTERNS["date"]:
            m = re.search(pat, search_text, re.I)
            if m:
                val = re.sub(r"\s+", "", m.group(1).strip())  # normalize spaces in date
                if _validate_date(val):
                    metadata["date"] = val
                    break

    # Case title — PETITIONER/RESPONDENT format first (Indian Kanoon), then header "v." match
    pet_match = re.search(
        r"PETITIONER:\s*([A-Z][A-Za-z\s\.&()]+?)\s+(?:Vs?\.?)\s+RESPONDENT:\s*([A-Z][A-Za-z\s\.&()]+?)(?=\s+DATE|\s+BENCH|\n|$)",
        header_text, re.I,
    )
    if pet_match:
        pt1 = pet_match.group(1).strip()
        pt2 = pet_match.group(2).strip()
        raw_title = f"{pt1} v. {pt2}"
        if _validate_title(raw_title):
            metadata["case_title"] = clean_case_title(raw_title)

    if "case_title" not in metadata:
        # Try all v.-based patterns on header, but skip matches containing junk indicators
        for pat in METADATA_PATTERNS["case_title"]:
            all_matches = list(re.finditer(pat, header_text[:3000], re.I))
            for m in all_matches:
                try:
                    pt1 = m.group(1).strip()
                    pt2 = m.group(2).strip()
                    raw_title = f"{pt1} v. {pt2}"
                except IndexError:
                    raw_title = m.group(1).strip()

                # Skip if it contains lawyer/procedural junk
                skip = False
                junk_words = ["Through", "Adv.", "Advocate", "Counsel", "Plainti", ".........", "harma,"]
                for jw in junk_words:
                    if jw.lower() in raw_title.lower():
                        skip = True
                        break
                if skip:
                    continue

                cleaned_title = clean_case_title(raw_title)
                if _validate_title(cleaned_title) and len(cleaned_title) < 120:
                    metadata["case_title"] = cleaned_title
                    break
            if "case_title" in metadata:
                break

    # Decision — search full text (outcome usually at the end)
    dec = re.search(
        r"\b(appeal\s+(?:is\s+)?(?:allowed|dismissed)|"
        r"petition\s+(?:is\s+)?(?:dismissed|allowed)|"
        r"disposed\s+of|partly\s+allowed|"
        r"writ\s+petition\s+(?:is\s+)?allowed)\b",
        full_text, re.I,
    )
    if dec:
        metadata["decision"] = dec.group(1).strip().title()

    # Bench / Judges — header only
    judges = re.findall(
        r"(?:Hon['\u2019]?ble\s+)?(?:Mr\.?|Mrs\.?|Ms\.?)?\s*Justice\s+([A-Z][A-Za-z\s\.]+)",
        header_text,
    )
    if judges:
        metadata["bench"] = [j.strip() for j in sorted(set(judges))]

    return metadata

# ============================================================
# ENTITY EXTRACTION
# ============================================================

def extract_entities(text: str) -> dict:
    # Statutes / Legal provisions
    statute_patterns = [
        r"(?:[A-Z][a-zA-Z]+\s+){2,10}(?:Act|Code|Rules|Regulation)",
        r"Article\s+\d+[A-Za-z0-9()]*",
        r"Section\s+\d+[A-Za-z0-9()]*(?:\s+of\s+the\s+[A-Z][A-Za-z\s]+(?:Act|Code))?",
    ]
    statutes = []
    for p in statute_patterns:
        statutes.extend(re.findall(p, text))

    # Case names (separate category)
    case_names = []
    for m in CASE_REGEX.findall(text):
        m = re.sub(r"\s+", " ", m).strip()
        m = re.sub(r"^(In|See|Re|Also|The)\s+", "", m, flags=re.I)
        # Must contain " v." or " v " to be a case name
        if " v." in m or " v " in m or " V." in m:
            # Length guard: reject if either party name > 60 chars
            parts = re.split(r"\s+v\.?\s+", m, flags=re.I)
            if all(5 < len(p.strip()) < 60 for p in parts if p.strip()):
                case_names.append(m)

    cleaned_statutes = sorted(set(
        re.sub(r"\s+", " ", s).strip()
        for s in statutes if len(s.strip()) > 5
    ))
    cleaned_cases = sorted(set(case_names))

    return {
        "statutes": cleaned_statutes,
        "case_names": cleaned_cases,
    }

# ============================================================
# CITATION DETECTION & LINKING
# ============================================================

def extract_citations(text: str) -> list:
    raw = []
    for pat in CITATION_PATTERNS:
        raw.extend(pat.findall(text))
    cleaned = sorted(set(re.sub(r"\s+", " ", c).strip().upper() for c in raw))
    return cleaned


def extract_case_citations(text: str, last_case_name: str = None) -> tuple[list, str]:
    case_matches = list(CASE_REGEX.finditer(text))
    citation_matches = []
    for pat in CITATION_PATTERNS:
        citation_matches.extend(pat.finditer(text))

    # Pattern for short-form references
    SHORT_FORM_PATTERNS = {
        "ibid": re.compile(r"\bibid\.?\b", re.I),
        "id": re.compile(r"\bid\.?\b", re.I),
        "supra": re.compile(r"\bsupra\b", re.I),
    }

    linked = []
    seen = set()
    current_last_case = last_case_name

    # First, handle explicit case v. case citations
    for cit in citation_matches:
        cit_pos = cit.start()
        nearest_case = None
        min_dist = 200
        for case in case_matches:
            dist = cit_pos - case.start()
            if 0 <= dist < min_dist:
                nearest_case = re.sub(r"\s+", " ", case.group()).strip()
                min_dist = dist
        
        cit_text = re.sub(r"\s+", " ", cit.group()).strip().upper()
        
        final_case = nearest_case or current_last_case
        key = (final_case or "", cit_text)
        
        if key not in seen:
            seen.add(key)
            entry = {"citation": cit_text}
            if final_case:
                entry["case_name"] = final_case
                current_last_case = final_case
            linked.append(entry)

    # Handle short-form references (ibid, id, supra)
    for label, pat in SHORT_FORM_PATTERNS.items():
        for m in pat.finditer(text):
            if current_last_case:
                entry = {
                    "citation": m.group().upper(),
                    "case_name": current_last_case,
                    "resolved_from": label
                }
                key = (current_last_case, entry["citation"])
                if key not in seen:
                    seen.add(key)
                    linked.append(entry)

    # Update last_case_name from case_matches if any exist in this text
    if case_matches:
        current_last_case = re.sub(r"\s+", " ", case_matches[-1].group()).strip()

    return linked, current_last_case

# ============================================================
# SECTION SPLITTING
# ============================================================

def split_sections(text: str, active: str = "PREAMBLE") -> tuple:
    parts = SECTION_PATTERN.split(text)

    if len(parts) < 3:
        if text.strip():
            return [{"section": active, "text": text.strip()}], active
        return [], active

    sections = []
    if parts[0].strip():
        sections.append({"section": active, "text": parts[0].strip()})

    for i in range(1, len(parts), 2):
        raw = parts[i].strip().upper()
        name = SECTION_ALIASES.get(raw, "REASONING")
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            sections.append({"section": name, "text": content})

    last = sections[-1]["section"] if sections else active
    return sections, last


def merge_sections(sections: list) -> list:
    merged = OrderedDict()
    for s in sections:
        name = s["section"]
        if name not in merged:
            merged[name] = s["text"]
        else:
            merged[name] += " " + s["text"]
    return [{"section": k, "text": v} for k, v in merged.items()]

# ============================================================
# RECURSIVE CHARACTER SPLITTER
# ============================================================

def recursive_split(text: str, max_tokens: int = 512, overlap: int = 50) -> list:
    """
    Splits at Judgment → Para → Sentence boundaries.
    """
    # 1. Split by Judgment/Order (often denoted by explicit markers or large breaks)
    # For a single document, we might already be within one, but let's check for sub-judgments
    judgments = re.split(r"(?i)\n(?:JUDGMENT|ORDER|DECREE)\n", text)
    
    final_chunks = []
    chunk_id = 0
    
    for j_text in judgments:
        # 2. Split by Paragraphs
        paragraphs = re.split(r"\n\s*\n", j_text)
        
        current_chunk_text = ""
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
                
            # If paragraph itself is too large, split by sentences
            if len(para.split()) > max_tokens:
                sentences = re.split(r"(?<=[.?!])\s+", para)
                for sent in sentences:
                    if len(current_chunk_text.split()) + len(sent.split()) > max_tokens:
                        if current_chunk_text:
                            final_chunks.append({
                                "chunk_id": chunk_id,
                                "text": current_chunk_text.strip(),
                                "token_count": len(current_chunk_text.split())
                            })
                            chunk_id += 1
                            # Handle overlap
                            words = current_chunk_text.split()
                            current_chunk_text = " ".join(words[-overlap:]) if overlap < len(words) else current_chunk_text
                        
                        # If a single sentence is still too long (rare in legal docs but possible)
                        if len(sent.split()) > max_tokens:
                            # Hard split by words
                            words = sent.split()
                            for i in range(0, len(words), max_tokens - overlap):
                                part = " ".join(words[i:i + max_tokens])
                                final_chunks.append({
                                    "chunk_id": chunk_id,
                                    "text": part.strip(),
                                    "token_count": len(part.split())
                                })
                                chunk_id += 1
                            current_chunk_text = ""
                        else:
                            current_chunk_text += " " + sent
                    else:
                        current_chunk_text += " " + sent
            else:
                if len(current_chunk_text.split()) + len(para.split()) > max_tokens:
                    if current_chunk_text:
                        final_chunks.append({
                            "chunk_id": chunk_id,
                            "text": current_chunk_text.strip(),
                            "token_count": len(current_chunk_text.split())
                        })
                        chunk_id += 1
                        words = current_chunk_text.split()
                        current_chunk_text = " ".join(words[-overlap:]) if overlap < len(words) else current_chunk_text
                    current_chunk_text += " " + para
                else:
                    current_chunk_text += " " + para
                    
        if current_chunk_text.strip():
            final_chunks.append({
                "chunk_id": chunk_id,
                "text": current_chunk_text.strip(),
                "token_count": len(current_chunk_text.split())
            })
            chunk_id += 1
            
    return final_chunks

def extract_para_numbers(text: str) -> list:
    para_re = re.compile(r"(?:¶\s*(\d+)|\bPara(?:graph)?\s+(\d+)\b|^\s*(\d+)\.\s|\[(\d+)\])", re.MULTILINE | re.IGNORECASE)
    found = []
    for m in para_re.findall(text):
        for num in m:
            if num:
                found.append(int(num))
    return sorted(set(found))


# ============================================================
# HASHING & MANIFEST
# ============================================================

def compute_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def get_manifest(output_folder: str) -> dict:
    manifest_path = str(config.MANIFEST_PATH) if config and getattr(config, "MANIFEST_PATH", None) else os.path.join(output_folder, "manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def update_manifest(output_folder: str, filename: str, file_hash: str):
    manifest_path = str(config.MANIFEST_PATH) if config and getattr(config, "MANIFEST_PATH", None) else os.path.join(output_folder, "manifest.json")
    manifest = get_manifest(output_folder)
    manifest[filename] = file_hash
    try:
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
    except Exception as e:
        log.error("Failed to update manifest: %s", e)

# ============================================================
# PAGE EXTRACTION (Robust OCR Fallback)
# ============================================================

def denoise_image(pil_img: Image) -> Image:
    """Apply OpenCV denoising to improve OCR accuracy."""
    try:
        # Convert PIL to OpenCV (numpy array)
        open_cv_image = np.array(pil_img.convert('RGB'))
        # Convert RGB to BGR 
        open_cv_image = open_cv_image[:, :, ::-1].copy()
        
        # Convert to grayscale
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)
        
        # Apply fastNlMeansDenoising
        denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        
        # Return as PIL Image
        return Image.fromarray(denoised)
    except Exception as e:
        log.warning("Denoising failed: %s", e)
        return pil_img


def extract_pages(pdf_path: str, ocr_lang: str = "eng") -> list:
    pages = []
    
    # 1. Try Unstructured hi_res strategy first if available
    if partition_pdf:
        try:
            log.info("Using Unstructured hi_res strategy for %s", pdf_path)
            elements = partition_pdf(
                filename=pdf_path,
                strategy="hi_res",
                infer_table_structure=True,
                model_name="yolox",
                ocr_languages=[ocr_lang]
            )
            
            # Group elements by page
            page_content = {}
            for el in elements:
                p_num = el.metadata.page_number or 1
                if p_num not in page_content:
                    page_content[p_num] = []
                page_content[p_num].append(str(el))
                
            for p_num in sorted(page_content.keys()):
                raw_text = "\n".join(page_content[p_num])
                cleaned = normalize_text(raw_text)
                pages.append({
                    "page_number": p_num,
                    "text": cleaned,
                    "raw_text": raw_text,
                    "page_hash": compute_hash(cleaned),
                })
            
            if pages:
                return pages
        except Exception as e:
            log.warning("Unstructured failed for %s, falling back to PyMuPDF: %s", pdf_path, e)

    # 2. Fallback to PyMuPDF + Tesseract with Denoising
    doc = fitz.open(pdf_path)
    try:
        for page_num, page in enumerate(doc):
            raw_text = ""
            try:
                raw_text = page.get_text("text", sort=True)
            except Exception:
                raw_text = page.get_text("text")

            # OCR fallback: try 150 DPI first, then 300
            if len(raw_text.strip()) < 50:
                for dpi in (150, 300):
                    try:
                        pix = page.get_pixmap(dpi=dpi)
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        
                        # Apply OCR image denoising
                        denoised_img = denoise_image(img)
                        
                        raw_text = pytesseract.image_to_string(
                            denoised_img, lang=ocr_lang, config="--oem 3 --psm 6"
                        )
                        if len(raw_text.strip()) >= 50:
                            break
                    except Exception as e:
                        log.warning("OCR failed %s p%d @%ddpi: %s", pdf_path, page_num+1, dpi, e)

            # Embedded image OCR
            try:
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    try:
                        base_img = doc.extract_image(xref)
                        pil_img = Image.open(io.BytesIO(base_img["image"]))
                        
                        # Denoise before OCR
                        denoised_pil = denoise_image(pil_img)
                        
                        ocr_out = pytesseract.image_to_string(denoised_pil, lang=ocr_lang)
                        if ocr_out.strip():
                            raw_text += "\n" + ocr_out.strip()
                    except Exception as e:
                        log.warning("Image OCR failed %s p%d xref%d: %s", pdf_path, page_num+1, xref, e)
            except Exception as e:
                log.warning("Image listing failed %s p%d: %s", pdf_path, page_num+1, e)

            cleaned = normalize_text(raw_text)

            pages.append({
                "page_number": page_num + 1,
                "text": cleaned,
                "raw_text": raw_text,
                "page_hash": compute_hash(cleaned),
            })
    except Exception as e:
        log.error("Fatal page extraction error %s: %s", pdf_path, e)
    finally:
        doc.close()
    return pages

# ============================================================
# PDF PROCESSOR (per-file worker)
# ============================================================

def preprocess_pdf(args: tuple) -> dict:
    pdf_path, output_folder, ocr_lang = args
    start_time = time.time()
    filename = os.path.basename(pdf_path)
    result = {"file": filename, "status": "success", "error": None}

    try:
        # 1. Check MD5 Manifest to skip unchanged files
        with open(pdf_path, "rb") as f:
            file_bytes = f.read()
            file_hash = hashlib.md5(file_bytes).hexdigest()
        
        base = os.path.splitext(filename)[0]
        json_name = re.sub(r"[^a-zA-Z0-9_]", "_", base) + ".json"
        json_path = os.path.join(output_folder, json_name)

        # An unchanged hash is only a reason to skip if the OUTPUT actually exists.
        # The manifest is global (config.MANIFEST_PATH), so a document processed
        # into some earlier output folder marks itself "unchanged" for every later
        # run — and the file is then silently dropped from the new corpus with no
        # error and no entry in the failure tally. That is how
        # University_Of_Kerala went missing while the run reported success.
        manifest = get_manifest(output_folder)
        if manifest.get(filename) == file_hash and os.path.exists(json_path):
            log.info("SKIPPING %s (unchanged, output present)", filename)
            result["status"] = "skipped"
            return result
        if manifest.get(filename) == file_hash:
            log.info("REPROCESSING %s (hash unchanged but output missing)", filename)

        # Extract pages
        pages = extract_pages(pdf_path, ocr_lang)
        if not pages:
            result["status"] = "empty"
            log.warning("No pages extracted: %s", pdf_path)
            return result

        # Detect & strip repeating headers
        repeating = detect_repeating_headers(pages)
        if repeating:
            for p in pages:
                p["text"] = strip_repeating_headers(p["text"], repeating)

        # Full text and header text (first 3 pages)
        full_text = " ".join(p["text"] for p in pages)
        header_text = " ".join(p["text"] for p in pages[:3])

        # Metadata, classification, language
        metadata = extract_metadata(header_text, full_text)

        # Filename-based case_title fallback
        if "case_title" not in metadata:
            fn_match = re.match(r"(.+?)_vs_(.+?)_on_", base, re.I)
            if fn_match:
                pt1 = fn_match.group(1).replace("_", " ").strip()
                pt2 = fn_match.group(2).replace("_", " ").strip()
                metadata["case_title"] = f"{pt1} v. {pt2}"

        metadata["document_type"] = classify_document(full_text)
        language = detect_language(full_text)
        metadata["language"] = language

        # Process pages
        processed_pages = []
        active_section = "PREAMBLE"
        global_citations = []
        last_case_name = metadata.get("case_title")

        for page in pages:
            try:
                sections, active_section = split_sections(page["text"], active_section)
                page_sections = merge_sections(sections)
            except Exception as e:
                log.warning("Section split failed %s p%d: %s", filename, page["page_number"], e)
                page_sections = [{"section": "PREAMBLE", "text": page["text"]}]

            section_data = []
            for sec in page_sections:
                text = sec["text"].strip()
                if len(text.split()) < 5:
                    continue

                # Guard: metadata section overflow
                sec_name = sec["section"]
                if sec_name == "CASE_METADATA" and len(text) > 300:
                    sec_name = "REASONING"

                # Enhanced citation extraction with id., supra, ibid. resolution
                case_cits, last_case_name = extract_case_citations(text, last_case_name)
                global_citations.extend(case_cits)

                section_data.append({
                    "section": sec_name,
                    "text": text,
                    "entities": extract_entities(text),
                    "citations": extract_citations(text),
                    "case_citations": case_cits,
                    "para_numbers": extract_para_numbers(text),
                    "chunks": recursive_split(text),
                })

            processed_pages.append({
                "page_number": page["page_number"],
                "page_hash": page["page_hash"],
                "sections": section_data,
            })

        # Deduplicate global citations
        seen = set()
        unique_global = []
        for c in global_citations:
            key = (c.get("case_name", ""), c["citation"])
            if key not in seen:
                seen.add(key)
                unique_global.append(c)

        output = {
            "file_name": filename,
            "metadata": metadata,
            "document_hash": file_hash,
            "total_pages": len(pages),
            "pages": processed_pages,
            "global_citations": unique_global,
        }

        output_path = os.path.join(output_folder, json_name)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        # Update manifest
        update_manifest(output_folder, filename, file_hash)

        elapsed = round(time.time() - start_time, 2)
        log.info(
            "OK %s | %d pages | %d sections | %d citations | %.2fs",
            filename, len(pages),
            sum(len(p["sections"]) for p in processed_pages),
            len(unique_global), elapsed,
        )
        result["pages"] = len(pages)
        result["time"] = elapsed

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        log.error("FAILED %s: %s", pdf_path, e)

    return result

# ============================================================
# CLASS-BASED INTERFACE (for Watchdog/Other Services)
# ============================================================

class Preprocessor:
    def __init__(self, input_folder=None, output_folder=None, ocr_lang="eng", workers=None):
        if input_folder:
            self.input_folder = input_folder
        elif config:
            self.input_folder = str(config.WATCH_FOLDER)
        else:
            self.input_folder = "Dataset"

        if output_folder:
            self.output_folder = output_folder
        elif config:
            self.output_folder = str(config.PROCESSED_JSON_FOLDER)
        else:
            self.output_folder = os.path.join(self.input_folder, "processed_json")

        self.ocr_lang = ocr_lang
        self.workers = workers or cpu_count()
        os.makedirs(self.output_folder, exist_ok=True)

    def process_all(self):
        pdf_files = sorted([
            os.path.join(self.input_folder, f)
            for f in os.listdir(self.input_folder)
            if f.lower().endswith(".pdf")
        ])

        if not pdf_files:
            log.warning("No PDF files found in %s", self.input_folder)
            return {"status": "empty", "count": 0}

        log.info("Starting batch: %d PDFs, %d workers, OCR=%s", len(pdf_files), self.workers, self.ocr_lang)
        args_list = [(f, self.output_folder, self.ocr_lang) for f in pdf_files]

        results = []
        with Pool(self.workers, maxtasksperchild=10) as pool:
            for res in tqdm(pool.imap(preprocess_pdf, args_list), total=len(args_list), desc="Processing"):
                results.append(res)

        success = sum(1 for r in results if r["status"] == "success")
        errors = sum(1 for r in results if r["status"] == "error")
        
        summary = f"Complete: {success} OK | {errors} failed | Total: {len(results)}"
        log.info(summary)
        return {"status": "done", "success": success, "errors": errors, "total": len(results)}

# ============================================================
# CLI RUNNER
# ============================================================

def run_preprocessing():
    parser = argparse.ArgumentParser(
        description="Production-grade legal document preprocessor"
    )
    parser.add_argument("--input", required=True, help="Folder containing PDFs")
    parser.add_argument("--output", default=None, help="Output folder for JSON")
    parser.add_argument("--ocr-lang", default="eng", help="Tesseract language pack (default: eng)")
    parser.add_argument("--workers", type=int, default=None, help="Number of workers (default: cpu_count)")
    args = parser.parse_args()

    INPUT_FOLDER = args.input
    OUTPUT_FOLDER = args.output or os.path.join(INPUT_FOLDER, "processed_json")
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    ocr_lang = args.ocr_lang
    workers = args.workers or cpu_count()

    pdf_files = sorted([
        os.path.join(INPUT_FOLDER, f)
        for f in os.listdir(INPUT_FOLDER)
        if f.lower().endswith(".pdf")
    ])

    if not pdf_files:
        print("No PDF files found in", INPUT_FOLDER)
        return

    print(f"Found {len(pdf_files)} PDFs. Processing with {workers} workers...")
    log.info("Starting batch: %d PDFs, %d workers, OCR=%s", len(pdf_files), workers, ocr_lang)

    args_list = [(f, OUTPUT_FOLDER, ocr_lang) for f in pdf_files]

    results = []
    with Pool(workers, maxtasksperchild=10) as pool:
        for res in tqdm(pool.imap(preprocess_pdf, args_list), total=len(args_list), desc="Processing"):
            results.append(res)

    # Summary
    success = sum(1 for r in results if r["status"] == "success")
    errors = sum(1 for r in results if r["status"] == "error")
    empty = sum(1 for r in results if r["status"] == "empty")

    summary = f"\nComplete: {success} OK | {errors} failed | {empty} empty | Total: {len(results)}"
    print(summary)
    print("Saved to:", OUTPUT_FOLDER)
    log.info(summary.strip())

    if errors:
        print("\nFailed files:")
        for r in results:
            if r["status"] == "error":
                print(f"  ✗ {r['file']}: {r['error']}")


if __name__ == "__main__":
    run_preprocessing()