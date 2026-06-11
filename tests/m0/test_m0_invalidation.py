"""Invalidation exactness on a synthetic derivation DAG (§4.2 join, §9, §11.2 M0).

Ground truth is tracked OUTSIDE the ledger: the test builds ~50 artifacts over
~200 atoms with explicitly known dependency edges (direct atoms + parent
artifacts whose provenance is composed in), then checks that
``invalidate(changed)`` equals exactly {artifact | deps(artifact) ∩ changed ≠ ∅}
for many random changed-atom subsets — no over-, no under-invalidation.
"""

import random

from ontoforge.contracts.atoms import make_cell_atom
from ontoforge.contracts.provenance import leaf, prov_prod, prov_sum
from ontoforge.ledger import SqliteLedger

N_ATOMS = 200
N_UNUSED = 20  # registered atoms that no artifact depends on
N_ARTIFACTS = 50
SEED = 42


def _build_dag(led, rng):
    """Returns (atom_ids, unused_ids, ground_truth: artifact_id -> dep atom set)."""
    atoms = [make_cell_atom("src", "tbl", f"r{i}", "v", f"val-{i}") for i in range(N_ATOMS)]
    unused = [make_cell_atom("src", "tbl", f"u{i}", "v", f"unused-{i}") for i in range(N_UNUSED)]
    atom_ids = led.register_atoms(atoms)
    unused_ids = led.register_atoms(unused)

    ground_truth: dict[str, set[str]] = {}
    terms: list = []  # ProvTerm per artifact, for parent composition
    dep_sets: list[set[str]] = []

    for i in range(N_ARTIFACTS):
        direct = rng.sample(atom_ids, k=rng.randint(1, 5))
        parts = [leaf(a) for a in direct]
        deps = set(direct)
        if terms and rng.random() < 0.6:
            k = min(len(terms), rng.randint(1, 2))
            for p in rng.sample(range(len(terms)), k=k):
                parts.append(terms[p])  # joint derivation from a parent artifact
                deps |= dep_sets[p]
        term = prov_prod(parts)
        if rng.random() < 0.3:  # alternative derivation branch
            alt = rng.sample(atom_ids, k=rng.randint(1, 3))
            term = prov_sum([term, prov_prod([leaf(a) for a in alt])])
            deps |= set(alt)
        ref = led.intern(term)
        artifact_id = f"art-{i:03d}"
        led.append_artifact(artifact_id, "derived-cell", f'{{"i": {i}}}', ref)
        ground_truth[artifact_id] = deps
        terms.append(term)
        dep_sets.append(deps)

    return atom_ids, unused_ids, ground_truth


def test_invalidation_exactness_random_subsets():
    rng = random.Random(SEED)
    led = SqliteLedger()
    atom_ids, unused_ids, truth = _build_dag(led, rng)

    for _ in range(40):
        size = rng.randint(1, 12)
        changed = set(rng.sample(atom_ids, k=size))
        if rng.random() < 0.5:  # sprinkle in atoms nothing depends on
            changed |= set(rng.sample(unused_ids, k=rng.randint(1, 3)))
        expected = {aid for aid, deps in truth.items() if deps & changed}
        got = led.invalidate(changed)
        assert got == expected, (
            f"changed={sorted(changed)[:5]}…: over={got - expected}, under={expected - got}"
        )


def test_invalidation_empty_and_unused_only():
    rng = random.Random(SEED)
    led = SqliteLedger()
    _, unused_ids, _ = _build_dag(led, rng)
    assert led.invalidate([]) == set()
    assert led.invalidate(unused_ids) == set()  # no over-invalidation
    assert led.invalidate(["not-even-registered"]) == set()


def test_invalidation_full_changed_set_hits_everything():
    rng = random.Random(SEED)
    led = SqliteLedger()
    atom_ids, _, truth = _build_dag(led, rng)
    got = led.invalidate(atom_ids)  # also exercises the >400 IN-clause chunking
    expected = {aid for aid, deps in truth.items() if deps}  # every artifact has deps
    assert got == expected == set(truth)


def test_invalidation_transitive_through_composed_provenance():
    """A grand-child artifact composed from a parent's term is invalidated by
    the parent's base atoms, even though it references them only transitively."""
    led = SqliteLedger()
    a, b, c = (make_cell_atom("s", "t", f"r{i}", "c", i) for i in range(3))
    led.register_atoms([a, b, c])
    parent_term = prov_prod([leaf(a.atom_id), leaf(b.atom_id)])
    led.append_artifact("parent", "k", "{}", led.intern(parent_term))
    child_term = prov_prod([parent_term, leaf(c.atom_id)])
    led.append_artifact("child", "k", "{}", led.intern(child_term))
    assert led.invalidate([a.atom_id]) == {"parent", "child"}
    assert led.invalidate([c.atom_id]) == {"child"}
    assert led.invalidate([b.atom_id, c.atom_id]) == {"parent", "child"}
