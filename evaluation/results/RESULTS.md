# Retrieval Experiment Results

Generated from `retrieval_experiments.json` — run at 2026-08-19T13:31:17+0530.

All values are measured. No number in this file is estimated or hand-entered.

## Corpus

| Property | Value |
| --- | --- |
| IPC sections parsed | 523 |
| BNS sections parsed | 355 |
| chunks — fixed_window | 786 |
| chunks — section_split | 1,144 |
| chunks — section_whole | 878 |
| test queries (total) | 43 |
| test queries (scored for ranking) | 38 |
| test queries (abstention only) | 5 |
| gold labels | 57 |

## Embeddings in ChromaDB

| Collection | Chunks | Embeddings | Dim | Build time |
| --- | ---: | ---: | ---: | ---: |
| `exp_statutes_fixed_window_bgebase` | 786 | 786 | 768 | 80.3s |
| `exp_statutes_fixed_window_bgem3` | 786 | 786 | 1024 | 213.6s |
| `exp_statutes_section_split_bgebase` | 1,144 | 1,144 | 768 | 65.7s |
| `exp_statutes_section_split_bgem3` | 1,144 | 1,144 | 1024 | 154.5s |
| `exp_statutes_section_whole_bgebase` | 878 | 878 | 768 | 48.7s |
| `exp_statutes_section_whole_bgem3` | 878 | 878 | 1024 | 145.7s |

## Retrieval configurations

Precision@5 ceiling for this test set: **0.300** (most queries have a single gold section, so P@5 is capped at |gold|/5).

| Cfg | Configuration | Model | Chunking | P@5 | P@5 / ceiling | R@5 | MRR | nDCG@5 | Hit@5 | p50 ms | p95 ms |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | Baseline (dense, fixed-window chunks) | bge-base | fixed_window | 0.089 | 0.298 | 0.346 | 0.310 | 0.283 | 0.421 | 37.0 | 253.2 |
| B | Dense + section chunking | bge-base | section_whole | 0.179 | 0.596 | 0.662 | 0.602 | 0.565 | 0.789 | 34.4 | 45.5 |
| B2 | Dense + section-split chunking | bge-base | section_split | 0.174 | 0.579 | 0.653 | 0.602 | 0.563 | 0.789 | 34.4 | 41.6 |
| C | Hybrid BM25 + dense (RRF) | bge-base | section_whole | 0.168 | 0.561 | 0.640 | 0.630 | 0.568 | 0.763 | 37.0 | 42.7 |
| D | Hybrid + cross-encoder rerank | bge-base | section_whole | 0.184 | 0.614 | 0.697 | 0.674 | 0.628 | 0.842 | 630.7 | 926.2 |
| A | Baseline (dense, fixed-window chunks) | bge-m3 | fixed_window | 0.100 | 0.333 | 0.364 | 0.309 | 0.283 | 0.474 | 65.4 | 481.5 |
| B | Dense + section chunking | bge-m3 | section_whole | 0.210 | 0.702 | 0.763 | 0.633 | 0.612 | 0.868 | 63.8 | 88.5 |
| B2 | Dense + section-split chunking | bge-m3 | section_split | 0.184 | 0.614 | 0.667 | 0.609 | 0.565 | 0.789 | 62.4 | 82.6 |
| C | Hybrid BM25 + dense (RRF) | bge-m3 | section_whole | 0.190 | 0.632 | 0.702 | 0.666 | 0.610 | 0.816 | 64.9 | 83.0 |
| D | Hybrid + cross-encoder rerank | bge-m3 | section_whole | 0.179 | 0.596 | 0.697 | 0.669 | 0.624 | 0.842 | 626.8 | 753.6 |

## Metrics at every K

### A — Baseline (dense, fixed-window chunks) (bge-base)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.210 | 0.197 | 0.210 | 0.210 |
| 3 | 0.114 | 0.268 | 0.342 | 0.248 |
| 5 | 0.089 | 0.346 | 0.421 | 0.283 |
| 10 | 0.060 | 0.456 | 0.553 | 0.322 |

MRR: **0.310**

### B — Dense + section chunking (bge-base)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.447 | 0.390 | 0.447 | 0.447 |
| 3 | 0.237 | 0.557 | 0.684 | 0.514 |
| 5 | 0.179 | 0.662 | 0.789 | 0.565 |
| 10 | 0.116 | 0.803 | 0.895 | 0.619 |

MRR: **0.602**

