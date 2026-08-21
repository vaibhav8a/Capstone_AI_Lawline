# Evaluation Methodology

How retrieval quality is measured in this project, what the numbers mean, and
what they do **not** support claiming.

Every figure in `evaluation/results/` is produced by running the scripts in
`evaluation/`. No metric anywhere in this repository is hand-entered.

---

## 1. The evaluation set

**File:** `evaluation/test_queries.json` (and `.csv`)
**Built by:** `evaluation/build_test_queries.py`

43 queries, of which 38 are scored for ranking metrics and 5 are abstention-only.
57 gold labels in total.

| Category | Count | What it tests |
| --- | ---: | --- |
| `direct_section` | 10 | Query names the section number outright |
| `natural_language` | 10 | Offence described in lay terms, no statutory vocabulary |
| `paraphrase` | 6 | Provision restated without its statutory wording |
| `ambiguous` | 4 | Under-specified; several sections defensible |
| `multi_section` | 4 | Genuinely requires more than one section |
| `ipc_bns_mapping` | 4 | Spans the IPC → BNS transition |
| `out_of_corpus` | 5 | Answerable by no section in the corpus |

### Provenance of the labels — read this before citing any number

This set is **author-constructed**, not expert-annotated and not a community
benchmark. The same person wrote the queries and assigned the gold sections.

What that supports: comparing retrieval configurations against each other on a
fixed, reproducible task. Configuration A and configuration D face identical
queries and identical labels, so a difference between them is a real difference
in retrieval behaviour.

What it does **not** support: any claim about absolute accuracy on Indian legal
QA in general, or any comparison against numbers published elsewhere.

### The one guard that is enforced

`build_test_queries.py` **fails and refuses to write the test set** if any gold
label names a section that is absent from the parsed corpus. This is not
decoration — it caught a real error during construction: a query about a public
servant accepting a bribe was labelled `IPC s.161`, but ss.161–165A were repealed
from the IPC by the Prevention of Corruption Act, 1988 and are correctly absent
from the corpus. An unreachable gold label silently depresses recall for every
configuration equally, which looks like a uniformly weak system rather than a
broken label.

---

## 2. Scoring unit: sections, not chunks

All metrics are computed over **statutory sections**, not chunks.

The chunking strategies under comparison emit different numbers of chunks per
section — `section_whole` emits exactly one, `fixed_window` can emit several
windows covering the same provision. Scoring chunks directly would let a strategy
inflate precision purely by cutting the same section into more pieces.

Each ranked chunk list is therefore collapsed to a ranked list of unique
`(document, section)` pairs, first occurrence winning, before any metric is
computed (`metrics_ir.dedupe_sections`). "Did it find IPC s.420" is also the
question a user actually asks.

---

## 3. Metrics

Binary relevance throughout. Implemented in `evaluation/metrics_ir.py`,
unit-tested against hand-computed values in `tests/test_metrics_ir.py`.

| Metric | Definition |
| --- | --- |
| Precision@K | relevant in top-K ÷ min(K, results returned) |
| Recall@K | relevant in top-K ÷ total gold sections |
| Hit rate@K | 1 if any gold section appears in top-K, else 0 |
| MRR | 1 ÷ rank of the first relevant section |
| nDCG@K | binary-relevance DCG with 1/log₂(rank+1) discount, over ideal DCG |

Two deliberate choices:

* **Precision@K divides by `min(K, len(results))`,** not by K. A system returning
  three results is not penalised for two empty slots it never claimed to fill.
* **Aggregation is macro-average** — every query counts equally, regardless of how
  many gold sections it has.

### The Precision@K ceiling

Most queries in this set have exactly one gold section. Precision@5 for such a
query is capped at 1/5 = 0.2 no matter how good the system is.

The macro-averaged **P@5 ceiling for this test set is 0.300**. A raw P@5 of 0.184
therefore represents 61% of the attainable maximum, not a 18% success rate.
`summarize_results.py` computes this ceiling from the test set and reports P@5
normalised by it alongside the raw value.

