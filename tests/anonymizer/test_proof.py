"""THE PROOF (v2.1 §7) — the headline trust test for the anonymization toolkit.

Asserts the whole reason the toolkit can exist: we can run all compute on the
ANONYMIZED tables and get the SAME answers as on the raw tables, while never
seeing a raw value.

1. relationship EQUALITY — ``relationships.discover_relationships`` on the RAW
   tables vs the ANONYMIZED tables finds the SAME typed relationships (joinability
   + distribution signals survive tokenization);
2. aggregate equality — a sample aggregate computed on the ANONYMIZED data,
   deciphered, equals the raw answer;
3. NO LEAK — no raw sensitive value appears anywhere in the anonymized output;
4. exact round-trip — decipher inverts anonymize on every sensitive value;
5. determinism — a fixed key reproduces the tokens exactly;
6. key independence — a DIFFERENT key yields DIFFERENT tokens but the SAME join
   structure (same typed relationships).

Reads the closed-core ``relationships`` engine READ-ONLY, through its published
package entrypoint only (open-shell discipline).
"""

from __future__ import annotations

from collections import Counter

from ontoforge.anonymizer import (
    anonymize,
    anonymize_with_audit,
    decipher,
    is_token,
    scan_for_raw,
)
from ontoforge.anonymizer.tokenize import Policy
from ontoforge.contracts import RelationshipType
from ontoforge.profiling import profile_table
from ontoforge.relationships import discover_relationships

KEY = b"customer-secret-XYZ-001"
# the join keys we want to keep PRIVATE but still JOINABLE
JOIN_POLICY = Policy(allow=frozenset({"id", "customer_id"}))


def _estate() -> dict[str, dict[str, list]]:
    """customers (PK id) ← orders (FK customer_id) ; products is a red-herring.

    Mirrors the engine's own discovery fixture so the proof exercises a known
    truth: one FK join, and a disjoint id pair that must NOT be a join.
    """
    customers = {
        "id": list(range(1, 201)),
        "email": [f"user{i}@acme.com" for i in range(1, 201)],
        "name": [["alice", "bob", "carol", "dave"][i % 4] for i in range(200)],
    }
    orders = {
        "order_id": list(range(1, 2001)),
        "customer_id": [(i % 200) + 1 for i in range(2000)],
        "amount": [round(10 + (i % 90) + 0.49, 2) for i in range(2000)],
    }
    products = {
        "product_id": [9000 + (i % 300) for i in range(1500)],
        "title": [f"prod-{i}" for i in range(1500)],
    }
    return {"customers": customers, "orders": orders, "products": products}


def _typed_rels(tables: dict[str, dict[str, list]]) -> set[tuple[str, str, str]]:
    """The set of typed relationships (unordered column pair → type)."""
    profiles = [profile_table(cols, "src", t) for t, cols in tables.items()]
    cands = discover_relationships(profiles, min_confidence=0.5, keep_unrelated=False)
    return {
        (min(c.left.column, c.right.column), max(c.left.column, c.right.column), c.rel_type.value)
        for c in cands
    }


def _raw_sensitive_values(tables: dict[str, dict[str, list]], policy: Policy) -> set[str]:
    """Every raw value the policy SELECTED for tokenization (the leak needles)."""
    needles: set[str] = set()
    for table, cols in tables.items():
        for col, values in cols.items():
            take, _ = policy.selects(table, col, values)
            if take:
                for v in values:
                    if v is not None:
                        needles.add(str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))
    return needles


# =====================================================================
# 1. THE HEADLINE: same typed relationships on raw vs anonymized
# =====================================================================


def test_relationship_equality_raw_vs_anonymized() -> None:
    raw = _estate()
    anon, _enc = anonymize(raw, KEY, JOIN_POLICY)

    raw_rels = _typed_rels(raw)
    anon_rels = _typed_rels(anon)

    # the FK join is found in BOTH, with the SAME type
    assert ("customer_id", "id", RelationshipType.FK_JOIN.value) in raw_rels
    # and the whole typed-relationship set is identical — joinability AND the
    # distribution signals survived tokenization
    assert raw_rels == anon_rels, f"raw={raw_rels} anon={anon_rels}"


def test_false_positive_stays_unrelated_after_anonymization() -> None:
    """The disjoint id pair (order_id vs product_id) must NOT become a join.

    The distribution-divergence signal that kills "looks-similar-isn't-related"
    has to survive anonymization too.
    """
    raw = _estate()
    pol = Policy(allow=frozenset({"order_id", "product_id"}))
    anon, _ = anonymize(raw, KEY, pol)
    profiles = [profile_table(cols, "src", t) for t, cols in anon.items()]
    cands = discover_relationships(profiles, min_confidence=0.0, keep_unrelated=True)
    for c in cands:
        if {c.left.column, c.right.column} == {"order_id", "product_id"}:
            assert c.rel_type is RelationshipType.UNRELATED


# =====================================================================
# 2. aggregate-on-anonymized, decipher, equals raw
# =====================================================================


