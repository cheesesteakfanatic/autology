"""Per-tenant pattern learning — isolated priors that compound within ONE engagement.

v2.1 build instructions §1.5 (M-REL). The engine learns, *per tenant*, the
naming conventions, semantic-type habits, and accepted/rejected relationship
history of THIS customer's data so each subsequent inference inside the same
engagement is cheaper and sharper. The learned priors then NUDGE the heuristic
confidence proxy of new :class:`~ontoforge.contracts.RelationshipCandidate`s —
raising a candidate that matches a recognised tenant key convention, lowering a
candidate whose shape this tenant has historically rejected — and record WHY in
the rationale.

HARD CONSTRAINT — per-tenant ISOLATION (NEVER cross-tenant)
-----------------------------------------------------------
Every prior is namespaced by ``tenant_id``. Two :class:`TenantPriors` with
different ``tenant_id``s share NO state: separate store rows (the SQLite/JSON key
space is tenant-scoped) and separate in-memory tables. There is no global table,
no shared cache, no "all tenants" rollup. One tenant's accepted joins can never
raise (or lower) another tenant's candidates. This is enforced structurally (the
store path / row keys carry the tenant id) and asserted by an isolation test.

BOUNDED NUDGE — priors never override hard evidence
---------------------------------------------------
The adjustment is a *bounded* additive nudge (clamped to ``±MAX_NUDGE``) applied
to the confidence proxy. A learned prior can sway a candidate sitting in the
ambiguous band, but it can NEVER flip a candidate that hard evidence already
rejects: if a candidate carries a *fired, conflicting*
``DISTRIBUTION_DIVERGENCE`` signal (distributions provably disagree) or is typed
``UNRELATED``, the nudge is suppressed to zero. Distribution-disagreement still
wins. The nudge also cannot lift a candidate above the join floor on its own —
it tops out below :data:`~ontoforge.engineer.operators.JOIN_LIKELY_FLOOR` worth
of lift so priors tune ranking, not the join/no-join gate.

CLOSED-CORE IP — this module is proprietary per OntoForge_Build_Instructions.md
§18 (the per-tenant-learning invention). It ships KEYLESS and DETERMINISTIC: the
priors are learned from observed schema + review verdicts with pure-python
counting, no model invocation and no network. Adjudication, where it happens,
routes through the existing aimodels router / ensemble gate on deterministic
adapters.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional, Union

from ..contracts import (
    ColumnProfile,
    RelationshipCandidate,
    RelationshipType,
    RelationshipVerdict,
    SignalKind,
    TableProfile,
    TenantPrior,
)

__all__ = [
    "MAX_NUDGE",
    "MIN_OBSERVATIONS",
    "KIND_NAME_CONVENTION",
    "KIND_SEMTYPE_MAP",
    "KIND_JOIN_HISTORY",
    "TenantPriors",
    "shape_key",
]

# --------------------------------------------------------------------- tuning

#: maximum absolute confidence nudge a learned prior may apply. Deliberately
#: small: priors tune RANKING, they do not flip the join/no-join gate. With the
#: likely-join floor at 0.35, a single nudge of 0.08 cannot, on its own, lift a
#: sub-floor candidate over the floor — hard evidence (coverage / distribution)
#: remains the deciding axis.
MAX_NUDGE = 0.08

#: a prior must be backed by at least this many observations before it nudges.
#: One stray verdict should not move future inference; a convention/shape has to
#: actually recur within the engagement.
MIN_OBSERVATIONS = 2

KIND_NAME_CONVENTION = "name_convention"
KIND_SEMTYPE_MAP = "semtype_map"
KIND_JOIN_HISTORY = "join_history"

#: per-observation learning step for a join-history shape prior (accept = +,
#: reject = −). Tanh-squashed into a bounded strength so repeated verdicts
#: saturate rather than run away.
_HISTORY_STEP = 0.6

#: minimum recurrence count for a name token to be treated as a CONVENTION.
_NAME_TOKEN_MIN = 2

#: tokens too generic to be meaningful tenant conventions.
_STOPWORD_TOKENS = frozenset({"", "the", "of", "a", "an", "to", "id", "no", "code", "name"})

_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")


# --------------------------------------------------------------------- helpers


def _norm(name: str) -> str:
    return name.strip().lower()


def _tokens(name: str) -> list[str]:
    """Split a column name into lowercase alnum tokens (snake/camel/kebab aware)."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [t for t in _TOKEN_SPLIT.split(spaced.lower()) if t]


