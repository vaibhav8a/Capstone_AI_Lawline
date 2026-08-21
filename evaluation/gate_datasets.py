"""
gate_datasets.py — development and held-out sets for the retrieval gate.

Why this file exists
--------------------
The abstention thresholds in `config.py` were chosen by inspecting similarity
distributions over `test_queries.json` and `abstention_probes.json`. Scoring the
gate on those same queries therefore measures **fit**, not generalisation, and
the project has already been burned once by exactly this: an early result over 5
unanswerable queries showed clean separation, and adding near-domain probes
overturned it completely.

So the data is split explicitly:

    DEVELOPMENT   test_queries.json (38 answerable) + abstention_probes.json (18)
                  Thresholds were tuned against these. Numbers here are a fit.

    HELD-OUT      the queries below. Written after the thresholds were fixed and
                  NEVER used to adjust them. This is the only honest estimate of
                  gate behaviour on unseen input.

Discipline: if a held-out number is disappointing, it is reported as-is. Tuning
against it would destroy the only unbiased measurement in the project. Any future
threshold change must be made on the development set and then re-measured here.

Held-out composition
--------------------
    answerable        gold section verified present in the corpus at build time
    near_domain       valid Indian legal questions in areas the corpus does NOT
                      cover (family, labour, tax, company, IP, motor vehicle)
    far_domain        clearly outside law entirely
    adversarial       false section numbers, wrong statute attributions,
                      fabricated cases, IPC/BNS confusion
"""

from __future__ import annotations

# (query, expected_answerable) — expected_answerable=True means the corpus
# contains a section that answers it, so the gate should ALLOW generation.
HELD_OUT_ANSWERABLE: list[tuple[str, str]] = [
    # (query, a section that must exist in the corpus for the label to be valid)
    ("What does the Code say about criminal intimidation?", "503"),
    ("Which provision covers giving false evidence?", "191"),
    ("What is the provision on kidnapping from lawful guardianship?", "361"),
    ("Explain the offence of mischief", "425"),
    ("What does IPC Section 141 define?", "141"),
    ("Which section deals with wrongful restraint?", "339"),
    ("What is the punishment for house-trespass?", "448"),
    ("Which provision defines counterfeiting?", "28"),
    ("What does the Code say about abetment of suicide?", "306"),
    ("Which section covers defamation?", "499"),
    ("What is the offence of criminal misappropriation of property?", "403"),
    ("Explain what constitutes an unlawful assembly", "141"),
    ("Which provision deals with rioting armed with a deadly weapon?", "148"),
    ("What does the Code provide about forgery of a valuable security?", "467"),
    ("Which section addresses public nuisance?", "268"),
]

# Valid legal questions whose answers are NOT in an IPC/BNS corpus. These are the
# hard negatives: they share vocabulary with criminal statutes.
HELD_OUT_NEAR_DOMAIN: list[str] = [
    "What is the limitation period for filing a civil suit for recovery of money?",
    "How is maintenance calculated under Section 125 of the CrPC?",
    "What are the conditions for granting anticipatory bail?",
    "What is the procedure for filing a consumer complaint?",
    "How does one register a partnership firm in India?",
    "What are the rights of a tenant against eviction?",
    "What is the process for obtaining a succession certificate?",
    "How is stamp duty computed on a gift deed?",
    "What are the statutory dues payable under the Employees Provident Fund Act?",
    "What are the requirements for a valid will under Indian succession law?",
    "How long does copyright protection last in India?",
    "What compensation is payable under the Motor Vehicles Act for an accident?",
]

HELD_OUT_FAR_DOMAIN: list[str] = [
    "What is the boiling point of water at sea level?",
    "Write a SQL query to join two tables",
    "Who won the cricket world cup in 2011?",
    "How do I bake sourdough bread?",
    "What is the population of Bengaluru?",
    "Explain how a transformer neural network works",
]

# Deliberately malformed premises. The correct behaviour is to abstain or to
# answer only from what was retrieved — never to accept the false premise.
HELD_OUT_ADVERSARIAL: list[tuple[str, str]] = [
    ("What is the punishment under IPC Section 999?", "nonexistent section"),
    ("Explain IPC Section 888 on cyber fraud", "nonexistent section + invented subject"),
    ("What does BNS Section 1200 say?", "nonexistent section"),
    ("Which IPC section covers income tax evasion?", "wrong statute domain"),
    ("What did the Supreme Court hold in Sharma v. Union of India (2019) about IPC 420?",
     "fabricated case"),
    ("Since IPC Section 302 was renumbered to BNS Section 302, what changed?",
     "false premise: BNS murder provision is s.103, not s.302"),
    ("Under IPC Section 420, what is the mandatory minimum sentence of ten years?",
     "false premise: no mandatory minimum in s.420"),
    ("What does the Indian Penal Code say about GST evasion penalties?",
     "wrong statute domain"),
]


def held_out_records() -> list[dict]:
    """Flatten the held-out set into scoring records."""
    records: list[dict] = []
    for query, gold_section in HELD_OUT_ANSWERABLE:
        records.append({
            "query": query,
            "group": "answerable",
            "expected_allow": True,
            "gold_section": gold_section,
        })
    for query in HELD_OUT_NEAR_DOMAIN:
        records.append({"query": query, "group": "near_domain", "expected_allow": False})
    for query in HELD_OUT_FAR_DOMAIN:
        records.append({"query": query, "group": "far_domain", "expected_allow": False})
    for query, reason in HELD_OUT_ADVERSARIAL:
        records.append({
            "query": query,
            "group": "adversarial",
            "expected_allow": False,
            "reason": reason,
        })
    return records


def validate_gold_sections(corpus_sections: set[str]) -> list[str]:
    """Return labels whose gold section is missing from the corpus.

    An answerable query whose gold section does not exist is mislabelled, and it
    would make the gate look worse than it is.
    """
    return [
        f"{query!r} -> section {section} not in corpus"
        for query, section in HELD_OUT_ANSWERABLE
        if section not in corpus_sections
    ]
