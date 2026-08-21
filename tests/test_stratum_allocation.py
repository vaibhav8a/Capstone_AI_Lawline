"""
Regression tests for stratum allocation.

The bug these exist to prevent
------------------------------
Stratum progress used to be a counter local to the running process. A harvest
that was interrupted and resumed started every stratum's allocation back at
zero, so the stratum was served again from scratch. In the 800-judgment
expansion this let `bns_era` reach 202 judgments against an approved target of
120, because the harvester was stopped and restarted three times.

The fix is that allocation is derived from the persisted corpus — the `stratum`
field on the records themselves — never from what the current process happens to
have retained. These tests pin that property down: no sequence of interruptions
may push a stratum past its approved target.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.ingestion.corpus_selection import STRATA, TARGET_TOTAL  # noqa: E402
from backend.ingestion.fetch_judgments import plan_strata  # noqa: E402


def _corpus(**by_stratum: int) -> list[dict]:
    """A stand-in corpus carrying only the field allocation depends on."""
    records: list[dict] = []
    for stratum, count in by_stratum.items():
        records.extend({"stratum": stratum} for _ in range(count))
    return records


def _targets() -> dict[str, int]:
    return {s["name"]: s["target"] for s in STRATA}


class TestAllocationComesFromTheCorpus:
    def test_fresh_corpus_gets_the_full_approved_target(self):
        targets = _targets()
        _, outstanding = plan_strata(_corpus(original_260=260), 260 + sum(targets.values()))
        assert outstanding == targets

    def test_progress_already_in_the_corpus_reduces_what_remains(self):
        targets = _targets()
        name = STRATA[0]["name"]
        done = targets[name] // 2
        _, outstanding = plan_strata(
            _corpus(original_260=260, **{name: done}),
            260 + sum(targets.values()) + done,
        )
        assert outstanding[name] == targets[name] - done

    def test_a_completed_stratum_asks_for_nothing(self):
        targets = _targets()
        name = STRATA[0]["name"]
        _, outstanding = plan_strata(
            _corpus(original_260=260, **{name: targets[name]}),
            260 + sum(targets.values()),
        )
        assert outstanding[name] == 0

    def test_an_overshot_stratum_asks_for_nothing_and_never_goes_negative(self):
        """The exact 202-vs-120 condition that motivated this module."""
        targets = _targets()
        name = STRATA[0]["name"]
        _, outstanding = plan_strata(
            _corpus(original_260=260, **{name: targets[name] + 82}),
            TARGET_TOTAL,
        )
        assert outstanding[name] == 0
        assert all(value >= 0 for value in outstanding.values())


class TestInterruptionCannotInflateAStratum:
    @pytest.mark.parametrize("interruptions", [1, 2, 3, 7, 20])
    def test_repeated_resume_never_exceeds_the_approved_target(self, interruptions):
        """
        Simulate a harvest stopped and resumed N times. Each resume re-plans from
        the persisted corpus, exactly as the harvester does on startup. Under the
        old process-local counter this test fails for every N > 1.
        """
        targets = _targets()
        name, target = STRATA[0]["name"], _targets()[STRATA[0]["name"]]
        records = _corpus(original_260=260)
        total_target = 260 + sum(targets.values())

        for _ in range(interruptions):
            prior, outstanding = plan_strata(records, total_target)
            assert prior.get(name, 0) + outstanding[name] <= target, (
                f"planner would allow {name} to reach "
                f"{prior.get(name, 0) + outstanding[name]} against target {target}"
            )
            # The run retains part of its allocation, then is interrupted.
            got = max(1, outstanding[name] // 3)
            records.extend({"stratum": name} for _ in range(got))
            if sum(1 for r in records if r.get("stratum") == name) >= target:
                break

        held = sum(1 for r in records if r.get("stratum") == name)
        assert held <= target, f"{name} reached {held}, approved {target}"

    def test_global_target_is_never_exceeded_by_the_plan(self):
        targets = _targets()
        # A corpus already near the cap: outstanding demand must be scaled to fit.
        records = _corpus(original_260=260, **{s["name"]: 10 for s in STRATA})
        cap = len(records) + 25
        _, outstanding = plan_strata(records, cap)
        assert sum(outstanding.values()) <= cap - len(records)

    def test_no_capacity_left_means_no_outstanding_work(self):
        records = _corpus(original_260=800)
        _, outstanding = plan_strata(records, 800)
        assert sum(outstanding.values()) == 0

    def test_overshoot_is_absorbed_proportionally_not_by_the_last_stratum(self):
        """
        When one stratum overshoots AND capacity is tight, the shortfall must be
        shared. Serving strata first-come-first-served would starve whichever ran
        last. Capacity is pinned below total demand here so the scaling path is
        actually exercised — with a roomy target no scaling is needed and the
        test would pass vacuously.
        """
        targets = _targets()
        first, rest = STRATA[0]["name"], [s["name"] for s in STRATA[1:]]
        records = _corpus(original_260=260, **{first: targets[first] + 82})
        demand = sum(targets[name] for name in rest)
        cap = len(records) + demand // 2          # only half the demand can be met

        _, outstanding = plan_strata(records, cap)

        assert outstanding[first] == 0, "an overshot stratum must ask for nothing"
        assert sum(outstanding.values()) <= cap - len(records)
        served = {name: outstanding[name] / targets[name] for name in rest}
        assert max(served.values()) - min(served.values()) < 0.05, (
            f"strata served at uneven rates: {served}"
        )
        assert all(outstanding[name] < targets[name] for name in rest), (
            f"scaling did not bite: {outstanding}"
        )


class TestLivePlannerAgainstTheRealCorpus:
    def test_the_shipped_corpus_is_within_its_approved_allocation(self):
        """
        Guards the real corpus on disk: allocation must never be negative, and
        the plan must never push the corpus past the configured target.
        """
        import json

        from backend.ingestion.fetch_judgments import JUDGMENTS_PATH

        if not JUDGMENTS_PATH.exists():
            pytest.skip("no corpus on disk")
        records = json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))
        _, outstanding = plan_strata(records, TARGET_TOTAL)
        assert all(value >= 0 for value in outstanding.values())
        assert len(records) + sum(outstanding.values()) <= TARGET_TOTAL

    def test_no_stratum_on_disk_exceeds_its_approved_target(self):
        """
        Phrased as a bound, not an identity. `held + sum(targets) ==
        TARGET_TOTAL` holds only before a harvest runs; after the strata fill,
        the same arithmetic double-counts and fails while nothing is wrong.
        """
        import collections
        import json

        from backend.ingestion.fetch_judgments import JUDGMENTS_PATH

        if not JUDGMENTS_PATH.exists():
            pytest.skip("no corpus on disk")
        records = json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))
        held = collections.Counter(r.get("stratum") for r in records)
        for name, target in _targets().items():
            assert held.get(name, 0) <= target, (
                f"{name} holds {held.get(name, 0)}, approved {target}"
            )
        assert len(records) <= TARGET_TOTAL
