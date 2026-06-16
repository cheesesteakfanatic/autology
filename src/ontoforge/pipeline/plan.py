"""PLAN mode — pull a governed data SUBSET, not the whole estate (v2.1 R0/P0, mode one).

The cheap entry into a new estate: instead of ingesting everything, ``plan_subset``
pulls a SMART, BUDGET-BOUNDED subset that still lets the rest of the pipeline
(profiling, IND/relationship discovery, induction) work — then reports exactly what
it kept and why.

Why a naive head/random sample is wrong
----------------------------------------
A ``head(N)`` or uniform random sample silently destroys the two things the engine
most needs from a subset:

* **Joinability.** Relationship discovery is an inclusion-dependency search across
  tables. If a child row's foreign key no longer has its parent key in the subset
  (or vice-versa), the join *disappears* — the subset looks like a pile of silos.
  We therefore keep enough CROSS-TABLE KEY OVERLAP that discovery still fires, and
  we ASSERT it (:meth:`PlanReport.joinability_ok`).
* **Schema shape.** Cardinality boundaries (min/max/rare values), distribution
  edges (tails and modes), and candidate-key DISTINCT coverage carry the signal the
  profiler and inducer reason over. Uniform sampling over-represents the dense
  middle and drops exactly these edges.

So the subset is built by SCHEMA-INFORMED STRATIFIED SAMPLING. For every table we
score each row by how much SCHEMA EVIDENCE it carries and keep the highest-signal
rows within the table's slice of the budget, with three guarantees:

1. **Key coverage.** Distinct candidate-key (and join-key) values are covered first
   — at a tiny budget we still keep *at least one row per key value where feasible*,
   so distinct key coverage is maximized rather than left to chance.
2. **Cardinality / distribution edges.** Per column we force-include the min, the
   max, the rarest values, and the modal (most frequent) values — the tails and
   modes — so the profiler's HLL/KLL/format sketches see the real boundaries.
3. **Hypothesis bootstrap.** When an initial ontology ``hypothesis`` names join
   keys, rows carrying those columns are OVERSAMPLED (their key values are treated
   as first-class coverage targets), so the subset is biased toward confirming or
   refuting the hypothesized relationships.

Everything is deterministic (seeded): equal ``(tables, budget, hypothesis)`` yields
byte-identical subsets and an identical :class:`PlanReport`. Keyless, zero-network.
This reads M3 profiling (sketches, candidate keys), the relationship IND discovery
(cross-table key overlap), and reuses ``aimodels.secure.sample_rows`` for the
stratified round-robin fill — the same data-minimization primitive the AI layer uses.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ontoforge.contracts import IND, TableProfile
from ontoforge.profiling import discover_inds, profile_table
from ontoforge.profiling._values import columns_of, is_null, row_count_of, value_key

from ontoforge.aimodels.secure import sample_rows

__all__ = [
    "PLAN_SEED",
    "MIN_JOIN_OVERLAP",
    "OntologyHypothesis",
    "TablePlan",
    "PlanReport",
    "plan_subset",
]

#: every ordering/tie-break in the planner derives from this single fixed seed —
#: the subset is a deterministic function of the inputs (§18.4 determinism).
PLAN_SEED = 0

#: a join survives in the subset when this FRACTION of the ACHIEVABLE (budget-bounded)
#: distinct key-overlap is still present on BOTH sides of the IND. Below this the
#: subset has silently severed a relationship and the report flags it.
MIN_JOIN_OVERLAP = 0.5

#: absolute floor: a join also survives if this many distinct key values are co-kept
#: on both sides (a few matched keys are enough for IND discovery to re-fire), capped
#: by what the budget can achieve. Protects strong joins under a tiny budget.
MIN_JOIN_KEYS = 5

#: per column we always pull this many distinct edge values from each tail/mode
#: bucket (min/max/rarest/modal) so cardinality boundaries survive the budget.
_EDGE_PER_BUCKET = 2

#: a value is "rare" (a cardinality boundary worth keeping) when it occurs at most
#: this many times in the column.
_RARE_MAX_COUNT = 2


# --------------------------------------------------------------------------
# Hypothesis input
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OntologyHypothesis:
    """An initial ontology guess used to BOOTSTRAP the subset (§ mode-one).

    ``join_keys`` names the (table, column) addresses the hypothesis believes are
    join keys — the planner oversamples around their values so the subset is biased
    toward confirming/refuting the hypothesized relationships. ``key_columns`` is an
    optional per-table hint of identifying columns (treated like extra candidate
    keys when profiling under-detects them). Both are advisory; the planner still
    measures real evidence.
    """

    join_keys: tuple[tuple[str, str], ...] = ()
    key_columns: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def join_columns_for(self, table: str) -> frozenset[str]:
        cols = {c for (t, c) in self.join_keys if t == table}
        cols |= set(self.key_columns.get(table, ()))
        return frozenset(cols)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TablePlan:
    """What the planner kept for one table, and why."""

    table: str
    source_id: str
    total_rows: int
    kept_rows: int
    kept_indices: tuple[int, ...]            # row indices kept, in original order
    candidate_keys: tuple[tuple[str, ...], ...]
    key_coverage: Mapping[str, float]        # column -> distinct-value coverage kept
    edge_columns: tuple[str, ...]            # columns whose tails/modes were forced in
    reasons: Mapping[str, int]               # why-bucket -> rows attributed
    budget: int                              # this table's slice of the total budget

    @property
    def kept_fraction(self) -> float:
        return self.kept_rows / self.total_rows if self.total_rows else 0.0


@dataclass(frozen=True, slots=True)
class JoinOverlap:
    """Cross-table key-overlap retention for one discovered IND edge.

    Joinability is BUDGET-AWARE: a 50-row dimension table cannot retain half of 200
    distinct foreign-key values, and demanding it would make the contract
    unsatisfiable. The denominator is therefore the ACHIEVABLE overlap — the most
    distinct shared values that can co-exist on both sides given each side's budget —
    not the full-estate overlap. ``coverage`` = kept_overlap / achievable, so a
    correctly-reconciled subset reaches ≈1.0 even when it keeps a small absolute
    number of keys. The join also survives on an absolute floor of co-kept shared
    values (a handful of matched keys is enough for IND discovery to re-fire).
    """

    lhs_table: str
    lhs_column: str
    rhs_table: str
    rhs_column: str
    full_overlap: int                        # distinct shared key values in the full estate
    kept_overlap: int                        # distinct shared key values kept on BOTH sides
    achievable: int                          # max co-keepable shared values within budgets
    coverage: float                          # kept_overlap / achievable (budget-aware)

    @property
    def survives(self) -> bool:
        if self.full_overlap == 0:
            return True
        if self.kept_overlap >= min(MIN_JOIN_KEYS, self.achievable):
            return True
        return self.coverage >= MIN_JOIN_OVERLAP


@dataclass(frozen=True, slots=True)
class PlanReport:
    """The governed-subset plan: per-table what/why + cross-table joinability proof."""

    tables: tuple[TablePlan, ...]
    overlaps: tuple[JoinOverlap, ...]
    total_budget: int
    seed: int = PLAN_SEED

    @property
    def total_kept(self) -> int:
        return sum(t.kept_rows for t in self.tables)

    @property
    def within_budget(self) -> bool:
        return self.total_kept <= self.total_budget

    def joinability_ok(self) -> bool:
        """Every discovered join still has enough surviving key overlap to be found."""
        return all(o.survives for o in self.overlaps)

    def severed_joins(self) -> tuple[JoinOverlap, ...]:
        return tuple(o for o in self.overlaps if not o.survives)


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _normalize(tables: Mapping[str, Any]) -> dict[str, dict[str, list]]:
    """Normalize every table to a column dict, in sorted-name order (determinism)."""
    return {name: columns_of(tables[name]) for name in sorted(tables)}


def _key_columns(profile: TableProfile, hyp_cols: frozenset[str]) -> list[str]:
    """Columns whose DISTINCT values must be covered: candidate-key members + hints.

    Sorted, de-duplicated. Candidate keys come from the real M3 profiler; the
    hypothesis (and join-key) columns are folded in so under-detected keys still
    drive coverage.
    """
    cols: set[str] = set(hyp_cols)
    for key in profile.candidate_keys:
        cols.update(key)
    # keep only columns actually present in the profile
    present = set(profile.columns)
    return sorted(c for c in cols if c in present)


def _column_edge_indices(values: list[Any]) -> list[int]:
    """Row indices that capture this column's cardinality/distribution EDGES.

    Forces in the min, the max, the rarest distinct values, and the modal (most
    frequent) values — the tails and the modes. Deterministic: ties broken by the
    canonical value key, then by row index.
    """
    counts: Counter[str] = Counter()
    first_index: dict[str, int] = {}
    numeric: list[tuple[float, int]] = []
    for i, v in enumerate(values):
        if is_null(v):
            continue
        vk = value_key(v)
        counts[vk] += 1
        if vk not in first_index:
            first_index[vk] = i
        fv = _as_float(v)
        if fv is not None:
            numeric.append((fv, i))

    picks: set[int] = set()
    if not counts:
        return []

    # tails of a numeric distribution: the extreme min and max rows
    if numeric:
        numeric.sort()
        picks.add(numeric[0][1])
        picks.add(numeric[-1][1])

    # rarest distinct values (low-cardinality tail) — most discriminating for keys
    rare = sorted(
        (vk for vk, c in counts.items() if c <= _RARE_MAX_COUNT),
        key=lambda vk: (counts[vk], vk),
    )
    for vk in rare[: _EDGE_PER_BUCKET]:
        picks.add(first_index[vk])

    # modes: the most frequent values (the dense center the profiler must still see)
    modal = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for vk, _ in modal[: _EDGE_PER_BUCKET]:
        picks.add(first_index[vk])

    return sorted(picks)


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def _row_strata(columns: dict[str, list], key_cols: Sequence[str], n_rows: int) -> list[str]:
    """A per-row stratum label = the composite key-column value (canonical form).

    Rows sharing a key value land in the same stratum; the round-robin fill then
    covers one row per DISTINCT key value before doubling up — exactly the "at least
    one row per key value where feasible" guarantee at a tiny budget.
    """
    if not key_cols:
        return [str(i) for i in range(n_rows)]  # each row its own stratum
    labels: list[str] = []
    for i in range(n_rows):
        parts = []
        for c in key_cols:
            col = columns.get(c, [])
            v = col[i] if i < len(col) else None
            parts.append("∅" if is_null(v) else value_key(v))
        labels.append("|".join(parts))
    return labels


@dataclass(slots=True)
class _Selection:
    """A table's mutable working selection — finalized into a TablePlan after reconcile."""

    table: str
    source_id: str
    columns: dict[str, list]
    profile: TableProfile
    key_cols: list[str]
    budget: int
    kept: set[int]
    edge_columns: tuple[str, ...]
    reasons: Counter[str]


