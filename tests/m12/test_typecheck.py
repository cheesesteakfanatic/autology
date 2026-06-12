"""M12 — OQIR type-checker adversarial suite (§6.2 well-formedness; §11.2 M12
'rejects seeded ill-typed plans with 100% recall').

HARD GATE: every seeded ill-typed plan is rejected (returns TypeError_), and
every well-typed plan is accepted. The seeded set covers all the §6.2 error
classes: phantom links (forward + reverse), unknown elements, unit mixing
(the 'altitude in dollars' case), SUM over text, wrong-grain aggregation,
TopK over a non-Table, malformed stances, and root-ambiguous traversals.
"""

from __future__ import annotations

import pytest

from ontoforge.contracts import Stance
from ontoforge.contracts.oqir import (
    Agg,
    Aggregate,
    AsOf,
    CmpOp,
    Condition,
    EntitySetT,
    Select,
    TableT,
    TextJoin,
    TopK,
    Traverse,
    TypeError_,
)
from ontoforge.lodestone import typecheck

NS = "onto://gold/aviation"


def C(name: str) -> str:
    return f"{NS}/{name}"


def sel(cls: str, *conds: Condition) -> Select:
    return Select(C(cls), tuple(conds))


def _forge_stance(kind: str, valid_at, known_at) -> Stance:
    """Bypass the contract's constructor validation (an adversarially
    deserialized plan would not run __post_init__ semantics either)."""
    s = object.__new__(Stance)
    object.__setattr__(s, "kind", kind)
    object.__setattr__(s, "valid_at", valid_at)
    object.__setattr__(s, "known_at", known_at)
    return s


# --------------------------------------------------------- ill-typed plans

ILL_TYPED = {
    "unknown_class": lambda: Select("onto://gold/aviation/Spaceship"),
    "unknown_property": lambda: sel(
        "Aircraft", Condition("wing_span", CmpOp.GT, 30.0)
    ),
    "phantom_forward_link": lambda: Traverse(sel("Aircraft"), "pilot"),
    "traverse_via_datatype_prop": lambda: Traverse(sel("Aircraft"), "serial_number"),
    "phantom_reverse_link": lambda: Traverse(sel("Place"), "model", reverse=True),
    "unit_mixing_usd_vs_ft": lambda: sel(
        "IncidentReport", Condition("altitude_agl", CmpOp.LT, 100.0, unit="USD")
    ),
    "unit_mixing_kg_vs_hours": lambda: sel(
        "WorkOrder", Condition("labor_hours", CmpOp.GT, 5.0, unit="kg")
    ),
    "unknown_unit": lambda: sel(
        "IncidentReport", Condition("altitude_agl", CmpOp.LT, 100.0, unit="furlong")
    ),
    "cross_currency_static_conversion": lambda: sel(
        "WorkOrder", Condition("cost", CmpOp.GT, 100.0, unit="EUR")
    ),
    "sum_over_text": lambda: Aggregate(sel("IncidentReport"), Agg.SUM, "narrative"),
    "sum_over_string": lambda: Aggregate(sel("Aircraft"), Agg.SUM, "tail_number"),
    "avg_unknown_measure": lambda: Aggregate(sel("Aircraft"), Agg.AVG, "airframe_hours"),
    "sum_without_measure": lambda: Aggregate(sel("WorkOrder"), Agg.SUM, None),
    "groupby_unknown": lambda: Aggregate(
        sel("WorkOrder"), Agg.COUNT, None, group_by=("priority",)
    ),
    "groupby_bad_path": lambda: Aggregate(
        sel("Aircraft"), Agg.COUNT, None, group_by=("model.serial_number",)
    ),
    "having_unknown_column": lambda: Aggregate(
        sel("Aircraft"), Agg.COUNT, None, group_by=("tail_number",),
        having=(Condition("max_cost", CmpOp.GT, 1),),
    ),
    "topk_over_entity_set": lambda: TopK(sel("Aircraft"), by="year_mfr", k=5),
    "topk_unknown_column": lambda: TopK(
        Aggregate(sel("WorkOrder"), Agg.SUM, "cost"), by="sum_hours", k=5
    ),
    # malformed stances cannot be CONSTRUCTED via the contract (__post_init__
    # raises), but an adversarial plan may arrive deserialized — the checker
    # must still reject it statically ('bad stance' class)
    "bad_stance_asof_missing_instant": lambda: AsOf(
        _forge_stance("as_of", None, None), sel("Aircraft")
    ),
    "bad_stance_audit_half_specified": lambda: AsOf(
        _forge_stance("audit", 123, None), sel("Aircraft")
    ),
    "bad_stance_unknown_kind": lambda: AsOf(
        _forge_stance("time_travel", 123, 123), sel("Aircraft")
    ),
    "ordered_cmp_numeric_vs_string": lambda: sel(
        "IncidentReport", Condition("altitude_agl", CmpOp.GT, "high")
    ),
    "condition_on_link_property": lambda: sel(
        "Aircraft", Condition("model", CmpOp.EQ, "SR22")
    ),
    "contains_on_numeric": lambda: sel(
        "AccidentEvent", Condition("fatalities", CmpOp.CONTAINS, "2")
    ),
    "textjoin_on_string_prop": lambda: TextJoin(
        sel("IncidentReport"), "flight_phase", "descent"
    ),
    "textjoin_unknown_prop": lambda: TextJoin(sel("IncidentReport"), "report_body", "bird"),
    "textjoin_empty_pattern": lambda: TextJoin(sel("IncidentReport"), "narrative", ""),
    "ambiguous_reverse_traversal_at_root": lambda: Traverse(
        sel("Operator"), "operator", reverse=True
    ),
}

