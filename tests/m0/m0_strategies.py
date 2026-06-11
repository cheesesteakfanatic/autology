"""Shared hypothesis strategies for M0 property tests.

Random provenance terms are built ONLY through the contracts' smart constructors,
so every generated term is already in normal form (flattened, identity-eliminated,
ZERO-annihilated) — matching what any real producer of terms would hand the ledger.
"""

from hypothesis import strategies as st

from ontoforge.contracts.provenance import ONE, ZERO, leaf, prov_prod, prov_sum

ATOM_IDS = [f"a{i:02d}" for i in range(8)]

# Deterministic per-atom confidences for the 'confidence' valuation tests.
CONF = {aid: (i + 1) / 10 for i, aid in enumerate(ATOM_IDS)}

atom_ids_st = st.sampled_from(ATOM_IDS)

_base = st.one_of(
    atom_ids_st.map(leaf),
    st.just(ZERO),
    st.just(ONE),
)


def terms(max_leaves: int = 16) -> st.SearchStrategy:
    """Random normalized provenance polynomials over a small atom alphabet."""
    return st.recursive(
        _base,
        lambda inner: st.one_of(
            st.lists(inner, min_size=0, max_size=3).map(prov_sum),
            st.lists(inner, min_size=0, max_size=3).map(prov_prod),
        ),
        max_leaves=max_leaves,
    )