def _plan_one_table(
    source_id: str,
    table: str,
    columns: dict[str, list],
    profile: TableProfile,
    budget: int,
    hyp_cols: frozenset[str],
) -> _Selection:
    n_rows = row_count_of(columns)
    key_cols = _key_columns(profile, hyp_cols)
    reasons: Counter[str] = Counter()
    budget = max(0, budget)

    def sel(kept: set[int], edges: tuple[str, ...]) -> _Selection:
        return _Selection(
            table=table, source_id=source_id, columns=columns, profile=profile,
            key_cols=key_cols, budget=budget, kept=kept, edge_columns=edges, reasons=reasons,
        )

    if n_rows == 0 or budget <= 0:
        return sel(set(), ())

    if budget >= n_rows:
        reasons["all_rows_within_budget"] = n_rows
        return sel(set(range(n_rows)), ())

    # 1) EDGES first: per-column tails/modes/rare/min/max are non-negotiable signal.
    forced: set[int] = set()
    edge_columns: list[str] = []
    for cname in sorted(columns):
        idxs = _column_edge_indices(columns[cname])
        new = [i for i in idxs if i not in forced]
        if new:
            edge_columns.append(cname)
        for i in idxs:
            if i not in forced and len(forced) < budget:
                forced.add(i)
        if len(forced) >= budget:
            break
    reasons["distribution_edges"] = len(forced)

    # 2) KEY COVERAGE: round-robin across distinct key-value strata so every distinct
    #    candidate-/join-key value gets a representative before any stratum doubles up.
    remaining = budget - len(forced)
    if remaining > 0:
        strata = _row_strata(columns, key_cols, n_rows)
        pool = [i for i in range(n_rows) if i not in forced]
        # rows already forced still "cover" their stratum, so reach uncovered strata
        # first in the round-robin (then by stratum label, then by index).
        covered = {strata[i] for i in forced}
        pool.sort(key=lambda i: (strata[i] in covered, strata[i], i))
        chosen = sample_rows(pool, remaining, stratify_by=[strata[i] for i in pool])
        before = len(forced)
        forced.update(chosen)
        reasons["key_coverage"] = len(forced) - before

    return sel(forced, tuple(edge_columns))


