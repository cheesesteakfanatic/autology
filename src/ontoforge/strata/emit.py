"""Ontology emission: admitted concepts -> contracts.Ontology (§3.4 Output, §3.5).

- Class URIs are intent-hash stable (contracts.class_uri_from_intent), so
  re-induction on permuted input yields identical URIs.
- Parents come from the transitively-reduced lattice order restricted to
  admitted concepts (multiple inheritance allowed — FCA's native strength).
- Properties come from the concept's distinguishing has-prop intent (own
  attributes minus everything inherited from admitted ancestors); datatype /
  dimension / unit are aggregated from the member ColumnProfiles, and
  functional=True exactly when an exact FD (candidate key -> column) exists.
- Link properties arise from (a) INDs whose rhs is an admitted class's key
  column, (b) G-decomp lhs columns (the synthesized 3NF foreign key), and
  (c) membership of a column in an admitted G-join hub domain.
- ShapeConstraints are compiled from profile statistics: null rate ->
  min_count, stable code-like format signatures -> regex pattern, numeric
  sketch quantiles -> value ranges.
- EVENT detection (§3.5): >= 1 timestamp-dimension property + >= 2 link
  properties + append-mostly CDC behavior on the evidence tables.

Naming happens here (not during admission) so the assigned names are a
deterministic function of the FINAL admitted set: base names come from the
memoized ``strata.name_concept`` task (intent-hash memo => re-induction
reuses names; renames only via TEMPER), collision suffixes are assigned in
intent-hash order (input-permutation stable).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from ontoforge.contracts import (
    IND,
    ClassDef,
    Datatype,
    ModelClient,
    ModelRequest,
    Ontology,
    PropertyDef,
    ShapeConstraint,
    TableProfile,
    class_uri_from_intent,
    leaf,
    property_uri,
    prov_sum,
)

from .admission import (
    NAME_TASK,
    _NAME_SCHEMA,
    AdmissionResult,
    NameMemo,
    register_evidence_atoms,
)
from .candidates import HUB_MIN_COVERAGE
from .context import FormalContext, is_code_like, is_timestampish
from .lattice import Concept, ConceptLattice

__all__ = ["emit_ontology", "concept_name_payload"]

#: null-rate ceiling for asserting min_count=1 in a shape
MIN_COUNT_NULL_RATE = 0.005


def _is_live(client: Optional[ModelClient]) -> bool:
    """True iff the resolved ``client`` is a LIVE model (a ``_RoutedClient`` with
    a wired live adapter). Reads ONLY the public ``activation.model_status`` over
    the ``.active`` attribute resolve_client attaches — a bare HeuristicAdapter /
    CassetteAdapter / explicitly-injected deterministic client is False. Any
    failure (e.g. an import edge) degrades to False (keyless/deterministic)."""
    try:
        from ontoforge.aimodels.activation import model_status

        return bool(model_status(client).live)
    except Exception:  # noqa: BLE001 — never let the live-check break the pipeline
        return False


def _render_prompt_for(client: Optional[ModelClient], task: str, payload_prompt: str,
                       grounding: Optional[str] = None) -> str:
    """Parity-safe prompt selection.

    Keyless/deterministic: returns ``payload_prompt`` UNCHANGED — the exact bytes
    the HeuristicAdapter/CassetteAdapter handlers parse (here, whole-prompt JSON).
    Live: wraps that SAME structured payload in the rich, versioned PromptLibrary
    template for ``task`` (instruction framing + ontology grounding + few-shot),
    with the verbatim ``payload_prompt`` as the INPUT slot. Any failure (import,
    missing template) degrades to ``payload_prompt`` — selection NEVER raises."""
    if not _is_live(client):
        return payload_prompt
    try:
        from ontoforge.aimodels.library import PromptLibrary

        return PromptLibrary().get(task).render(user_input=payload_prompt, grounding=grounding)
    except Exception:  # noqa: BLE001 — never let prompt selection break the pipeline
        return payload_prompt


#: model_id the deterministic HeuristicAdapter stamps on every response. When a
#: live propose degrades to this fallback, it ran on whatever prompt we sent — so
#: we must guarantee that prompt was the BARE one (parity), never the rich one.
_DETERMINISTIC_MODEL_ID = "heuristic"


def _propose_richly(
    client: ModelClient,
    *,
    task: str,
    payload_prompt: str,
    schema: str,
    grounding: Optional[str],
    temperature: float = 0.0,
    max_tokens: int = 1024,
):
    """Propose with the rich prompt on the LIVE path, BARE prompt keyless —
    preserving the parity-sacred fall-through.

    The resolved live client (``_RoutedClient``) routes a single ``req`` to BOTH a
    live adapter AND a deterministic fallback that re-parses ``req.prompt``. The
    keyless handlers parse the CURRENT minimal prompt — a rich prompt either crashes
    them (whole-prompt-is-JSON) or makes them silently misparse (spine's brace
    slicing). So a live failure/cassette-miss on a RICH request must NEVER let a
    deterministic handler run on the rich bytes.

    Mechanism: (keyless) send the BARE request directly. (live) try the RICH
    request; if it RAISES (whole-prompt-JSON fallback can't parse rich) OR the
    response came from the DETERMINISTIC fallback (``model_id == 'heuristic'`` — it
    degraded and ran on the rich prompt, untrustworthy) we re-issue the BARE request
    so the deterministic verdict is computed on the byte-identical current prompt.
    The rich prompt is only ever CONSUMED by a genuine live model; deterministic
    behavior is always byte-identical to today."""
    bare_req = ModelRequest(
        task=task, prompt=payload_prompt, schema=schema,
        temperature=temperature, max_tokens=max_tokens,
    )
    if not _is_live(client):
        return client.propose(bare_req)
    rich_prompt = _render_prompt_for(client, task, payload_prompt, grounding)
    if rich_prompt == payload_prompt:  # no rich template (e.g. unknown task)
        return client.propose(bare_req)
    rich_req = ModelRequest(
        task=task, prompt=rich_prompt, schema=schema,
        temperature=temperature, max_tokens=max_tokens,
    )
    try:
        resp = client.propose(rich_req)
    except Exception:  # noqa: BLE001 — deterministic fallback crashed on rich; degrade to bare
        return client.propose(bare_req)
    if getattr(resp, "model_id", "") == _DETERMINISTIC_MODEL_ID:
        # the live leg degraded to the deterministic fallback (which ran on the
        # rich prompt): recompute on the BARE prompt for byte-identical parity.
        return client.propose(bare_req)
    return resp


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------


def _distinguishing_props(
    concept: Concept,
    lattice: ConceptLattice,
    admitted: Mapping[str, Any],
    ctx: FormalContext,
) -> list[str]:
    """Own has-prop canonical names (intent minus admitted-ancestor intents),
    ordered by rarity (most type-specific first) then name."""
    inherited: frozenset[str] = frozenset()
    for h in lattice.ancestors(concept.intent_hash):
        if h in admitted:
            inherited |= lattice.concepts[h].intent
    own = concept.intent - inherited
    props = [a.split(":", 1)[1] for a in own if a.startswith("has-prop:")]
    rarity = {p: len(ctx.attr_extent(f"has-prop:{p}")) for p in props}
    return sorted(props, key=lambda p: (rarity[p], p))


def concept_name_payload(
    concept: Concept,
    lattice: ConceptLattice,
    admitted: Mapping[str, Any],
    ctx: FormalContext,
) -> dict[str, Any]:
    """The T2 naming-task payload: dominant candidate hint + distinguishing
    attributes (deterministic; serialized sorted)."""
    object_hint = ""
    cands = sorted(
        (ctx.candidates[g] for g in concept.extent if g in ctx.candidates),
        key=lambda c: c.cid,
    )
    for cand in cands:
        if ctx.objects[cand.cid] == concept.intent:
            object_hint = cand.name_hint
            break
    tables = sorted(
        {t for g in concept.extent if g in ctx.candidates for t in ctx.candidates[g].evidence_tables}
    )
    return {
        "task": NAME_TASK,
        "intent_hash": concept.intent_hash,
        "object_hint": object_hint,
        "distinguishing_props": _distinguishing_props(concept, lattice, admitted, ctx),
        "event_like": "has-timestamp" in concept.intent,
        "tables": tables,
        "support": concept.support,
    }


def _name_grounding(payload: Mapping[str, Any]) -> str:
    """Deterministic ontology-grounding subset for the live naming prompt:
    the distinguishing properties, the candidate hint, event flag, and the
    evidence tables this concept is induced over. (Used ONLY on the live path;
    the keyless prompt is the bare payload JSON and never sees this.)"""
    props = ", ".join(str(p) for p in payload.get("distinguishing_props", [])) or "(none)"
    tables = ", ".join(str(t) for t in payload.get("tables", [])) or "(none)"
    hint = payload.get("object_hint") or "(none)"
    lines = [
        f"distinguishing properties: {props}",
        f"candidate name hint: {hint}",
        f"event_like: {bool(payload.get('event_like'))}",
        f"evidence tables: {tables}",
    ]
    return "\n".join(lines)


def _resolve_names(
    admitted_hashes: Sequence[str],
    lattice: ConceptLattice,
    admitted: Mapping[str, Any],
    ctx: FormalContext,
    client: Optional[ModelClient],
    memo: NameMemo,
    ledger: Any,
    profiles_by_table: Mapping[str, TableProfile],
) -> dict[str, tuple[str, str]]:
    """Base names via memo/ModelClient; collision suffixes in intent-hash
    order (permutation-stable)."""
    base: dict[str, tuple[str, str]] = {}
    for ih in sorted(admitted_hashes):
        cached = memo.get(ih)
        if cached is not None:
            base[ih] = cached
            continue
        concept = lattice.concepts[ih]
        payload = concept_name_payload(concept, lattice, admitted, ctx)
        if client is None:
            name = payload["object_hint"] or "Concept"
            definition = ""
        else:
            # Deterministic 'payload prompt' (the exact bytes the keyless handler
            # parses with json.loads on the WHOLE prompt). On the LIVE path this is
            # wrapped in the rich library template; keyless it is sent unchanged.
            payload_prompt = json.dumps(payload, sort_keys=True)
            resp = _propose_richly(
                client,
                task=NAME_TASK,
                payload_prompt=payload_prompt,
                schema=_NAME_SCHEMA,
                grounding=_name_grounding(payload),
                temperature=0.0,
                max_tokens=256,
            )
            parsed = resp.parsed if isinstance(resp.parsed, dict) else {}
            name = str(parsed.get("name") or "Concept")
            definition = str(parsed.get("definition") or "")
        cands = [ctx.candidates[g] for g in concept.extent if g in ctx.candidates]
        prov_atoms = register_evidence_atoms(ledger, cands, profiles_by_table)
        memo.put(ih, name, definition, prov_atoms)
        base[ih] = (name, definition)

    # deterministic collision suffixes
    out: dict[str, tuple[str, str]] = {}
    used: Counter[str] = Counter()
    for ih in sorted(admitted_hashes):
        name, definition = base[ih]
        used[name] += 1
        if used[name] > 1:
            name = f"{name}{used[name]}"
        out[ih] = (name, definition)
    return out


# ---------------------------------------------------------------------------
# property assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PropEvidence:
    canonical: str
    columns: tuple[tuple[str, str], ...]   # member (table, column) coordinates


def _prop_columns(
    canonical: str,
    concept: Concept,
    ctx: FormalContext,
) -> tuple[tuple[str, str], ...]:
    """Member columns of the concept's extent candidates mapping to this
    canonical property name."""
    clusters = ctx.clusters
    cols: set[tuple[str, str]] = set()
    for g in concept.extent:
        cand = ctx.candidates.get(g)
        if cand is None:
            continue
        for table, column in cand.member_columns:
            if clusters is not None and clusters.canonical_of(table, column) == canonical:
                cols.add((table, column))
    return tuple(sorted(cols))


def _majority_datatype(cols: Sequence[tuple[str, str]], profiles: Mapping[str, TableProfile]) -> Datatype:
    votes = Counter()
    for t, c in cols:
        tp = profiles.get(t)
        if tp and c in tp.columns:
            votes[tp.columns[c].inferred_type] += 1
    if not votes:
        return Datatype.STRING
    return max(votes, key=lambda d: (votes[d], d.value))


def _majority_unit_dim(cols, profiles):
    dims = Counter()
    units = Counter()
    for t, c in cols:
        tp = profiles.get(t)
        if tp and c in tp.columns:
            cp = tp.columns[c]
            if cp.dimension is not None and not cp.dimension.dimensionless:
                dims[cp.dimension] += 1
            if cp.unit:
                units[cp.unit] += 1
    dim = max(dims, key=lambda d: (dims[d], str(d))) if dims else None
    unit = max(units, key=lambda u: (units[u], u)) if units else None
    return dim, unit


def _is_functional(
    cols: Sequence[tuple[str, str]],
    concept: Concept,
    ctx: FormalContext,
    profiles: Mapping[str, TableProfile],
) -> bool:
    """functional=True when an exact FD (candidate key -> column) exists in
    the evidence (or the column IS part of the key)."""
    for g in concept.extent:
        cand = ctx.candidates.get(g)
        if cand is None:
            continue
        key_by_table: dict[str, set[str]] = {}
        for t, c in cand.key_columns:
            key_by_table.setdefault(t, set()).add(c)
        for t, c in cols:
            keys = key_by_table.get(t)
            if not keys:
                continue
            if c in keys:
                return True
            tp = profiles.get(t)
            if tp is None:
                continue
            for fd in tp.fds:
                if fd.rhs == c and fd.confidence >= 1.0 and set(fd.lhs) <= keys:
                    return True
    return False


def _shape_for(
    prop: PropertyDef,
    cols: Sequence[tuple[str, str]],
    profiles: Mapping[str, TableProfile],
) -> ShapeConstraint:
    cps = [
        profiles[t].columns[c]
        for t, c in cols
        if t in profiles and c in profiles[t].columns
    ]
    max_null = max((cp.null_rate for cp in cps), default=1.0)
    pattern = None
    sigs = {cp.format_signature for cp in cps}
    if len(sigs) == 1 and cps and is_code_like(cps[0].format_signature):
        # regenerate a regex from the (shared-signature) samples
        from ontoforge.profiling import generalize, to_regex

        samples = [s for cp in cps for s in cp.sample_values if s]
        if samples:
            pattern = f"^{to_regex(generalize(samples))}$"
    min_v = max_v = None
    if prop.datatype in (Datatype.INTEGER, Datatype.FLOAT):
        lows = [cp.quantiles[0] for cp in cps if cp.quantiles]
        highs = [cp.quantiles[-1] for cp in cps if cp.quantiles]
        if lows and highs:
            min_v, max_v = min(lows), max(highs)
    return ShapeConstraint(
        prop=prop.name,
        min_count=1 if max_null <= MIN_COUNT_NULL_RATE else 0,
        max_count=1,
        datatype=None if prop.is_link else prop.datatype,
        pattern=pattern,
        min_value=min_v,
        max_value=max_v,
        unit=prop.unit,
    )


# ---------------------------------------------------------------------------
# link discovery
# ---------------------------------------------------------------------------


def _class_of_candidate(
    cid: str,
    ctx: FormalContext,
    lattice: ConceptLattice,
    admission: AdmissionResult,
) -> Optional[str]:
    """Admitted concept (intent hash) representing a candidate: its object
    concept if admitted, else the merge target / nearest admitted ancestor."""
    from .context import intent_hash_of

    ih = intent_hash_of(ctx.objects[cid])
    seen: set[str] = set()
    while ih is not None and ih not in seen:
        seen.add(ih)
        if ih in admission.admitted:
            return ih
        nxt = admission.merged.get(ih)
        if nxt is None:
            concept = lattice.concepts.get(ih)
            if concept is None:
                return None
            anc = [h for h in lattice.ancestors(ih) if h in admission.admitted]
            if not anc:
                return None
            return min(anc, key=lambda h: (lattice.concepts[h].support, h))
        ih = nxt
    return None


def _link_targets(
    ctx: FormalContext,
    lattice: ConceptLattice,
    admission: AdmissionResult,
    inds: Sequence[IND],
) -> dict[tuple[str, str], str]:
    """(table, column) -> admitted concept hash this column links to.

    Sources of link evidence, in priority order:
    1. IND whose rhs is the key column of an admitted candidate's class;
    2. G-decomp lhs columns (the synthesized 3NF foreign key) link the host
       table's columns to the decomp class;
    3. membership in an admitted G-join hub domain.
    """
    targets: dict[tuple[str, str], str] = {}
    key_of: dict[tuple[str, str], str] = {}     # key (table, col) -> candidate cid
    for cid, cand in ctx.candidates.items():
        for t, c in cand.key_columns:
            key_of.setdefault((t, c), cid)

    # (3) hub membership (lowest priority: write first, allow overwrite)
    for cid, cand in ctx.candidates.items():
        if cand.kind != "g-join":
            continue
        target = _class_of_candidate(cid, ctx, lattice, admission)
        if target is None:
            continue
        for t, c in cand.member_columns:
            if (t, c) not in cand.key_columns:
                targets[(t, c)] = target
            else:
                targets.setdefault((t, c), target)

    # (2) decomp foreign keys: host-table column = decomp key column
    for cid, cand in ctx.candidates.items():
        if cand.kind != "g-decomp":
            continue
        target = _class_of_candidate(cid, ctx, lattice, admission)
        if target is None:
            continue
        for t, c in cand.key_columns:
            targets[(t, c)] = target

    # (1) INDs into admitted candidate keys (highest priority)
    for ind in inds:
        if ind.coverage < HUB_MIN_COVERAGE or ind.lhs_table == ind.rhs_table:
            continue
        cid = key_of.get((ind.rhs_table, ind.rhs_column))
        if cid is None:
            continue
        target = _class_of_candidate(cid, ctx, lattice, admission)
        if target is not None:
            targets[(ind.lhs_table, ind.lhs_column)] = target
    return targets


# ---------------------------------------------------------------------------
# the emitter
# ---------------------------------------------------------------------------


def emit_ontology(
    ctx: FormalContext,
    lattice: ConceptLattice,
    admission: AdmissionResult,
    profiles: Sequence[TableProfile],
    inds: Sequence[IND] = (),
    *,
    client: Optional[ModelClient] = None,
    ledger: Any = None,
    memo: Optional[NameMemo] = None,
    version: int = 1,
) -> Ontology:
    """Materialize the admitted concept set as a contracts.Ontology."""
    profiles_by_table = {tp.table: tp for tp in profiles}
    memo = memo if memo is not None else NameMemo(ledger)
    admitted_hashes = sorted(admission.admitted)
    names = _resolve_names(
        admitted_hashes, lattice, admission.admitted, ctx,
        client, memo, ledger, profiles_by_table,
    )
    link_targets = _link_targets(ctx, lattice, admission, inds)
    uri_of = {ih: class_uri_from_intent(ih) for ih in admitted_hashes}

    onto = Ontology(version=version)
    for ih in admitted_hashes:
        ac = admission.admitted[ih]
        concept = ac.concept
        name, definition = names[ih]
        ac.name, ac.definition = name, definition

        # parents: transitive reduction over admitted ancestors
        anc = [h for h in lattice.ancestors(ih) if h in admission.admitted]
        parents = [
            p for p in anc
            if not any(
                z != p and z in anc
                and lattice.concepts[z].extent < lattice.concepts[p].extent
                and lattice.concepts[ih].extent < lattice.concepts[z].extent
                for z in anc
            )
        ]
        inherited: frozenset[str] = frozenset()
        for h in anc:
            inherited |= lattice.concepts[h].intent

        own_props = sorted(
            a.split(":", 1)[1]
            for a in (concept.intent - inherited)
            if a.startswith("has-prop:")
        )
        c_uri = uri_of[ih]
        props: list[PropertyDef] = []
        shapes: list[ShapeConstraint] = []
        n_ts = 0
        for pname in own_props:
            cols = _prop_columns(pname, concept, ctx)
            if not cols:
                continue
            link_hash = None
            for col in cols:
                if col in link_targets and link_targets[col] != ih:
                    link_hash = link_targets[col]
                    break
            dtype = _majority_datatype(cols, profiles_by_table)
            dim, unit = _majority_unit_dim(cols, profiles_by_table)
            cps = [
                profiles_by_table[t].columns[c]
                for t, c in cols
                if t in profiles_by_table and c in profiles_by_table[t].columns
            ]
            if any(is_timestampish(cp) for cp in cps):
                n_ts += 1
            synonyms = tuple(sorted({c for _, c in cols}))
            prop = PropertyDef(
                uri=property_uri(c_uri, pname),
                name=pname,
                datatype=dtype,
                is_link=link_hash is not None,
                range_class=uri_of.get(link_hash) if link_hash is not None else None,
                dimension=dim,
                unit=unit,
                cardinality="one",
                functional=_is_functional(cols, concept, ctx, profiles_by_table),
                synonyms=synonyms,
            )
            props.append(prop)
            shapes.append(_shape_for(prop, cols, profiles_by_table))

        # §3.5 event rule: timestamp + >=2 link props (own+inherited) + append-mostly.
        # Inherited links are counted from the FULL intent (own + ancestor attrs)
        # so emission order over admitted classes cannot matter.
        has_ts = "has-timestamp" in concept.intent or n_ts > 0
        n_links = 0
        for attr in sorted(concept.intent):
            if not attr.startswith("has-prop:"):
                continue
            pname = attr.split(":", 1)[1]
            cols = _prop_columns(pname, concept, ctx)
            if any(col in link_targets and link_targets[col] != ih for col in cols):
                n_links += 1
        tables = {
            t
            for g in concept.extent
            if g in ctx.candidates
            for t in ctx.candidates[g].evidence_tables
        }
        append_mostly = any(
            profiles_by_table[t].append_mostly for t in tables if t in profiles_by_table
        )
        is_event = bool(has_ts and n_links >= 2 and append_mostly)

        prov_ref = f"strata://intent/{ih}"
        if ledger is not None:
            cands = [ctx.candidates[g] for g in concept.extent if g in ctx.candidates]
            atom_ids = register_evidence_atoms(ledger, cands, profiles_by_table)
            if atom_ids:
                prov_ref = ledger.intern(prov_sum([leaf(a) for a in atom_ids]))

        onto.add(
            ClassDef(
                uri=c_uri,
                name=name,
                parents=tuple(sorted(uri_of[p] for p in parents)),
                properties=tuple(props),
                shapes=tuple(shapes),
                definition=definition,
                intent_hash=ih,
                is_event=is_event,
                confidence=ac.decision.confidence,
                prov_ref=prov_ref,
            )
        )
    return onto
