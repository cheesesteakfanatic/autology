"""PromptLibrary: multi-version coexistence, zero-regression seeding from PROMPTS,
and DETERMINISTIC champion selection from an observation log (plan §3)."""

from __future__ import annotations

import pytest

from ontoforge.aimodels.library import PromptLibrary
from ontoforge.aimodels.observation import ObservationLog
from ontoforge.aimodels.prompts import PROMPTS, PromptTemplate

_DECISION_SCHEMA = {
    "type": "object",
    "required": ["decision", "confidence"],
    "properties": {
        "decision": {"type": "string"},
        "confidence": {"type": "number"},
    },
}


def _tmpl(task: str, version: str, instruction: str = "do the thing") -> PromptTemplate:
    return PromptTemplate(task=task, version=version, instruction=instruction, schema=_DECISION_SCHEMA)


def test_seeding_from_prompts_preserves_every_task() -> None:
    lib = PromptLibrary()
    # every task in the static registry is present, and is its own champion
    assert set(lib.tasks()) == set(PROMPTS)
    for task, template in PROMPTS.items():
        assert template.version in lib.versions(task)
        assert lib.champion(task) == template.version
        # champion get() returns byte-identical template to the seed
        assert lib.get(task) is template


def test_two_versions_of_one_task_coexist_and_are_retrievable() -> None:
    lib = PromptLibrary(seed=False)
    v1 = _tmpl("join", "1", "version one")
    v2 = _tmpl("join", "2", "version two")
    lib.register(v1)
    lib.register(v2)
    assert lib.versions("join") == ("1", "2")
    # both retrievable by explicit version
    assert lib.get("join", "1") is v1
    assert lib.get("join", "2") is v2
    # first registered remains champion (register does not auto-promote)
    assert lib.champion("join") == "1"
    assert lib.get("join") is v1


def test_register_replaces_same_version_and_keeps_champion() -> None:
    lib = PromptLibrary(seed=False)
    lib.register(_tmpl("merge", "1", "first"))
    lib.register(_tmpl("merge", "2", "second"))
    lib.set_champion("merge", "2")
    # replacing version 1 must not change the champion (still 2)
    replacement = _tmpl("merge", "1", "first-rewritten")
    lib.register(replacement)
    assert lib.versions("merge") == ("1", "2")
    assert lib.get("merge", "1") is replacement
    assert lib.champion("merge") == "2"


def test_set_champion_validates() -> None:
    lib = PromptLibrary()
    with pytest.raises(KeyError):
        lib.set_champion("join", "does-not-exist")
    with pytest.raises(KeyError):
        lib.get("join", "does-not-exist")
    with pytest.raises(KeyError):
        lib.get("no-such-task")


def test_champion_selection_picks_higher_mean_confidence() -> None:
    lib = PromptLibrary(seed=False)
    lib.register(_tmpl("join", "a"))
    lib.register(_tmpl("join", "b"))
    assert lib.champion("join") == "a"  # first registered

    log = ObservationLog()
    # version "a": mean confidence 0.5 over 2 obs
    log.append("join", "a", "fp0", "heuristic", "deterministic", "fire", 0.4)
    log.append("join", "a", "fp1", "heuristic", "deterministic", "fire", 0.6)
    # version "b": mean confidence 0.9 over 2 obs -> should win
    log.append("join", "b", "fp2", "heuristic", "deterministic", "fire", 0.9)
    log.append("join", "b", "fp3", "heuristic", "deterministic", "hold", 0.9)

    lib.select_by_observations(log)
    assert lib.champion("join") == "b"


def test_champion_selection_tiebreak_is_stable() -> None:
    """Equal mean_confidence + equal count -> lexicographically smallest version,
    and the result is identical across repeated runs (no time/random)."""
    results = set()
    for _ in range(5):
        lib = PromptLibrary(seed=False)
        # register in a non-sorted order to prove ordering is by the tie-break,
        # not by registration order
        lib.register(_tmpl("merge", "z"))
        lib.register(_tmpl("merge", "a"))
        lib.register(_tmpl("merge", "m"))
        log = ObservationLog()
        for ver in ("z", "a", "m"):
            log.append("merge", ver, f"fp-{ver}", "heuristic", "deterministic", "fire", 0.7)
        lib.select_by_observations(log)
        results.add(lib.champion("merge"))
    assert results == {"a"}  # smallest version on every run


def test_count_tiebreak_when_confidence_equal() -> None:
    lib = PromptLibrary(seed=False)
    lib.register(_tmpl("retype", "1"))
    lib.register(_tmpl("retype", "2"))
    log = ObservationLog()
    # equal mean confidence (0.8) but version "2" has more observations
    log.append("retype", "1", "f0", "heuristic", "deterministic", "fire", 0.8)
    log.append("retype", "2", "f1", "heuristic", "deterministic", "fire", 0.8)
    log.append("retype", "2", "f2", "heuristic", "deterministic", "fire", 0.8)
    lib.select_by_observations(log)
    assert lib.champion("retype") == "2"  # higher count wins the tie


def test_task_with_no_observations_keeps_champion() -> None:
    lib = PromptLibrary(seed=False)
    lib.register(_tmpl("join", "1"))
    lib.register(_tmpl("join", "2"))
    lib.set_champion("join", "2")
    lib.register(_tmpl("merge", "1"))
    log = ObservationLog()
    # observations only for join; merge has none
    log.append("join", "1", "f0", "heuristic", "deterministic", "fire", 0.99)
    lib.select_by_observations(log)
    assert lib.champion("join") == "1"  # promoted by evidence
    assert lib.champion("merge") == "1"  # untouched (no evidence)


def test_unregistered_version_in_log_is_ignored() -> None:
    lib = PromptLibrary(seed=False)
    lib.register(_tmpl("join", "1"))
    log = ObservationLog()
    # a phantom version "99" with high confidence the library never registered
    log.append("join", "99", "f0", "heuristic", "deterministic", "fire", 1.0)
    log.append("join", "1", "f1", "heuristic", "deterministic", "fire", 0.3)
    lib.select_by_observations(log)
    # only registered versions are promotable -> stays on "1"
    assert lib.champion("join") == "1"