### B2 — Dense + section-split chunking (bge-base)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.447 | 0.404 | 0.447 | 0.447 |
| 3 | 0.263 | 0.605 | 0.763 | 0.541 |
| 5 | 0.174 | 0.653 | 0.789 | 0.563 |
| 10 | 0.111 | 0.767 | 0.868 | 0.609 |

MRR: **0.602**

### C — Hybrid BM25 + dense (RRF) (bge-base)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.526 | 0.421 | 0.526 | 0.526 |
| 3 | 0.246 | 0.588 | 0.711 | 0.541 |
| 5 | 0.168 | 0.640 | 0.763 | 0.568 |
| 10 | 0.105 | 0.737 | 0.816 | 0.607 |

MRR: **0.630**

### D — Hybrid + cross-encoder rerank (bge-base)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.553 | 0.478 | 0.553 | 0.553 |
| 3 | 0.263 | 0.636 | 0.763 | 0.596 |
| 5 | 0.184 | 0.697 | 0.842 | 0.628 |
| 10 | 0.113 | 0.811 | 0.895 | 0.671 |

MRR: **0.674**

### A — Baseline (dense, fixed-window chunks) (bge-m3)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.184 | 0.158 | 0.184 | 0.184 |
| 3 | 0.132 | 0.307 | 0.395 | 0.256 |
| 5 | 0.100 | 0.364 | 0.474 | 0.283 |
| 10 | 0.053 | 0.390 | 0.500 | 0.292 |

MRR: **0.309**

### B — Dense + section chunking (bge-m3)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.447 | 0.355 | 0.447 | 0.447 |
| 3 | 0.298 | 0.653 | 0.789 | 0.564 |
| 5 | 0.210 | 0.763 | 0.868 | 0.612 |
| 10 | 0.124 | 0.855 | 0.921 | 0.649 |

MRR: **0.633**

### B2 — Dense + section-split chunking (bge-m3)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.421 | 0.342 | 0.421 | 0.421 |
| 3 | 0.289 | 0.640 | 0.789 | 0.551 |
| 5 | 0.184 | 0.667 | 0.789 | 0.565 |
| 10 | 0.116 | 0.807 | 0.895 | 0.619 |

MRR: **0.609**

### C — Hybrid BM25 + dense (RRF) (bge-m3)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.553 | 0.434 | 0.553 | 0.553 |
| 3 | 0.254 | 0.579 | 0.737 | 0.555 |
| 5 | 0.190 | 0.702 | 0.816 | 0.610 |
| 10 | 0.116 | 0.794 | 0.842 | 0.648 |

MRR: **0.666**

### D — Hybrid + cross-encoder rerank (bge-m3)

| K | Precision@K | Recall@K | Hit rate@K | nDCG@K |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.553 | 0.478 | 0.553 | 0.553 |
| 3 | 0.254 | 0.623 | 0.737 | 0.587 |
| 5 | 0.179 | 0.697 | 0.842 | 0.624 |
| 10 | 0.113 | 0.811 | 0.895 | 0.670 |

MRR: **0.669**

## Hit rate@5 by query category

| Cfg | Model | ambiguous | direct_section | ipc_bns_mapping | multi_section | natural_language | paraphrase |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | bge-base | 0.500 | 0.200 | 0.500 | 0.500 | 0.400 | 0.667 |
| B | bge-base | 1.000 | 0.900 | 1.000 | 1.000 | 0.500 | 0.667 |
| B2 | bge-base | 1.000 | 0.900 | 1.000 | 1.000 | 0.500 | 0.667 |
| C | bge-base | 0.750 | 0.900 | 0.750 | 1.000 | 0.400 | 1.000 |
| D | bge-base | 1.000 | 0.900 | 0.750 | 1.000 | 0.600 | 1.000 |
| A | bge-m3 | 0.750 | 0.200 | 0.750 | 0.750 | 0.300 | 0.667 |
| B | bge-m3 | 1.000 | 0.800 | 1.000 | 1.000 | 0.700 | 1.000 |
| B2 | bge-m3 | 1.000 | 0.500 | 1.000 | 1.000 | 0.700 | 1.000 |
| C | bge-m3 | 1.000 | 0.800 | 1.000 | 1.000 | 0.500 | 1.000 |
| D | bge-m3 | 1.000 | 1.000 | 0.750 | 1.000 | 0.500 | 1.000 |

## Retrieval latency

End-to-end per query: query embedding + vector search + BM25 + fusion + reranking. Measured on the host that ran the experiment; no warm-up runs were discarded.