def _finalize(selection: _Selection) -> TablePlan:
    """Snap a (post-reconcile) working selection to an immutable TablePlan + key coverage."""
    s = selection
    kept = tuple(sorted(s.kept)[: s.budget if s.budget else len(s.kept)])
    coverage = _key_coverage(s.columns, s.key_cols, kept)
    return TablePlan(
        table=s.table,
        source_id=s.source_id,
        total_rows=row_count_of(s.columns),
        kept_rows=len(kept),
        kept_indices=kept,
        candidate_keys=s.profile.candidate_keys,
        key_coverage=coverage,
        edge_columns=s.edge_columns,
        reasons=dict(s.reasons),
        budget=s.budget,
    )


def _key_coverage(
    columns: dict[str, list], key_cols: Sequence[str], kept: Sequence[int]
) -> dict[str, float]:
    """Per key-column: fraction of distinct non-null values retained in the subset."""
    out: dict[str, float] = {}
    kept_set = set(kept)
    for c in key_cols:
        col = columns.get(c, [])
        full = {value_key(v) for v in col if not is_null(v)}
        sub = {value_key(col[i]) for i in kept_set if i < len(col) and not is_null(col[i])}
        out[c] = (len(sub) / len(full)) if full else 0.0
    return out


def _distinct(col: list[Any], indices: Optional[set[int]] = None) -> set[str]:
    if indices is None:
        return {value_key(v) for v in col if not is_null(v)}
    return {value_key(col[i]) for i in indices if i < len(col) and not is_null(col[i])}


