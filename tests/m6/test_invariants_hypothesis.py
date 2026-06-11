"""§4.5(e) interval invariants, property-tested with hypothesis (derandomized).

Under ARBITRARY interleavings of pipeline writes (entities x props x values x
ranks x confidences x open/closed valid intervals), after EVERY commit:

I1. never two current (both-intervals-open) cells for one (entity, prop);
I2. system time is append-monotone: cells are never deleted; a cell's only
    mutation ever is its system interval closing exactly once; created_at is
    non-decreasing in write order;
I3. no overlapping valid intervals among system-open cells of one
    (entity, prop) — checked for same-rank pairs (the spec invariant) AND
    across ranks (the stronger invariant this implementation maintains);
I4. the derived indexes agree with a from-scratch rebuild (disposability);
I5. a current read equals the audit stance pinned at (the far future, now+1).
    now+1, not now: a dead-on-arrival write is auditable for exactly its
    1-microsecond system window [now, now+1) — the documented append-only
    representation of "received and retracted within the same tick" — so the
    equivalence holds from the first instant AFTER the commit settles.
"""

from __future__ import annotations

import tempfile
from itertools import combinations
from pathlib import Path

from hypothesis import given, settings, strategies as st

from m6_helpers import mint_prov, vc

from ontoforge.contracts import FOREVER, Layer, Stance
from ontoforge.hearth import Hearth
from ontoforge.ledger import SqliteLedger

CLASS = "onto://test/Prop"

_op = st.tuples(
    st.sampled_from(["e://a", "e://b"]),  # entity
    st.sampled_from(["p", "q"]),  # prop
    st.integers(min_value=0, max_value=5),  # value
    st.integers(min_value=1, max_value=3),  # src_rank
    st.sampled_from([0.5, 1.0]),  # confidence
    st.integers(min_value=0, max_value=20),  # valid_from (units of 100)
    st.sampled_from([None, 1, 3, 8]),  # valid length in units; None = open
)


def _snapshot(h: Hearth) -> dict[tuple, list]:
    return {
        (s.layer, s.class_uri): list(s.cells)  # ValueCell is frozen; shallow copy suffices
        for s in h.value_shard_items()
    }


def _check_invariants(h: Hearth, before: dict, now: int) -> None:
    for shard in h.value_shard_items():
        old = before.get((shard.layer, shard.class_uri), [])
        # I2 — append-only with single system-close as the only mutation
        assert len(shard.cells) >= len(old)
        for seq, prior in enumerate(old):
            cur = shard.cells[seq]
            if cur != prior:
                assert prior.system.open and not cur.system.open, "illegal mutation"
                assert cur == type(prior)(
                    entity_uri=prior.entity_uri,
                    prop=prior.prop,
                    value=prior.value,
                    valid=prior.valid,
                    system=cur.system,
                    prov_ref=prior.prov_ref,
                    confidence=prior.confidence,
                    src_rank=prior.src_rank,
                ), "only the system interval may change"
                assert cur.system.start == prior.system.start
        assert all(
            shard.cells[i].system.start <= shard.cells[i + 1].system.start
            for i in range(len(shard.cells) - 1)
        ), "created_at must be non-decreasing in write order"

        by_key: dict[tuple, list] = {}
        for c in shard.cells:
            by_key.setdefault((c.entity_uri, c.prop), []).append(c)
        for key, cells in by_key.items():
            # I1 — at most one current cell
            current = [c for c in cells if c.is_current]
            assert len(current) <= 1, f"two current cells for {key}"
            # I3 — system-open cells pairwise valid-disjoint (same rank AND all ranks)
            open_cells = [c for c in cells if c.system.open]
            for a, b in combinations(open_cells, 2):
                assert not a.valid.overlaps(b.valid), f"overlapping open cells for {key}"

    # I4 — derived indexes are disposable: rebuild and compare
    derived = {
        (s.layer, s.class_uri): (dict(s.current), {k: sorted(v) for k, v in s.open_by_key.items()})
        for s in h.value_shard_items()
    }
    h.rebuild_indexes()
    rebuilt = {
        (s.layer, s.class_uri): (dict(s.current), {k: sorted(v) for k, v in s.open_by_key.items()})
        for s in h.value_shard_items()
    }
    assert derived == rebuilt, "incrementally-maintained index diverged from rebuild"

    # I5 — current == audit(far-future world time, known just after the commit)
    for entity in ("e://a", "e://b"):
        cur = h.read(entity)
        aud = h.read(entity, Stance("audit", valid_at=FOREVER - 1, known_at=now + 1))
        assert cur == aud, f"current vs audit divergence for {entity}"


@settings(max_examples=60, deadline=None, derandomize=True)
@given(ops=st.lists(_op, min_size=1, max_size=25))
def test_interval_invariants_hold_under_arbitrary_writes(ops) -> None:
    ledger = SqliteLedger()
    with tempfile.TemporaryDirectory() as tmp:
        h = Hearth(Path(tmp) / "h", ledger)
        prov = mint_prov(ledger, "hyp")
        for i, (entity, prop, value, rank, conf, vf_u, vlen) in enumerate(ops):
            now = 1_000_000 + i * 1_000
            valid_from = vf_u * 100
            valid_to = FOREVER if vlen is None else valid_from + vlen * 100
            before = _snapshot(h)
            h.commit(
                Layer.ENTITY,
                CLASS,
                [vc(entity, prop, value, prov, valid_from=valid_from, valid_to=valid_to, rank=rank, conf=conf)],
                now=now,
            )
            _check_invariants(h, before, now)
        ledger.close()


@settings(max_examples=25, deadline=None, derandomize=True)
@given(ops=st.lists(_op, min_size=2, max_size=12))
def test_batch_commit_preserves_invariants(ops) -> None:
    """The same invariants when all writes land in ONE commit batch."""
    ledger = SqliteLedger()
    with tempfile.TemporaryDirectory() as tmp:
        h = Hearth(Path(tmp) / "h", ledger)
        prov = mint_prov(ledger, "hyp-batch")
        cells = [
            vc(
                entity,
                prop,
                value,
                prov,
                valid_from=vf_u * 100,
                valid_to=FOREVER if vlen is None else vf_u * 100 + vlen * 100,
                rank=rank,
                conf=conf,
            )
            for (entity, prop, value, rank, conf, vf_u, vlen) in ops
        ]
        before = _snapshot(h)
        h.commit(Layer.ENTITY, CLASS, cells, now=1_000_000)
        _check_invariants(h, before, 1_000_000)
        ledger.close()
