"""§4.5 acceptance: the BITEMPORAL SCENARIO SUITE, table-driven gold scenarios.

Each scenario is data: a sequence of writes (pipeline commits and/or human
Actions) followed by gold assertions per temporal stance, plus optional
history-shape assertions. Covers:

(a) plain insert -> current / as-of / as-known-at;
(b) world-time retroactive correction (audit distinguishes old/new belief);
(c) supersession by a newer same-source value (system-expired, still known-at);
(d) survivorship: human Action beats pipeline; pipeline cannot clobber an
    Action; two pipeline sources ranked; confidence tiebreak at equal rank.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from m6_helpers import mint_prov, stance, vc

from ontoforge.contracts import FOREVER, Layer
from ontoforge.hearth import Hearth, SetProperty
from ontoforge.ledger import SqliteLedger

CLASS = "onto://test/Thing"
E = "e://thing/1"


@dataclass(frozen=True)
class Commit:
    """One pipeline write: (prop, value, valid_from, valid_to, rank, conf) @ now."""

    prop: str
    value: Any
    now: int
    valid_from: int = 0
    valid_to: int = FOREVER
    rank: int = 1
    conf: float = 1.0


@dataclass(frozen=True)
class HumanSet:
    """One human Action (rank 0): SetProperty @ now (valid defaults [now, inf))."""

    prop: str
    value: Any
    now: int
    actor: str = "alice"


@dataclass(frozen=True)
class Scenario:
    name: str
    steps: tuple
    # (stance_spec, expected prop->value dict) — gold answers per stance
    reads: tuple = ()
    # (prop, [(value, system_open), ...]) in history order — audit-trail shape
    histories: tuple = ()


SCENARIOS = [
    # ----------------------------------------------------------- (a) insert
    Scenario(
        name="plain_insert",
        steps=(Commit("status", "active", now=1000, valid_from=100),),
        reads=(
            (("current",), {"status": "active"}),
            (("as_of", 150), {"status": "active"}),
            (("as_of", 50), {}),  # before the fact held in the world
            (("as_known_at", 1500), {"status": "active"}),
            (("as_known_at", 500), {}),  # before the system knew anything
            (("audit", 150, 1500), {"status": "active"}),
            (("audit", 150, 500), {}),
        ),
        histories=(("status", [("active", True)]),),
    ),
    # ------------------------------------- (b) world-time retroactive correction
    # "the value was wrong for [200, 300)" — correction lands at system time 2000
    Scenario(
        name="retroactive_correction",
        steps=(
            Commit("status", "airworthy", now=1000, valid_from=100),
            Commit("status", "grounded", now=2000, valid_from=200, valid_to=300),
        ),
        reads=(
            # current value unchanged: the correction only touches the past window
            (("current",), {"status": "airworthy"}),
            # corrected world time
            (("as_of", 250), {"status": "grounded"}),
            (("as_of", 150), {"status": "airworthy"}),
            (("as_of", 350), {"status": "airworthy"}),
            # what the system believed BEFORE the correction: the old value
            (("as_known_at", 1500), {"status": "airworthy"}),
            # audit() distinguishes both beliefs about the same world time
            (("audit", 250, 1500), {"status": "airworthy"}),
            (("audit", 250, 2500), {"status": "grounded"}),
            (("audit", 150, 2500), {"status": "airworthy"}),
        ),
        histories=(
            # original (system-closed), correction, left residual, right residual
            ("status", [("airworthy", False), ("grounded", True), ("airworthy", True), ("airworthy", True)]),
        ),
    ),
    # --------------------------------- (c) supersession by newer source value
    Scenario(
        name="newer_source_supersedes",
        steps=(
            Commit("fuel_qty", 50, now=1000),
            Commit("fuel_qty", 75, now=2000),
        ),
        reads=(
            (("current",), {"fuel_qty": 75}),
            (("as_known_at", 1500), {"fuel_qty": 50}),  # past belief preserved
            (("as_known_at", 2500), {"fuel_qty": 75}),
            (("audit", 10, 1500), {"fuel_qty": 50}),
            (("audit", 10, 2500), {"fuel_qty": 75}),
        ),
        histories=(("fuel_qty", [(50, False), (75, True)]),),
    ),
    # ------------------------------------------- (d) survivorship: rank matrix
    Scenario(
        name="human_beats_pipeline_then_pipeline_cannot_clobber",
        steps=(
            Commit("status", "pipeline-1", now=1000),
            HumanSet("status", "human-fix", now=2000),
            Commit("status", "pipeline-2", now=3000),  # must land dead-on-arrival
        ),
        reads=(
            (("current",), {"status": "human-fix"}),
            (("as_known_at", 3500), {"status": "human-fix"}),
            # the human edit's valid time starts at 2000; before that the
            # pipeline value still held in the world (left residual)
            (("as_of", 1500), {"status": "pipeline-1"}),
            (("as_known_at", 1500), {"status": "pipeline-1"}),
        ),
        histories=(
            # pipeline-1 (closed), human-fix (current), residual [0,2000) of
            # pipeline-1 (open), pipeline-2 dead-on-arrival (closed)
            (
                "status",
                [("pipeline-1", False), ("human-fix", True), ("pipeline-1", True), ("pipeline-2", False)],
            ),
        ),
    ),
    Scenario(
        name="two_pipeline_sources_rank_order",
        steps=(
            Commit("owner", "FAA-registry", now=1000, rank=1),
            Commit("owner", "scraped-blog", now=2000, rank=2),  # worse source, newer
        ),
        reads=(
            (("current",), {"owner": "FAA-registry"}),
            (("as_known_at", 2500), {"owner": "FAA-registry"}),
        ),
        histories=(("owner", [("FAA-registry", True), ("scraped-blog", False)]),),
    ),
    Scenario(
        name="two_pipeline_sources_rank_order_reversed_arrival",
        steps=(
            Commit("owner", "scraped-blog", now=1000, rank=2),
            Commit("owner", "FAA-registry", now=2000, rank=1),  # better source wins
        ),
        reads=(
            (("current",), {"owner": "FAA-registry"}),
            (("as_known_at", 1500), {"owner": "scraped-blog"}),
        ),
        histories=(("owner", [("scraped-blog", False), ("FAA-registry", True)]),),
    ),
    Scenario(
        name="equal_rank_higher_confidence_wins",
        steps=(
            Commit("seats", 180, now=1000, rank=1, conf=0.9),
            Commit("seats", 200, now=2000, rank=1, conf=0.4),  # newer but less sure
        ),
        reads=((("current",), {"seats": 180}),),
        histories=(("seats", [(180, True), (200, False)]),),
    ),
    Scenario(
        name="equal_rank_equal_confidence_newer_wins",
        steps=(
            Commit("seats", 180, now=1000, rank=1, conf=0.9),
            Commit("seats", 200, now=2000, rank=1, conf=0.9),
        ),
        reads=((("current",), {"seats": 200}),),
        histories=(("seats", [(180, False), (200, True)]),),
    ),
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_bitemporal_scenario(tmp_path, scenario: Scenario) -> None:
    ledger = SqliteLedger()
    h = Hearth(tmp_path / "h", ledger)
    for i, step in enumerate(scenario.steps):
        if isinstance(step, Commit):
            prov = mint_prov(ledger, scenario.name, i)
            cell = vc(
                E,
                step.prop,
                step.value,
                prov,
                valid_from=step.valid_from,
                valid_to=step.valid_to,
                rank=step.rank,
                conf=step.conf,
            )
            h.commit(Layer.ENTITY, CLASS, [cell], now=step.now)
        elif isinstance(step, HumanSet):
            h.action(step.actor, SetProperty(CLASS, E, step.prop, step.value), now=step.now)
        else:  # pragma: no cover
            raise AssertionError(step)

    for stance_spec, expected in scenario.reads:
        got = h.read(E, stance(stance_spec))
        assert got == expected, f"{scenario.name}: read{stance_spec} = {got!r}, want {expected!r}"

    for prop, shape in scenario.histories:
        cells = h.history(E, prop)
        got_shape = [(c.value, c.system.open) for c in cells]
        assert got_shape == shape, f"{scenario.name}: history({prop}) = {got_shape!r}, want {shape!r}"


def test_scenarios_survive_reload(tmp_path) -> None:
    """Replay every scenario, reopen the store from disk, and require the same
    gold answers — canonical Parquet alone must reconstruct all reads."""
    ledger = SqliteLedger()
    for scenario in SCENARIOS:
        root = tmp_path / scenario.name
        h = Hearth(root, ledger)
        for i, step in enumerate(scenario.steps):
            if isinstance(step, Commit):
                prov = mint_prov(ledger, "reload", scenario.name, i)
                h.commit(
                    Layer.ENTITY,
                    CLASS,
                    [
                        vc(
                            E,
                            step.prop,
                            step.value,
                            prov,
                            valid_from=step.valid_from,
                            valid_to=step.valid_to,
                            rank=step.rank,
                            conf=step.conf,
                        )
                    ],
                    now=step.now,
                )
            else:
                h.action(step.actor, SetProperty(CLASS, E, step.prop, step.value), now=step.now)
        reopened = Hearth(root, ledger)
        for stance_spec, expected in scenario.reads:
            assert reopened.read(E, stance(stance_spec)) == expected, (scenario.name, stance_spec)
        for prop, shape in scenario.histories:
            assert [(c.value, c.system.open) for c in reopened.history(E, prop)] == shape