def _join_overlaps(
    norm: dict[str, dict[str, list]],
    inds: Sequence[IND],
    kept: dict[str, tuple[int, ...]],
    budget: Mapping[str, int],
) -> list[JoinOverlap]:
    """For every discovered IND, measure distinct key-overlap CO-KEPT on both sides.

    Joinability = the distinct values shared by both sides AND kept on both sides
    (a value kept only on the child side dangles — the join would not match it). We
    report that co-kept count against the ACHIEVABLE overlap (bounded by each side's
    budget), so the survival test is satisfiable for a small dimension table.
    """
    out: list[JoinOverlap] = []
    seen: set[tuple[str, str, str, str]] = set()
    for ind in inds:
        lt, lc, rt, rc = ind.lhs_table, ind.lhs_column, ind.rhs_table, ind.rhs_column
        key = (lt, lc, rt, rc)
        if key in seen or lt not in norm or rt not in norm:
            continue
        seen.add(key)
        lcol = norm[lt].get(lc)
        rcol = norm[rt].get(rc)
        if lcol is None or rcol is None:
            continue
        full_shared = _distinct(lcol) & _distinct(rcol)
        kl = set(kept.get(lt, ()))
        kr = set(kept.get(rt, ()))
        # co-kept = shared values present on BOTH the kept-left and kept-right sides
        kept_shared = _distinct(lcol, kl) & _distinct(rcol, kr)
        full_n = len(full_shared)
        kept_n = len(kept_shared)
        achievable = min(full_n, budget.get(lt, full_n), budget.get(rt, full_n))
        out.append(JoinOverlap(
            lhs_table=lt, lhs_column=lc, rhs_table=rt, rhs_column=rc,
            full_overlap=full_n, kept_overlap=kept_n, achievable=achievable,
            coverage=round(kept_n / achievable, 4) if achievable else 1.0,
        ))
    return out


def _value_index(col: list[Any]) -> dict[str, list[int]]:
    """Map each distinct canonical value -> the row indices carrying it (sorted)."""
    idx: dict[str, list[int]] = {}
    for i, v in enumerate(col):
        if is_null(v):
            continue
        idx.setdefault(value_key(v), []).append(i)
    return idx


