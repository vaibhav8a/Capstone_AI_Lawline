"""Correctness tests for the IR metrics.

These matter more than most tests in the repo: if a metric is wrong, every number
in evaluation/results/ is wrong in a way that looks entirely plausible. Each case
below is hand-computed from the metric definition rather than from the output of
the code under test.
"""

import math

import pytest

from evaluation.metrics_ir import (
    aggregate,
    dedupe_sections,
    hit_rate_at_k,
    latency_summary,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

A, B, C, D = ("IPC", "1"), ("IPC", "2"), ("IPC", "3"), ("IPC", "4")


class TestDedupe:
    def test_keeps_first_occurrence_order(self):
        assert dedupe_sections([A, B, A, C, B]) == [A, B, C]

    def test_empty(self):
        assert dedupe_sections([]) == []


class TestPrecision:
    def test_all_relevant(self):
        assert precision_at_k([A, B], {A, B}, 2) == 1.0

    def test_half_relevant(self):
        assert precision_at_k([A, C], {A, B}, 2) == 0.5

    def test_denominator_is_result_count_when_fewer_than_k(self):
        # Two results, one relevant, k=5 -> 1/2, not 1/5. A system returning
        # fewer results should not be penalised for slots it never filled.
        assert precision_at_k([A, C], {A}, 5) == 0.5

    def test_empty_ranking(self):
        assert precision_at_k([], {A}, 5) == 0.0


class TestRecall:
    def test_finds_one_of_two(self):
        assert recall_at_k([A, C, D], {A, B}, 3) == 0.5

    def test_cutoff_excludes_later_hit(self):
        assert recall_at_k([C, D, A], {A}, 2) == 0.0

    def test_no_gold_is_zero(self):
        assert recall_at_k([A], set(), 5) == 0.0


class TestHitRate:
    def test_hit_within_k(self):
        assert hit_rate_at_k([C, A], {A}, 2) == 1.0

    def test_miss_outside_k(self):
        assert hit_rate_at_k([C, D, A], {A}, 2) == 0.0


class TestReciprocalRank:
    @pytest.mark.parametrize("ranked,expected", [
        ([A, B, C], 1.0),
        ([B, A, C], 0.5),
        ([B, C, A], 1.0 / 3.0),
        ([B, C, D], 0.0),
    ])
    def test_first_relevant_position(self, ranked, expected):
        assert reciprocal_rank(ranked, {A}) == pytest.approx(expected)


class TestNDCG:
    def test_perfect_ranking_is_one(self):
        assert ndcg_at_k([A, B, C], {A, B}, 3) == pytest.approx(1.0)

    def test_hand_computed_value(self):
        # ranked = [C, A], gold = {A}; the single hit sits at rank 2.
        # DCG  = 1/log2(3) = 0.63093
        # IDCG = 1/log2(2) = 1.0
        expected = (1.0 / math.log2(3)) / 1.0
        assert ndcg_at_k([C, A], {A}, 5) == pytest.approx(expected)

    def test_two_gold_one_retrieved_at_rank_two(self):
        # DCG  = 1/log2(3)
        # IDCG = 1/log2(2) + 1/log2(3)   (two relevant docs available)
        dcg = 1.0 / math.log2(3)
        idcg = 1.0 + 1.0 / math.log2(3)
        assert ndcg_at_k([C, A], {A, B}, 5) == pytest.approx(dcg / idcg)

    def test_ordering_matters(self):
        assert ndcg_at_k([A, C], {A}, 5) > ndcg_at_k([C, A], {A}, 5)

    def test_no_gold_is_zero(self):
        assert ndcg_at_k([A], set(), 5) == 0.0


class TestAggregate:
    def test_macro_average(self):
        rows = [{"mrr": 1.0}, {"mrr": 0.0}, {"mrr": 0.5}]
        assert aggregate(rows)["mrr"] == pytest.approx(0.5)

    def test_empty(self):
        assert aggregate([]) == {}


class TestPercentile:
    def test_p50_odd_length(self):
        assert percentile([1, 2, 3], 50) == 2

    def test_p95_takes_high_value(self):
        assert percentile(list(range(1, 101)), 95) == 95

    def test_single_value(self):
        assert percentile([7.5], 95) == 7.5

    def test_empty(self):
        assert percentile([], 50) == 0.0


class TestLatencySummary:
    def test_fields_and_values(self):
        summary = latency_summary([10.0, 20.0, 30.0])
        assert summary["n"] == 3
        assert summary["mean_ms"] == pytest.approx(20.0)
        assert summary["min_ms"] == 10.0
        assert summary["max_ms"] == 30.0

    def test_empty(self):
        assert latency_summary([]) == {}