| Cfg | Model | mean | p50 | p95 | min | max |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| A | bge-base | 616.7 | 37.0 | 253.2 | 20.9 | 23728.8 |
| B | bge-base | 35.7 | 34.4 | 45.5 | 28.9 | 47.9 |
| B2 | bge-base | 35.4 | 34.4 | 41.6 | 28.0 | 65.8 |
| C | bge-base | 36.9 | 37.0 | 42.7 | 25.2 | 45.9 |
| D | bge-base | 944.6 | 630.7 | 926.2 | 388.8 | 13167.2 |
| A | bge-m3 | 503.0 | 65.4 | 481.5 | 38.7 | 16453.2 |
| B | bge-m3 | 64.4 | 63.8 | 88.5 | 42.0 | 102.0 |
| B2 | bge-m3 | 64.4 | 62.4 | 82.6 | 45.3 | 88.7 |
| C | bge-m3 | 67.2 | 64.9 | 83.0 | 51.9 | 95.2 |
| D | bge-m3 | 632.6 | 626.8 | 753.6 | 546.6 | 786.8 |

## Abstention probe (out-of-corpus queries)

These five queries have no answer anywhere in the corpus. A retrieval score close to the in-corpus range means similarity alone cannot be used as an abstention signal — the generator has to be told to abstain.

| Cfg | Query | Top dense similarity |
| --- | --- | ---: |
| A | What are the filing requirements for a GST return? | 0.5677 |
| A | How do I apply for an Indian passport? | 0.5107 |
| A | What is the procedure for registering a trademark in India | 0.5685 |
| A | What is the capital gains tax rate on equity shares? | 0.4698 |
| A | Write me a Python function to reverse a linked list | 0.4124 |
| B | What are the filing requirements for a GST return? | 0.5370 |
| B | How do I apply for an Indian passport? | 0.5427 |
| B | What is the procedure for registering a trademark in India | 0.5824 |
| B | What is the capital gains tax rate on equity shares? | 0.4721 |
| B | Write me a Python function to reverse a linked list | 0.4351 |
| B2 | What are the filing requirements for a GST return? | 0.5434 |
| B2 | How do I apply for an Indian passport? | 0.5427 |
| B2 | What is the procedure for registering a trademark in India | 0.5824 |
| B2 | What is the capital gains tax rate on equity shares? | 0.4791 |
| B2 | Write me a Python function to reverse a linked list | 0.4351 |
| C | What are the filing requirements for a GST return? | 0.5370 |
| C | How do I apply for an Indian passport? | 0.5427 |
| C | What is the procedure for registering a trademark in India | 0.5824 |
| C | What is the capital gains tax rate on equity shares? | 0.4721 |
| C | Write me a Python function to reverse a linked list | 0.4351 |
| D | What are the filing requirements for a GST return? | 0.5370 |
| D | How do I apply for an Indian passport? | 0.5427 |
| D | What is the procedure for registering a trademark in India | 0.5824 |
| D | What is the capital gains tax rate on equity shares? | 0.4721 |
| D | Write me a Python function to reverse a linked list | 0.4351 |
| A | What are the filing requirements for a GST return? | 0.4433 |
| A | How do I apply for an Indian passport? | 0.4926 |
| A | What is the procedure for registering a trademark in India | 0.4993 |
| A | What is the capital gains tax rate on equity shares? | 0.3841 |
| A | Write me a Python function to reverse a linked list | 0.4349 |
| B | What are the filing requirements for a GST return? | 0.4259 |
| B | How do I apply for an Indian passport? | 0.4657 |
| B | What is the procedure for registering a trademark in India | 0.4496 |
| B | What is the capital gains tax rate on equity shares? | 0.3776 |
| B | Write me a Python function to reverse a linked list | 0.4230 |
| B2 | What are the filing requirements for a GST return? | 0.4339 |
| B2 | How do I apply for an Indian passport? | 0.4748 |
| B2 | What is the procedure for registering a trademark in India | 0.4582 |
| B2 | What is the capital gains tax rate on equity shares? | 0.3776 |
| B2 | Write me a Python function to reverse a linked list | 0.4437 |
| C | What are the filing requirements for a GST return? | 0.4259 |
| C | How do I apply for an Indian passport? | 0.4657 |
| C | What is the procedure for registering a trademark in India | 0.4496 |
| C | What is the capital gains tax rate on equity shares? | 0.3776 |
| C | Write me a Python function to reverse a linked list | 0.4230 |
| D | What are the filing requirements for a GST return? | 0.4259 |
| D | How do I apply for an Indian passport? | 0.4657 |
| D | What is the procedure for registering a trademark in India | 0.4496 |
| D | What is the capital gains tax rate on equity shares? | 0.3776 |
| D | Write me a Python function to reverse a linked list | 0.4230 |