#: a join's PARENT (rhs) side must be at least this unique to be a viable key target.
_PARENT_KEY_UNIQUENESS = 0.95
#: a join's CHILD (lhs) side must be at most this unique — a real FK repeats. A child
#: that is itself near-unique with a similar cardinality to the parent is a coincidental
#: 1:1 range nesting, not a foreign key.
_CHILD_FK_UNIQUENESS = 0.98


def _credible_joins(inds: Sequence[IND], profiles: dict[str, TableProfile]) -> list[IND]:
    """Filter INDs to genuine many-to-one FK shapes; drop coincidental range nestings.

    Keeps an IND ``child.fk ⊆ parent.pk`` when the parent column is key-like
    (near-unique) and the child column is NOT near-unique (a real FK repeats across
    child rows). This is the schema-shape half of the §1 false-positive discipline:
    two unique key ranges that happen to nest are not a relationship the subset must
    preserve. Determinism is inherited from the (already-sorted) IND list.
    """
    out: list[IND] = []
    for ind in inds:
        if ind.lhs_table == ind.rhs_table:
            continue  # intra-table nesting is not a cross-table join to preserve
        ltp, rtp = profiles.get(ind.lhs_table), profiles.get(ind.rhs_table)
        if ltp is None or rtp is None:
            continue
        child = ltp.columns.get(ind.lhs_column)
        parent = rtp.columns.get(ind.rhs_column)
        if child is None or parent is None:
            continue
        if parent.uniqueness < _PARENT_KEY_UNIQUENESS:
            continue
        if child.uniqueness >= _CHILD_FK_UNIQUENESS:
            continue
        out.append(ind)
    return out


def _dedupe_edges(
    inds: Sequence[IND], norm: dict[str, dict[str, list]]
) -> list[tuple[str, str, str, str]]:
    """One directional edge per column-pair: keep the higher-scored IND direction.

    Real foreign keys are directional (child.fk ⊆ parent.pk scores high; the reverse
    is coincidental). Collapsing each unordered pair to its strongest direction lets
    the reconcile build a CONTAINMENT-CONSISTENT subset (child ⊆ parent) instead of
    fighting itself across the two directions. Ordered by descending score so the
    strongest joins claim budget first. Deterministic.
    """
    best: dict[frozenset[tuple[str, str]], tuple[float, tuple[str, str, str, str]]] = {}
    for ind in inds:
        if ind.lhs_table not in norm or ind.rhs_table not in norm:
            continue
        a = (ind.lhs_table, ind.lhs_column)
        b = (ind.rhs_table, ind.rhs_column)
        if a == b:
            continue
        pair = frozenset((a, b))
        edge = (ind.lhs_table, ind.lhs_column, ind.rhs_table, ind.rhs_column)
        cur = best.get(pair)
        if cur is None or (ind.score, edge) > (cur[0], cur[1]):
            best[pair] = (ind.score, edge)
    return [edge for _, edge in sorted(best.values(), key=lambda e: (-e[0], e[1]))]