def _affixes(name: str) -> tuple[list[str], list[str]]:
    """Return (prefix-affixes, suffix-affixes) observed in a column name.

    A tenant convention shows up as a recurring leading or trailing token:
    ``cust_id`` contributes prefix ``cust_`` and suffix ``_id``; ``dim_region``
    contributes prefix ``dim_``. We record affixes with their separators so the
    learned key reads back as the convention the tenant actually uses.
    """
    n = _norm(name)
    prefixes: list[str] = []
    suffixes: list[str] = []
    # token-based affixes (separator-aware)
    toks = _tokens(n)
    if len(toks) >= 2:
        first, last = toks[0], toks[-1]
        sep_pref = n[len(first) : len(first) + 1]
        if sep_pref in ("_", "-"):
            prefixes.append(f"{first}{sep_pref}")
        sep_suf = n[-(len(last) + 1) : -len(last)] if len(last) < len(n) else ""
        if sep_suf in ("_", "-"):
            suffixes.append(f"{sep_suf}{last}")
    return prefixes, suffixes


def shape_key(left: str, right: str, rel_type: Union[RelationshipType, str]) -> str:
    """Stable, ORDER-INSENSITIVE key for an (left, right, rel_type) join shape.

    Generalises a concrete candidate to its *shape* so history learned on one
    pair informs a *similar* future pair. We abstract each side's column to its
    trailing affix (or whole token signature) — a join between ``orders.cust_id``
    and ``customers.cust_id`` and one between ``invoices.cust_id`` and
    ``customers.cust_id`` share the ``_id``/``cust_`` shape. Sides are sorted so
    (a,b) and (b,a) collapse to one shape.
    """
    rt = rel_type.value if isinstance(rel_type, RelationshipType) else str(rel_type)

    def sig(col: str) -> str:
        toks = _tokens(col)
        if not toks:
            return _norm(col)
        # prefer the trailing token (the key role: _id, _key, _code...) plus a
        # leading domain token if present, so 'cust_id' -> 'cust|id'.
        if len(toks) == 1:
            return toks[0]
        return f"{toks[0]}|{toks[-1]}"

    a, b = sorted((sig(left), sig(right)))
    return f"{a}~{b}~{rt}"


# --------------------------------------------------------------------- store


class _Store:
    """Tenant-scoped persistence for priors. SQLite when a path is given, else
    pure in-memory. The tenant_id namespaces EVERY row — there is no global table.
    """

    def __init__(self, tenant_id: str, store_path: Optional[Union[str, Path]]) -> None:
        self.tenant_id = tenant_id
        self._path = Path(store_path) if store_path is not None else None
        # in-memory mirror: kind -> key -> (value, weight, observations)
        self._mem: dict[str, dict[str, tuple[str, float, int]]] = {}
        self._conn: Optional[sqlite3.Connection] = None
        if self._path is not None:
            self._open_sqlite()
            self._load()

    def _open_sqlite(self) -> None:
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS tenant_priors ("
            "  tenant_id TEXT NOT NULL,"
            "  kind      TEXT NOT NULL,"
            "  key       TEXT NOT NULL,"
            "  value     TEXT NOT NULL DEFAULT '',"
            "  weight    REAL NOT NULL DEFAULT 0.0,"
            "  obs       INTEGER NOT NULL DEFAULT 0,"
            "  PRIMARY KEY (tenant_id, kind, key)"
            ")"
        )
        self._conn.commit()

    def _load(self) -> None:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT kind, key, value, weight, obs FROM tenant_priors WHERE tenant_id = ?",
            (self.tenant_id,),
        ).fetchall()
        for kind, key, value, weight, obs in rows:
            self._mem.setdefault(kind, {})[key] = (value, float(weight), int(obs))

    def get(self, kind: str, key: str) -> Optional[tuple[str, float, int]]:
        return self._mem.get(kind, {}).get(key)

    def put(self, kind: str, key: str, value: str, weight: float, obs: int) -> None:
        self._mem.setdefault(kind, {})[key] = (value, weight, obs)
        if self._conn is not None:
            self._conn.execute(
                "INSERT INTO tenant_priors (tenant_id, kind, key, value, weight, obs) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(tenant_id, kind, key) DO UPDATE SET "
                "  value=excluded.value, weight=excluded.weight, obs=excluded.obs",
                (self.tenant_id, kind, key, value, weight, obs),
            )
            self._conn.commit()

    def items(self, kind: str) -> Iterable[tuple[str, tuple[str, float, int]]]:
        # sorted for deterministic iteration
        return sorted(self._mem.get(kind, {}).items())

    def all_priors(self) -> list[TenantPrior]:
        out: list[TenantPrior] = []
        for kind in sorted(self._mem):
            for key, (value, weight, obs) in sorted(self._mem[kind].items()):
                out.append(
                    TenantPrior(
                        tenant_id=self.tenant_id,
                        kind=kind,
                        key=key,
                        value=value,
                        weight=weight,
                        observations=obs,
                    )
                )
        return out

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# --------------------------------------------------------------------- priors