**Recall@5, MRR and nDCG@5 are the metrics to read.** They are not distorted by
the gold-set size and are directly comparable across configurations.

### Out-of-corpus queries

The 5 `out_of_corpus` queries have empty gold sets. Precision, recall, MRR and
nDCG are all undefined with no relevant document, so these queries are **excluded
from every ranking metric** and reported separately: the run records the top dense
similarity score, which is the signal an abstention mechanism would have to
threshold on.

---

## 4. Configurations

Each differs from its neighbour in exactly one dimension, so a difference in the
metrics is attributable to that dimension.

| Cfg | Chunking | Dense | BM25 | Reranker | Isolates |
| --- | --- | :-: | :-: | :-: | --- |
| A | `fixed_window` | ✓ | | | baseline |
| B | `section_whole` | ✓ | | | A→B: **chunking** |
| B2 | `section_split` | ✓ | | | B→B2: split vs whole |
| C | `section_whole` | ✓ | ✓ | | B→C: **hybrid retrieval** |
| D | `section_whole` | ✓ | ✓ | ✓ | C→D: **reranking** |

The embedding-model comparison re-runs configuration **B** under each model,
holding chunking and retrieval constant.

Fixed parameters: candidate depth 50 per retriever, RRF k=60, rerank depth 20,
metrics at K ∈ {1, 3, 5, 10}.

---

## 5. Latency

Measured per query with `time.perf_counter()` around the full retrieval path:
query embedding → vector search → BM25 (where enabled) → RRF fusion → reranking
(where enabled). Reported as mean, p50, p95, min, max.

Caveats stated plainly:

* Model load time is excluded; the model is warm. Cold-start is separate.
* **No warm-up runs are discarded.** The first query in a run pays one-off costs,
  which is why p95 sits well above p50 in some configurations.
* Single-process, single-user, local machine (Apple Silicon, MPS backend). These
  are not throughput numbers under concurrent load.
* Embedding *build* time is recorded separately in `index_build_stats.json`.

---

## 6. Reproducibility

```bash
python -m backend.ingestion.fetch_statutes      # official PDFs + provenance
python -m backend.ingestion.parse_statutes      # sections
python -m backend.ingestion.chunk_statutes      # all three strategies
python -m backend.ingestion.build_index --all   # embeddings into ChromaDB
python evaluation/build_test_queries.py         # test set (validates gold labels)
python -m evaluation.evaluate_retrieval --configs all
python evaluation/summarize_results.py          # results tables
```

### Determinism — measured, not assumed

Re-running an identical configuration reproduces metrics to roughly **three
decimal places**, not bit-exactly. A repeat of configuration B gave MRR 0.6015 vs
0.6016.

The cause was isolated rather than guessed: encoding the same query twice on the
MPS backend returns **bit-identical** vectors (max abs difference 0.0), so the
variance comes from **ChromaDB's HNSW approximate nearest-neighbour search**, not
from the embedding model. This is expected behaviour for an ANN index.

Practical consequence: differences between configurations of less than ~0.005 on
any metric should not be treated as meaningful on this test set.

---

## 7. What this evaluation does not cover

* **Generation quality.** These are retrieval metrics only. Faithfulness,
  groundedness, citation correctness and abstention behaviour of the LLM require
  a separate evaluation with an API key configured.
* **Statistical significance.** With 38 scored queries, small differences are
  within noise. No significance testing has been performed, and none is claimed.
* **The judgment corpus.** Experiments run on the statute corpus, where ground
  truth is unambiguous. The case-law corpus has no reliable gold labels.
* **Corpus currency.** The IPC text is a 1997-vintage consolidation; see
  `docs/data_pipeline.md`. Retrieval is measured against the corpus as it exists,
  which is not the same as the law as it stood at repeal.