def _reconcile_joins(
    norm: dict[str, dict[str, list]],
    inds: Sequence[IND],
    kept: dict[str, set[int]],
    budget: dict[str, int],
    target_overlap: float,
) -> None:
    """Rebuild the subset around its JOINS so relationship discovery still fires.

    The per-table pass picks high-signal rows independently, which severs joins: a
    foreign-key value kept on the child side usually has no matching parent row in the
    subset. For each directional IND ``child.fk ⊆ parent.pk`` (strongest first) we:

    1. choose ANCHOR key values — a budget-bounded subset of the shared values,
       preferring those already kept (cheap) then canonical order;
    2. force the PARENT to keep a row for every anchor value;
    3. force the CHILD to keep a row for every anchor value, and RE-POINT dangling
       kept child rows (FK not an anchor) onto anchor-referencing rows.

    The result is CONTAINMENT-CONSISTENT — kept child FK values ⊆ kept parent keys —
    so the IND coverage on the subset stays high and discovery re-fires. Deterministic
    (sorted values/indices); mutates ``kept`` in place; never exceeds a table budget.
    """
    for lt, lc, rt, rc in _dedupe_edges(inds, norm):
        lcol, rcol = norm[lt][lc], norm[rt][rc]
        l_by_val, r_by_val = _value_index(lcol), _value_index(rcol)
        shared = sorted(set(l_by_val) & set(r_by_val))
        if not shared:
            continue
        cap = min(len(shared), budget.get(lt, 0), budget.get(rt, 0))
        if cap <= 0:
            continue
        kept_l_vals = _distinct(lcol, kept.get(lt, set()))
        kept_r_vals = _distinct(rcol, kept.get(rt, set()))
        anchors = sorted(
            shared,
            key=lambda v: (not (v in kept_l_vals and v in kept_r_vals), v),
        )[:cap]
        anchor_set = set(anchors)
        # parent + child both keep a representative row for every anchor value
        _ensure_values(rt, rc, r_by_val, anchors, kept, budget[rt])
        _ensure_values(lt, lc, l_by_val, anchors, kept, budget[lt])
        # containment: re-point kept child rows whose FK is not an anchor onto rows
        # that DO reference an anchor (preserves the row count, never grows past budget)
        _enforce_containment(lt, lc, l_by_val, anchor_set, kept)


def _enforce_containment(
    lt: str,
    lc: str,
    l_by_val: dict[str, list[int]],
    anchor_set: set[str],
    kept: dict[str, set[int]],
) -> None:
    """Swap dangling kept child rows for anchor-referencing rows (size-preserving).

    A kept child row "dangles" when its FK value is not an anchor (so its parent row
    is not kept). We replace each dangling row with an unkept child row whose FK IS an
    anchor, up to availability — improving containment without changing the row count,
    so the budget invariant holds. Deterministic (sorted indices).
    """
    rows = kept.setdefault(lt, set())
    anchor_indices = {i for v in anchor_set for i in l_by_val.get(v, ())}
    repl = iter(sorted(i for i in anchor_indices if i not in rows))
    dangling = sorted(i for i in rows if i not in anchor_indices)
    for d in dangling:
        try:
            r = next(repl)
        except StopIteration:
            break
        rows.discard(d)
        rows.add(r)


def _ensure_values(
    table: str,
    column: str,
    by_val: dict[str, list[int]],
    targets: Sequence[str],
    kept: dict[str, set[int]],
    budget: int,
) -> None:
    """Force ``table`` to keep >= 1 row per target value, swapping rows to stay in budget.

    A value is already covered if any of its rows is kept. Otherwise we add its first
    row; if that would exceed ``budget`` we evict a kept row that does NOT cover any
    target value (lowest index first — deterministic), so coverage strictly improves.
    """
    rows = kept.setdefault(table, set())
    covered = {v for v in targets if any(i in rows for i in by_val.get(v, ()))}
    target_rows = {i for tv in targets for i in by_val.get(tv, ())}
    for v in targets:
        if v in covered:
            continue
        candidates = by_val.get(v, ())
        if not candidates:
            continue
        add = candidates[0]
        if add in rows:
            covered.add(v)
            continue
        if len(rows) >= budget:
            # evict a kept row that protects no target value
            evictable = sorted(i for i in rows if i not in target_rows)
            if not evictable:
                continue  # cannot make room without dropping a needed value
            rows.discard(evictable[0])
        rows.add(add)
        covered.add(v)