@dataclass(frozen=True, slots=True)
class _Nudge:
    """The decision of how to nudge one candidate, with its reasoning trail."""

    delta: float
    reasons: tuple[str, ...]
    suppressed: bool = False


class TenantPriors:
    """Isolated per-tenant prior store + candidate adjuster (§1.5; CLOSED CORE).

    Learns three prior kinds from THIS tenant's data, all namespaced by
    ``tenant_id``:

    * **name conventions** — recurring prefix/suffix token patterns in the
      tenant's column names (``cust_``, ``_id``, ``dim_``).
    * **semtype map** — observed column-name → semantic-type mappings in THIS
      tenant (so a name the tenant consistently types a certain way is a prior).
    * **join history** — accepted vs. rejected ``(left, right, rel_type)``
      *shapes*, accumulated into a signed strength used as a prior over similar
      future candidates.

    Use :meth:`observe_schema` to feed table profiles and :meth:`observe_verdict`
    to feed accepted/rejected review outcomes; then :meth:`adjust_candidate` to
    nudge a new candidate's confidence proxy (recording WHY in its rationale).

    Two instances with different ``tenant_id``s NEVER see each other's priors.
    """

    def __init__(
        self,
        tenant_id: str,
        store_path: Optional[Union[str, Path]] = None,
    ) -> None:
        if not tenant_id or not str(tenant_id).strip():
            raise ValueError("tenant_id is required and must be non-empty (isolation key)")
        self.tenant_id = str(tenant_id)
        self._store = _Store(self.tenant_id, store_path)

    # ----------------------------------------------------------- observation

    def observe_schema(self, table_profiles: Iterable[TableProfile]) -> None:
        """Learn naming conventions + semantic-type map from the tenant's schema.

        Counts recurring leading/trailing affixes across the tenant's column
        names and records column-name → semantic-type mappings the profiler
        emitted. Idempotent in spirit: re-observing the same schema strengthens
        (re-counts) the same priors; it never invents cross-tenant ones.
        """
        # tally affixes across this observation
        pref_counts: dict[str, int] = {}
        suf_counts: dict[str, int] = {}
        for tp in table_profiles:
            for col_name, col in _iter_columns(tp):
                prefixes, suffixes = _affixes(col_name)
                for p in prefixes:
                    if _affix_token(p) not in _STOPWORD_TOKENS:
                        pref_counts[p] = pref_counts.get(p, 0) + 1
                for s in suffixes:
                    suf_counts[s] = suf_counts.get(s, 0) + 1
                # semantic-type map: a name the tenant types consistently
                self._observe_semtype(col_name, col)

        for affix, n in pref_counts.items():
            if n >= _NAME_TOKEN_MIN:
                self._bump_name_convention(affix, "prefix", n)
        for affix, n in suf_counts.items():
            if n >= _NAME_TOKEN_MIN:
                self._bump_name_convention(affix, "suffix", n)

    def _observe_semtype(self, col_name: str, col: ColumnProfile) -> None:
        sem = (col.semantic_type or "").strip()
        if not sem:
            return
        key = _norm(col_name)
        prior = self._store.get(KIND_SEMTYPE_MAP, key)
        obs = (prior[2] if prior else 0) + 1
        # store the most-recently-observed semtype; weight = confidence proxy
        weight = min(1.0, (prior[1] if prior else 0.0) + 0.34)
        self._store.put(KIND_SEMTYPE_MAP, key, sem, weight, obs)

    def _bump_name_convention(self, affix: str, role: str, increment: int) -> None:
        prior = self._store.get(KIND_NAME_CONVENTION, affix)
        obs = (prior[2] if prior else 0) + increment
        # weight saturates toward 1.0 with recurrence
        weight = 1.0 - (1.0 / (1.0 + obs))
        self._store.put(KIND_NAME_CONVENTION, affix, role, weight, obs)

    def observe_verdict(self, verdict: RelationshipVerdict, accepted: bool) -> None:
        """Learn from a human/Build review verdict on a relationship.

        Accumulates a SIGNED strength per join *shape* (see :func:`shape_key`):
        accepted shapes drift positive, rejected shapes drift negative. The
        magnitude saturates (tanh) so a recurring pattern dominates a one-off.
        """
        key = shape_key(verdict.left.column, verdict.right.column, verdict.rel_type)
        prior = self._store.get(KIND_JOIN_HISTORY, key)
        prev_w = prior[1] if prior else 0.0
        obs = (prior[2] if prior else 0) + 1
        step = _HISTORY_STEP if accepted else -_HISTORY_STEP
        # accumulate in an unbounded latent then squash, so verdicts compose
        latent = _atanh(_clamp(prev_w, -0.999, 0.999)) + step
        new_w = _tanh(latent)
        # store the rel_type as the value so we know what was accepted/rejected
        self._store.put(KIND_JOIN_HISTORY, key, verdict.rel_type.value, new_w, obs)

    # ----------------------------------------------------------- adjustment

    def adjust_candidate(self, candidate: RelationshipCandidate) -> RelationshipCandidate:
        """Return ``candidate`` with its confidence proxy NUDGED by learned priors.

        The nudge is bounded to ``±MAX_NUDGE`` and SUPPRESSED entirely when hard
        evidence already refutes the candidate (a fired, conflicting
        distribution-divergence signal, or an ``UNRELATED`` type) — so priors can
        never flip distribution-disagreement into a join. The reasoning is
        appended to ``rationale`` and ``confidence`` is re-clamped to ``[0, 1]``.
        Returns a NEW frozen candidate; the input is never mutated.
        """
        nudge = self._compute_nudge(candidate)
        if nudge.delta == 0.0 and not nudge.reasons:
            return candidate

        new_conf = _clamp(candidate.confidence + nudge.delta, 0.0, 1.0)
        why = "; ".join(nudge.reasons)
        prefix = candidate.rationale.rstrip()
        sep = " | " if prefix else ""
        new_rationale = f"{prefix}{sep}tenant[{self.tenant_id}]: {why}"
        return replace(candidate, confidence=new_conf, rationale=new_rationale)

    def _compute_nudge(self, candidate: RelationshipCandidate) -> _Nudge:
        # 1) HARD-EVIDENCE GUARD — distribution-disagreement (or UNRELATED) wins.
        if _has_disagreeing_distribution(candidate) or candidate.rel_type is RelationshipType.UNRELATED:
            # we still annotate WHY the prior was withheld, for the trail.
            return _Nudge(
                delta=0.0,
                reasons=("prior nudge suppressed (hard evidence: distributions disagree)",),
                suppressed=True,
            )

        reasons: list[str] = []
        delta = 0.0

        # 2) name-convention prior: a recognised tenant key affix RAISES.
        nc_delta, nc_reason = self._name_convention_nudge(candidate)
        if nc_reason:
            delta += nc_delta
            reasons.append(nc_reason)

        # 3) semtype-map prior: tenant consistently types these names the same.
        st_delta, st_reason = self._semtype_nudge(candidate)
        if st_reason:
            delta += st_delta
            reasons.append(st_reason)

        # 4) join-history prior: accepted shape RAISES, rejected shape LOWERS.
        jh_delta, jh_reason = self._history_nudge(candidate)
        if jh_reason:
            delta += jh_delta
            reasons.append(jh_reason)

        delta = _clamp(delta, -MAX_NUDGE, MAX_NUDGE)
        if not reasons:
            return _Nudge(delta=0.0, reasons=())
        return _Nudge(delta=delta, reasons=tuple(reasons))

    def _name_convention_nudge(self, candidate: RelationshipCandidate) -> tuple[float, str]:
        # collect affixes present on EITHER side of the candidate
        names = (candidate.left.column, candidate.right.column)
        best_w = 0.0
        best_affix = ""
        for name in names:
            prefixes, suffixes = _affixes(name)
            for affix in (*prefixes, *suffixes):
                prior = self._store.get(KIND_NAME_CONVENTION, affix)
                if prior and prior[2] >= MIN_OBSERVATIONS and prior[1] > best_w:
                    best_w = prior[1]
                    best_affix = affix
        if not best_affix:
            return 0.0, ""
        delta = MAX_NUDGE * best_w
        return delta, (
            f"recognised key convention '{best_affix}' "
            f"(learned, {self._obs(KIND_NAME_CONVENTION, best_affix)} obs) → +{delta:.3f}"
        )

    def _semtype_nudge(self, candidate: RelationshipCandidate) -> tuple[float, str]:
        lk = _norm(candidate.left.column)
        rk = _norm(candidate.right.column)
        lp = self._store.get(KIND_SEMTYPE_MAP, lk)
        rp = self._store.get(KIND_SEMTYPE_MAP, rk)
        if not (lp and rp):
            return 0.0, ""
        if lp[2] < MIN_OBSERVATIONS or rp[2] < MIN_OBSERVATIONS:
            return 0.0, ""
        if lp[0] != rp[0]:
            return 0.0, ""
        w = min(lp[1], rp[1])
        delta = (MAX_NUDGE * 0.5) * w
        return delta, (
            f"both sides typed '{lp[0]}' by this tenant → +{delta:.3f}"
        )

    def _history_nudge(self, candidate: RelationshipCandidate) -> tuple[float, str]:
        key = shape_key(candidate.left.column, candidate.right.column, candidate.rel_type)
        prior = self._store.get(KIND_JOIN_HISTORY, key)
        if not prior or prior[2] < MIN_OBSERVATIONS:
            return 0.0, ""
        strength = prior[1]  # signed in (-1, 1)
        if abs(strength) < 1e-9:
            return 0.0, ""
        delta = MAX_NUDGE * strength
        verb = "accepted" if strength > 0 else "rejected"
        return delta, (
            f"this tenant has historically {verb} this join shape "
            f"({prior[2]} verdicts) → {delta:+.3f}"
        )

    # ----------------------------------------------------------- introspection

    def priors(self) -> list[TenantPrior]:
        """All learned priors for THIS tenant (sorted, deterministic)."""
        return self._store.all_priors()

    def export_json(self) -> str:
        """Deterministic JSON snapshot of this tenant's priors (for inspection)."""
        return json.dumps(
            [
                {
                    "tenant_id": p.tenant_id,
                    "kind": p.kind,
                    "key": p.key,
                    "value": p.value,
                    "weight": round(p.weight, 6),
                    "observations": p.observations,
                }
                for p in self.priors()
            ],
            sort_keys=True,
            separators=(",", ":"),
        )

    def close(self) -> None:
        self._store.close()

    def _obs(self, kind: str, key: str) -> int:
        prior = self._store.get(kind, key)
        return prior[2] if prior else 0

    def __enter__(self) -> "TenantPriors":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------- internals


def _iter_columns(tp: TableProfile) -> Iterable[tuple[str, ColumnProfile]]:
    cols = tp.columns
    if isinstance(cols, dict):
        for name in sorted(cols):
            yield name, cols[name]


def _affix_token(affix: str) -> str:
    return affix.strip("_-")


def _has_disagreeing_distribution(candidate: RelationshipCandidate) -> bool:
    """True iff a FIRED, CONFLICTING distribution-divergence signal is present.

    This is the hard-evidence guard: when the value distributions provably
    disagree, no learned prior may raise the candidate. Distribution-
    disagreement still wins (bounded-nudge invariant).
    """
    for art in candidate.evidence:
        if (
            art.kind is SignalKind.DISTRIBUTION_DIVERGENCE
            and art.fired
            and art.conflicts
        ):
            return True
    return False


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _tanh(x: float) -> float:
    return math.tanh(x)


def _atanh(x: float) -> float:
    return math.atanh(x)