# the trick-unit competency case (CQ-18) expressed as expect_unit
TRICK_UNIT = lambda: Aggregate(sel("IncidentReport"), Agg.SUM, "altitude_agl")  # noqa: E731


@pytest.mark.parametrize("name", sorted(ILL_TYPED))
def test_ill_typed_plan_is_rejected(name, gold_onto):
    t = typecheck(ILL_TYPED[name](), gold_onto)
    assert isinstance(t, TypeError_), f"{name}: accepted as {t}"
    assert t.message


def test_seeded_set_is_large_enough():
    assert len(ILL_TYPED) >= 12


def test_total_altitude_in_dollars_is_a_type_error(gold_onto):
    """CQ-18's plan must die in the CHECKER, not at execution (§6.2)."""
    t = typecheck(TRICK_UNIT(), gold_onto, expect_unit="USD")
    assert isinstance(t, TypeError_)
    assert "USD" in t.message and ("ft" in t.message or "dimension" in t.message)


def test_ill_typed_rejection_recall_is_100_percent(gold_onto):
    rejected = sum(
        isinstance(typecheck(mk(), gold_onto), TypeError_) for mk in ILL_TYPED.values()
    )
    assert rejected == len(ILL_TYPED)


# --------------------------------------------------------- well-typed plans


WELL_TYPED: dict[str, tuple] = {
    "plain_select": (lambda: sel("Aircraft"), EntitySetT),
    "select_with_condition": (
        lambda: sel("Aircraft", Condition("tail_number", CmpOp.EQ, "N4669X")),
        EntitySetT,
    ),
    "select_with_dotted_path_condition": (
        lambda: sel(
            "Aircraft", Condition("model.manufacturer.name", CmpOp.IN, ("ROCKWELL INTL",))
        ),
        EntitySetT,
    ),
    "condition_with_convertible_unit": (
        # meters literal on a feet property: same dimension -> conversion injected
        lambda: sel("IncidentReport", Condition("altitude_agl", CmpOp.LT, 3000.0, unit="m")),
        EntitySetT,
    ),
    "forward_traverse": (lambda: Traverse(sel("Aircraft"), "model"), EntitySetT),
    "inherited_link_traverse": (
        # 'aircraft' is declared on SafetyEvent; IncidentReport inherits it
        lambda: Traverse(sel("IncidentReport"), "aircraft"),
        EntitySetT,
    ),
    "reverse_traverse_narrowed_by_measure": (
        # reverse 'aircraft' is owner-ambiguous; SUM(cost) narrows to WorkOrder
        lambda: Aggregate(
            Traverse(sel("Aircraft", Condition("tail_number", CmpOp.EQ, "N79946")),
                     "aircraft", reverse=True),
            Agg.SUM, "cost",
        ),
        TableT,
    ),
    "textjoin_on_text_prop": (
        lambda: TextJoin(sel("IncidentReport"), "narrative", "bird strike"),
        EntitySetT,
    ),
    "count_aggregate": (lambda: Aggregate(sel("Aircraft"), Agg.COUNT, None), TableT),
    "groupby_having": (
        lambda: Aggregate(
            sel("Aircraft"), Agg.COUNT, "serial_number", group_by=("tail_number",),
            having=(Condition("count_serial_number", CmpOp.GT, 1),),
        ),
        TableT,
    ),
    "topk_over_table": (
        lambda: TopK(
            Aggregate(sel("WorkOrder"), Agg.SUM, "cost", group_by=("action",)),
            by="sum_cost", k=3,
        ),
        TableT,
    ),
    "asof_wraps_anything": (
        lambda: AsOf(
            Stance("as_of", valid_at=1_000_000),
            Traverse(sel("Aircraft", Condition("tail_number", CmpOp.EQ, "N44304")),
                     "registrant"),
        ),
        EntitySetT,
    ),
}


@pytest.mark.parametrize("name", sorted(WELL_TYPED))
def test_well_typed_plan_is_accepted(name, gold_onto):
    mk, expected_kind = WELL_TYPED[name]
    t = typecheck(mk(), gold_onto)
    assert not isinstance(t, TypeError_), f"{name}: rejected with {t}"
    assert isinstance(t, expected_kind)


def test_well_typed_set_is_large_enough():
    assert len(WELL_TYPED) >= 8


def test_expect_unit_identity_and_convertible(gold_onto):
    t = typecheck(Aggregate(sel("WorkOrder"), Agg.SUM, "cost"), gold_onto, expect_unit="USD")
    assert isinstance(t, TableT)
    t = typecheck(
        Aggregate(sel("IncidentReport"), Agg.MIN, "altitude_agl"), gold_onto, expect_unit="m"
    )
    assert isinstance(t, TableT)
