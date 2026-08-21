"""
config.py — Central configuration for Legal RAG System
All modules import from here; change once and it propagates everywhere.
"""

import os
from pathlib import Path
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional import guard
    load_dotenv = None

# ─── Project Paths ────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load environment variables from project root .env for local development.
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

# ─── Incremental Indexing ───────────────────────────────────────────────────
MANIFEST_PATH           = OUTPUT_DIR / "manifest.json"
WATCH_FOLDER            = BASE_DIR / "Dataset"
PROCESSED_JSON_FOLDER   = BASE_DIR / "backend/processed_json"

# ─── Vector Store (Chroma & FAISS fallback) ─────────────────────────────────
VECTOR_STORE            = "chroma"          # "chroma" | "faiss"
CHROMA_PERSIST_PATH     = OUTPUT_DIR / "chroma_db"
CHROMA_COLLECTION_ALL   = "legal_chunks"
CHROMA_COLLECTION_RATIO = "legal_ratio"
CHROMA_COLLECTION_FACTS = "legal_facts"
CHROMA_COLLECTION_CITS  = "legal_citations"
FAISS_INDEX_PATH        = OUTPUT_DIR / "vector_store.faiss"

# ─── Legacy/Other Indices ────────────────────────────────────────────────────
BM25_INDEX_PATH  = OUTPUT_DIR / "bm25.pkl"
GRAPH_PATH       = OUTPUT_DIR / "knowledge_graph.gpickle"
EVAL_LOG_PATH    = OUTPUT_DIR / "eval_log.jsonl"

# ═════════════════════════════════════════════════════════════════════════════
# PRODUCTION STATUTE PIPELINE
# ═════════════════════════════════════════════════════════════════════════════
# This configuration was selected by measurement, not preference. See
# evaluation/results/RESULTS.md. On the 43-query evaluation set, holding
# everything else constant:
#
#   chunking   section_whole beat fixed_window   MRR 0.310 -> 0.602  (+94%)
#              section_whole beat section_split  R@5 0.763 vs 0.667
#   model      bge-m3 beat bge-base              R@5 0.763 vs 0.662  (+15%)
#   retrieval  dense-only is the production default: hybrid+rerank scored higher
#              MRR (0.669 vs 0.633) but LOWER Recall@5 (0.697 vs 0.763) and cost
#              ~10x latency (627ms vs 64ms p50). Hybrid and reranking remain
#              implemented and reproducible via the evaluation harness.
#
# The collection name below encodes the model, because a Chroma collection is
# only valid for the embedding dimensionality it was built with. bge-base is 768d
# and bge-m3 is 1024d; querying one with the other's vectors is the exact failure
# this project started with (config declared bge-m3 while the index on disk was
# 768d, so vector search silently returned nothing).
STATUTE_COLLECTION      = "prod_statutes_section_whole_bgem3"
STATUTE_CHUNK_STRATEGY  = "section_whole"
STATUTE_EMBED_MODEL_KEY = "bge-m3"

# Judgments live in a separate collection from statutes. They have a different
# metadata shape (case name, court, citation) and a different chunking regime
# (overlapping passages rather than whole sections). Mixing them would let a
# statute query return judicial prose as though it were the provision's own text,
# and would invalidate the statute retrieval experiments already recorded in
# evaluation/results/.
JUDGMENT_COLLECTION     = "prod_judgments_sc_bgem3"
JUDGMENT_TOP_K          = 5
JUDGMENT_CANDIDATE_K    = 15

# ─── Embedding Model ─────────────────────────────────────────────────────────
EMBEDDING_MODEL   = "BAAI/bge-m3" # Multilingual (100+ languages incl. HI, KN, TA)
EMBEDDING_DIM     = 1024          # bge-m3 output dimension is 1024
EMBEDDING_BATCH   = 8             # bge-m3 is 568M params; 32 OOMs the MPS backend
EMBEDDING_MAX_SEQ = 512           # pinned so model comparisons stay controlled
# bge-m3 is trained WITHOUT an instruction prefix — unlike bge-base/bge-small,
# where the prefix is required. Applying the bge-base prefix to bge-m3 degrades it.
BGE_QUERY_PREFIX  = ""
FAISS_USE_SQ8     = True         # SQ8 quantization (~4x memory savings)

# ─── Cross-Encoder Reranker ──────────────────────────────────────────────────
RERANKER_MODEL    = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANKER_TOP_K    = 5            # final results after reranking

# ─── Production statute retrieval ────────────────────────────────────────────
STATUTE_TOP_K          = 5     # sections passed to the LLM as context
STATUTE_CANDIDATE_K    = 15    # candidates fetched before filtering/abstention