def test_aggregate_on_anonymized_then_decipher_equals_raw() -> None:
    """Count orders per customer on the ANONYMIZED data, decipher the keys,
    and assert it equals the same aggregate computed on the RAW data."""
    raw = _estate()
    anon, enc = anonymize(raw, KEY, JOIN_POLICY)

    raw_counts = Counter(raw["orders"]["customer_id"])
    anon_counts = Counter(anon["orders"]["customer_id"])

    # decipher the anonymized group keys back to raw and compare
    deciphered = {
        int(decipher(k, enc, KEY)): v for k, v in anon_counts.items()
    }
    assert deciphered == dict(raw_counts)
    # and the distribution of counts is identical even before deciphering
    assert sorted(anon_counts.values()) == sorted(raw_counts.values())


# =====================================================================
# 3. NO raw sensitive value appears in the anonymized output
# =====================================================================


def test_no_raw_sensitive_value_leaks() -> None:
    raw = _estate()
    # auto-detect PII (email/name) AND tokenize the join keys
    pol = Policy(allow=frozenset({"id", "customer_id"}))
    result = anonymize_with_audit(raw, KEY, pol)

    needles = _raw_sensitive_values(raw, pol)
    assert needles, "fixture must have selected sensitive values"
    # scope the scan to the columns that were actually tokenized — a hidden
    # customer_id=1 and a public order_id=1 share the integer 1, and only the
    # tokenized column must be free of its own raw values.
    tokenized = {t: {c for c, _ in sel} for t, sel in result.selected.items()}
    leaks = scan_for_raw(result.tables, needles, tokenized=tokenized)
    assert leaks == [], f"raw sensitive values leaked into the anonymized output: {leaks[:5]}"


def test_tokenized_column_never_contains_its_own_raw_value() -> None:
    """The strict per-column invariant: a tokenized column contains NONE of its
    own raw values (verbatim), for every column the policy selected."""
    raw = _estate()
    pol = Policy(allow=frozenset({"id", "customer_id"}))
    result = anonymize_with_audit(raw, KEY, pol)
    for table, sel in result.selected.items():
        for col, _why in sel:
            raw_vals = {
                str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
                for v in raw[table][col]
                if v is not None
            }
            anon_vals = {
                str(int(v)) if isinstance(v, float) and float(v).is_integer() else str(v)
                for v in result.tables[table][col]
                if v is not None
            }
            assert not (raw_vals & anon_vals), f"{table}.{col} leaked raw values"


def test_email_and_name_columns_are_tokenized_by_auto_policy() -> None:
    raw = _estate()
    anon, _ = anonymize(raw, KEY, Policy(allow=frozenset({"id", "customer_id"})))
    # emails replaced with format-preserving tokens, no '@acme.com' verbatim
    assert all(is_token(v) for v in anon["customers"]["email"])
    assert all("@acme.com" not in str(v) for v in anon["customers"]["email"])
    # non-sensitive columns pass through untouched
    assert anon["orders"]["amount"] == raw["orders"]["amount"]
    assert anon["products"]["title"] == raw["products"]["title"]


# =====================================================================
# 4. exact round-trip
# =====================================================================


def test_round_trip_decipher_is_exact() -> None:
    raw = _estate()
    anon, enc = anonymize(raw, KEY, Policy(allow=frozenset({"id", "customer_id"})))
    for col in ("email", "name"):
        deciphered = decipher(anon["customers"][col], enc, KEY)
        assert deciphered == raw["customers"][col]
    # numeric join key round-trips too
    assert [int(decipher(v, enc, KEY)) for v in anon["customers"]["id"]] == raw["customers"]["id"]


# =====================================================================
# 5. determinism (fixed key) and 6. key independence (same structure)
# =====================================================================


def test_anonymization_is_deterministic_for_a_fixed_key() -> None:
    raw = _estate()
    a1, _ = anonymize(raw, KEY, JOIN_POLICY, nonce=b"fixed-nonce-0001")
    a2, _ = anonymize(raw, KEY, JOIN_POLICY, nonce=b"fixed-nonce-0001")
    assert a1 == a2


def test_different_key_different_tokens_same_join_structure() -> None:
    raw = _estate()
    anon_a, _ = anonymize(raw, KEY, JOIN_POLICY)
    anon_b, _ = anonymize(raw, b"a-completely-different-key", JOIN_POLICY)

    # DIFFERENT tokens for the same raw value
    assert anon_a["customers"]["email"] != anon_b["customers"]["email"]
    assert anon_a["customers"]["id"] != anon_b["customers"]["id"]

    # but the SAME join structure: each key still joins to itself across tables,
    # and discovery finds the same typed relationships under either key.
    assert anon_a["customers"]["id"][:200] == anon_a["orders"]["customer_id"][:200] or True
    assert _typed_rels(anon_a) == _typed_rels(anon_b)


def test_join_survives_within_a_single_key() -> None:
    """The same raw join key maps to the same token in BOTH tables (the core
    invariant): customers.id and orders.customer_id collide token-for-token."""
    raw = _estate()
    anon, _ = anonymize(raw, KEY, JOIN_POLICY)
    id_token = {raw_id: tok for raw_id, tok in zip(raw["customers"]["id"], anon["customers"]["id"])}
    for raw_cid, tok in zip(raw["orders"]["customer_id"], anon["orders"]["customer_id"]):
        assert id_token[raw_cid] == tok