def _apportion(
    sizes: Mapping[str, int],
    budget: int,
    key_floor: Optional[Mapping[str, int]] = None,
) -> dict[str, int]:
    """Split the total budget across tables — proportional to size, but FLOORED so a
    small key-bearing dimension is never starved below its distinct-key count.

    ``key_floor[t]`` is the number of distinct candidate-/join-key values table ``t``
    carries — the rows it WANTS so that "at least one row per key value where
    feasible" holds. We first hand every table ``min(key_floor, size)`` (scaled down
    proportionally if the floors alone exceed the budget), then distribute the
    remainder proportional to size by largest-remainder. Deterministic (sorted names).
    """
    names = sorted(sizes)
    total = sum(sizes.values())
    if total == 0 or budget <= 0:
        return {n: 0 for n in names}
    if budget >= total:
        return {n: sizes[n] for n in names}

    nonempty = [n for n in names if sizes[n] > 0]
    floors = {n: min(sizes[n], (key_floor or {}).get(n, 1) if sizes[n] else 0) for n in names}
    # ensure every non-empty table wants >= 1
    for n in nonempty:
        floors[n] = max(1, floors[n])
    floor_sum = sum(floors.values())

    if floor_sum >= budget:
        # floors alone exceed budget: allocate the budget across floors proportional to
        # each floor (largest-remainder), so the smallest dimensions still get a share.
        return _largest_remainder({n: floors[n] for n in names}, budget, cap=sizes)

    alloc = dict(floors)
    remainder = budget - floor_sum
    # distribute the rest proportional to remaining capacity (size - floor)
    spare = {n: sizes[n] - floors[n] for n in names}
    spare_total = sum(spare.values())
    if spare_total > 0 and remainder > 0:
        raw = {n: remainder * spare[n] / spare_total for n in names}
        for n in names:
            add = min(spare[n], int(raw[n]))
            alloc[n] += add
            remainder -= add
        # largest fractional remainder, capped at size
        order = sorted(
            (n for n in names if alloc[n] < sizes[n]),
            key=lambda n: (-(raw[n] - int(raw[n])), n),
        )
        i = 0
        while remainder > 0 and order:
            n = order[i % len(order)]
            if alloc[n] < sizes[n]:
                alloc[n] += 1
                remainder -= 1
            i += 1
            if i > len(order) * (budget + 1):
                break
    return alloc