# Abstention signals. These are NOT a trained classifier and must not be
# presented as one. The separation measured in evaluation/results/abstention.json
# was 0.491 (lowest in-corpus) vs 0.466 (highest out-of-corpus) — a margin of
# 0.026 observed over only 5 out-of-corpus queries. That is suggestive, not
# established. The thresholds below are therefore set conservatively and combined
# with other signals rather than used as a single cut-off; see
# backend/services/abstention.py.
ABSTAIN_SIM_HARD       = 0.40   # below this, nothing plausible was retrieved
# Set from the measured boundary over 18 unanswerable probes and 38 answerable
# queries: highest unanswerable peak was 0.4805, lowest answerable peak 0.4915.
# 0.486 sits between them. This IS fitted to the observed data — there is no
# held-out set — so it is a working default, not a validated operating point.
ABSTAIN_SIM_SOFT       = 0.486
ABSTAIN_MIN_SUPPORT    = 1      # at least one candidate must clear the soft line
# Retained for reporting only. Measured over the same data, the top1-top3 spread
# did NOT separate answerable from unanswerable queries (answerable min 0.0011 vs
# unanswerable max 0.0298), so it is no longer used in the decision.
ABSTAIN_MARGIN_MIN     = 0.02

# ─── Retrieval Settings & Weights ────────────────────────────────────────────
FAISS_TOP_K       = 20
BM25_TOP_K        = 20
GRAPH_TOP_K       = 10
HYBRID_TOP_K      = 40
RRF_K             = 60

WEIGHT_CHROMA_ALL       = 0.35
WEIGHT_CHROMA_RATIO     = 0.20
WEIGHT_CHROMA_FACTS     = 0.10
WEIGHT_CHROMA_CITS      = 0.10
WEIGHT_BM25             = 0.15
WEIGHT_GRAPH            = 0.10

# Maintain compatibility with v1
WEIGHT_FAISS  = 0.50

# ─── Court Hierarchy Multipliers (Jurisdiction Logic) ────────────────────────
COURT_MULTIPLIERS = {
    "SUPREME COURT OF INDIA": 1.25,
    "SUPREME COURT": 1.25, "SC": 1.25,
    "CONSTITUTIONAL BENCH": 1.20,
    "FULL BENCH": 1.15, "LARGER BENCH": 1.15,
    "DIVISION BENCH": 1.10,
    "HIGH COURT": 1.05, "HC": 1.05,
}

# ─── Feature Flags (The Ultimate Upgrades) ───────────────────────────────────
HYDE_ENABLED            = True     # LLM expansion of queries
SELF_RAG_ENABLED        = True     # LLM Critic hallucination detection
QUERY_DECOMPOSE_ENABLED = True     # Semantic split of complex queries
PARENT_DOC_ENABLED      = True     # Search child (256), return parent (512)
RATE_LIMITING_ENABLED   = False    # Scalability API limits (Disabled for speed)
CACHE_ENABLED           = True     # Redis/DiskCache

RATIO_SECTION_BOOST     = 1.15     # Score boost for RATIO/KEY_POINTS
CITATION_WINDOW         = 5        # Rolling window for id./supra resolver

# ─── Chunking Boundaries ─────────────────────────────────────────────────────
CHILD_CHUNK_TOKENS      = 256      # Small chunk used for vector matching
PARENT_CHUNK_TOKENS     = 512      # Parent retrieved for LLM context
CHUNK_OVERLAP           = 50

# ─── Caching & Services ──────────────────────────────────────────────────────
REDIS_URL               = os.getenv("REDIS_URL", "redis://localhost:6379")
CACHE_TTL_HOURS         = 24
CACHE_DIR               = OUTPUT_DIR / "query_cache"
GROQ_STREAM             = True     # Enable SSE yielding

# ─── LLM / Query Engine ──────────────────────────────────────────────────────
LLM_BACKEND      = os.getenv("RAG_LLM_BACKEND", "groq")  # groq | openai | gemini | ollama
# Secrets are read from the environment only. Never hardcode a default here:
# config.py is tracked in git, so any literal placed below is a published secret.
# Copy .env.example to .env and set GROQ_API_KEY there (.env is gitignored).
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL       = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODELS = [
    m.strip() for m in os.getenv(
        "GROQ_FALLBACK_MODELS",
        "llama-3.3-70b-versatile,llama-3.1-8b-instant"
    ).split(",") if m.strip()
]
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL     = "gpt-4o-mini"
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = "gemini-1.5-flash"
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

MAX_CONTEXT_CHUNKS  = 5
MAX_CONTEXT_TOKENS  = 3000

# ─── Logging / Evaluation ────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
