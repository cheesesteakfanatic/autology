"""The M10 replay harness (§3.6 benchmark, AMD-rescaled): 300 seeded random
valid operator sequences (length <= 8) over the gold aviation ontology + the
~207-entity base HEARTH store. After each sequence:

(a) replay(morphism ledger, base) == final ontology (exact class/URI equality);
(b) SNAPSHOT-QUERYABILITY: the fixed 10-query battery authored against the
    BASE version answers identically through the composed rewriter chain;
(c) URI stability: every gold class not structurally replaced (split/merged/
    demoted/dropped) keeps its URI; renames keep URIs by construction;
(d) migration cost ∝ touched extent: label/axiom-only operators record ZERO
    Hearth commits (asserted per record via the engine's commit counter).
"""

from __future__ import annotations

import random

import pytest

from ontoforge.temper import DATA_TOUCHING, TemperEngine, replay

from m10_helpers import BATTERY, auto_accept_spine, run_sequence

N_SEQUENCES = 300
CHUNK = 30

STRUCTURAL_REMOVERS = {
    "SplitClass": ("uri",),
    "MergeClasses": ("c1_uri", "c2_uri"),
    "DemoteClass": ("class_uri",),
    "DropClass": ("uri",),
}


def _removed_uris(records) -> set[str]:
    out: set[str] = set()
    for rec in records:
        for key in STRUCTURAL_REMOVERS.get(rec.op_type, ()):
            out.add(rec.params[key])
    return out


@pytest.mark.parametrize("chunk", range(N_SEQUENCES // CHUNK))
def test_replay_and_snapshot_queryability(chunk, gold, clone_store, base_answers):
    for seed in range(chunk * CHUNK, (chunk + 1) * CHUNK):
        rng = random.Random(seed)
        eng = TemperEngine(gold, clone_store(), auto_accept_spine())
        reports = run_sequence(eng, rng, rng.randint(3, 8))

        # (a) morphism-ledger replay reconstructs O^(t) exactly
        rebuilt = replay(eng.records(), gold)
        assert rebuilt.classes == eng.ontology.classes, f"seed {seed}: replay mismatch"
        assert rebuilt.version == eng.ontology.version

        # (b) snapshot-queryability over the whole battery
        for q in BATTERY:
            got = eng.answer(q, gold.version)
            assert got == base_answers[q], (
                f"seed {seed}: query {q.class_uri} {q.filters} diverged after "
                f"{[r.op_type for r in reports]}: +{sorted(got - base_answers[q])[:3]} "
                f"-{sorted(base_answers[q] - got)[:3]}"
            )

        # (c) URI stability for structurally untouched gold classes
        removed = _removed_uris(eng.records())
        for uri in gold.classes:
            if uri not in removed:
                assert uri in eng.ontology.classes, f"seed {seed}: {uri} lost its URI"

        # (d) commit accounting: only data-touching ops may commit
        for rec in eng.records():
            if rec.op_type not in DATA_TOUCHING:
                assert rec.stats["commits"] == 0, f"seed {seed}: {rec.op_type} committed"


def test_label_only_sequences_zero_hearth_commits(gold, clone_store):
    """§3.6 design target: zero full-table rewrites for label/axiom-only
    changes — in fact zero Hearth commits at all."""
    for seed in (1, 2, 3, 4, 5):
        rng = random.Random(1000 + seed)
        eng = TemperEngine(gold, clone_store(), auto_accept_spine())
        run_sequence(eng, rng, 8, label_only=True)
        assert eng.commit_count == 0
        assert all(rec.stats["commits"] == 0 for rec in eng.records())


def test_sequences_are_deterministic(gold, clone_store):
    """Same seed, same base state => identical operator streams, morphism
    records, and final ontologies."""
    for seed in (11, 42):
        results = []
        for _run in range(2):
            rng = random.Random(seed)
            eng = TemperEngine(gold, clone_store(), auto_accept_spine())
            run_sequence(eng, rng, 6)
            results.append((eng.records(), dict(eng.ontology.classes)))
        assert results[0] == results[1]


def test_harness_exercises_data_touching_operators(gold, clone_store):
    """Coverage sanity: across a slice of seeds the generator reaches the
    structural/data operators, not just label ops."""
    seen: set[str] = set()
    for seed in range(40):
        rng = random.Random(seed)
        eng = TemperEngine(gold, clone_store(), auto_accept_spine())
        run_sequence(eng, rng, rng.randint(3, 8))
        seen.update(rec.op_type for rec in eng.records())
    for kind in ("SplitClass", "MergeClasses", "PromoteProperty", "RetypeProperty", "Generalize"):
        assert kind in seen, f"{kind} never generated in 40 seeds"