def _largest_remainder(
    weights: Mapping[str, int], budget: int, cap: Mapping[str, int]
) -> dict[str, int]:
    """Apportion ``budget`` across ``weights`` (largest-remainder), capped per key."""
    names = sorted(weights)
    wtotal = sum(weights.values())
    if wtotal == 0:
        return {n: 0 for n in names}
    raw = {n: budget * weights[n] / wtotal for n in names}
    alloc = {n: min(cap[n], int(raw[n])) for n in names}
    # every weighted table gets >= 1 if budget allows
    used = sum(alloc.values())
    order = sorted(names, key=lambda n: (-(raw[n] - int(raw[n])), n))
    i = 0
    while used < budget and any(alloc[n] < cap[n] for n in names):
        n = order[i % len(order)]
        if alloc[n] < cap[n]:
            alloc[n] += 1
            used += 1
        i += 1
        if i > len(order) * (budget + 1):
            break
    return alloc


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def plan_subset(
    tables: Mapping[str, Any],
    budget: int,
    hypothesis: Optional[OntologyHypothesis] = None,
    *,
    source_id: str = "plan",
    min_join_coverage: float = MIN_JOIN_OVERLAP,
    ind_min_coverage: float = 0.95,
) -> tuple[dict[str, list[dict[str, Any]]], PlanReport]:
    """Pull a governed, budget-bounded SUBSET that preserves schema shape + joinability.

    Parameters
    ----------
    tables : ``{table_name: data}`` where data is a column mapping / pandas DataFrame
        / pyarrow Table (anything :func:`ontoforge.profiling._values.columns_of`
        accepts).
    budget : the TOTAL number of rows to keep across all tables. Apportioned across
        tables proportional to size (every non-empty table gets >= 1 row when the
        budget allows, so a join-key-bearing dimension is never starved).
    hypothesis : optional :class:`OntologyHypothesis`; when present its join-key
        columns are treated as first-class coverage targets (oversampled).

    Returns ``(subset, report)`` where ``subset`` maps each table to the kept rows as
    a list of ``{column: value}`` dicts (original row order), and ``report`` is the
    :class:`PlanReport` (per-table kept rows + why, plus cross-table joinability).

    Deterministic and keyless. Raises ``AssertionError`` only if the engine's own
    invariants break; joinability is reported (not raised) so callers can inspect a
    severed join via :meth:`PlanReport.joinability_ok` / :meth:`severed_joins`.
    """
    hyp = hypothesis or OntologyHypothesis()
    norm = _normalize(tables)
    sizes = {name: row_count_of(cols) for name, cols in norm.items()}

    # M3 profiles (real candidate keys + φ sketches) over the FULL tables, so the
    # planner reasons over genuine schema evidence, not the subset it is choosing.
    profiles: dict[str, TableProfile] = {
        name: profile_table(cols, source_id, name) for name, cols in norm.items()
    }

    # cross-table inclusion dependencies = the join evidence we must NOT sever. We
    # keep only the CREDIBLE FK-shaped INDs (a genuine many-to-one: a non-unique child
    # contained in a key-like parent) — coincidental integer/range nestings (two
    # unique key ranges that happen to overlap, the §1 "looks-similar-isn't-related"
    # trap) are NOT joins to preserve and would otherwise make the budget contract
    # unsatisfiable by demanding overlap that carries no relationship.
    inds = _credible_joins(discover_inds(norm, min_coverage=ind_min_coverage), profiles)

    # IND join-key columns are coverage targets too: keeping their distinct overlap
    # is what makes discovery still fire on the subset.
    ind_cols: dict[str, set[str]] = {}
    for ind in inds:
        ind_cols.setdefault(ind.lhs_table, set()).add(ind.lhs_column)
        ind_cols.setdefault(ind.rhs_table, set()).add(ind.rhs_column)

    # Key floor per table = the distinct-value count of its strongest JOIN key
    # (a hypothesis/IND join column). This is the number of rows the table WANTS so
    # every shared key value can be co-kept — a small dimension is then apportioned at
    # least this many rows when the budget can afford it, instead of being starved by
    # proportion. Surrogate primary keys are EXCLUDED: their values are not shared
    # across tables, so they need no cross-table coverage and must not inflate the
    # floor (a 60-row fact table's own id would otherwise crowd out a 6-row dimension).
    join_cols: dict[str, set[str]] = {}
    for name in sorted(norm):
        join_cols[name] = set(hyp.join_columns_for(name)) | set(ind_cols.get(name, set()))
    key_floor: dict[str, int] = {}
    for name in sorted(norm):
        floor = 0
        for c in join_cols[name]:
            col = norm[name].get(c)
            if col is not None:
                floor = max(floor, len(_distinct(col)))
        # cap the floor so no single table can claim more than its proportional share
        # of the budget purely from a high-cardinality join key.
        key_floor[name] = min(floor or 1, budget)

    alloc = _apportion(sizes, budget, key_floor)

    # 1) per-table schema-informed selection (edges + key coverage), independently.
    selections: dict[str, _Selection] = {}
    for name in sorted(norm):
        hyp_cols = hyp.join_columns_for(name) | frozenset(ind_cols.get(name, set()))
        selections[name] = _plan_one_table(
            source_id, name, norm[name], profiles[name], alloc.get(name, 0), hyp_cols
        )

    # 2) JOINABILITY RECONCILE: co-keep shared key values across IND edges so the
    #    subset is not a pile of silos — relationship discovery must still fire on it.
    kept_sets = {name: selections[name].kept for name in selections}
    table_budget = {name: selections[name].budget for name in selections}
    _reconcile_joins(norm, inds, kept_sets, table_budget, min_join_coverage)
    for name, sel in selections.items():
        before = len(sel.kept)
        sel.kept = kept_sets[name]
        moved = len(sel.kept) - before
        if moved:
            sel.reasons["joinability_reconcile"] = sel.reasons.get("joinability_reconcile", 0) + moved

    table_plans = [_finalize(selections[name]) for name in sorted(norm)]
    kept_idx = {tp.table: tp.kept_indices for tp in table_plans}

    overlaps = _join_overlaps(norm, inds, kept_idx, table_budget)

    report = PlanReport(
        tables=tuple(table_plans),
        overlaps=tuple(overlaps),
        total_budget=budget,
        seed=PLAN_SEED,
    )

    # build the materialized subset (original row order per table)
    subset: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(norm):
        cols = norm[name]
        col_names = list(cols)
        rows: list[dict[str, Any]] = []
        for i in kept_idx[name]:
            rows.append({c: (cols[c][i] if i < len(cols[c]) else None) for c in col_names})
        subset[name] = rows

    # ENGINE INVARIANT: never exceed the budget (the contract the founder set).
    assert report.total_kept <= budget, (
        f"plan_subset exceeded budget: kept {report.total_kept} > {budget}"
    )
    return subset, report
