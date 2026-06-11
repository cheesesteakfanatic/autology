"""M8 driver — ANVIL: by-ontology transform synthesis (§5.2, §11.2 M8).

`synthesize(df, table_profile, target_class, ontology)` runs the tiered
algorithm against the target specification (the induced/gold Ontology plus the
class's ShapeConstraints):

  T0  fix detectors (detectors.py)  — corruption taxonomy -> parameterized SQL;
  T1  constrained beam search (search.py) — residual gaps, FD/IND/Σ pruning;
  V   verification (verify.py)      — seeded 70/30 holdout Σ satisfaction +
                                      provenance equivalence via row tags;
  TX  acceptance (acceptance.py)    — spine decision; accepted -> TransformDef,
                                      ambiguous -> ledger review artifact.

Everything is deterministic for a fixed seed; no network, no model calls (T2/T3
escalation is wired through the spine but v0 runs spine-T0/T1 only — README).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import pandas as pd

from ontoforge.contracts import (
    IND,
    ClassDef,
    Datatype,
    Ontology,
    PropertyDef,
    TableProfile,
    TransformDef,
    VerificationReport,
)

from .acceptance import AcceptanceOutcome, Acceptor
from .detectors import (
    detect_column_fixes,
    detect_constant_columns,
    detect_duplicate_rows,
    detect_header_rows,
)
from .mapping import match_columns
from .program import CandidateProgram, ColumnExpr, Fix, qident
from .search import SearchStats, t1_search
from .verify import split_indices, verify_candidate

__all__ = ["Anvil", "SynthesisRun", "synthesize"]


@dataclass(slots=True)
class SynthesisRun:
    """Full evidence of one synthesize() call (tests/diagnostics)."""

    accepted: list[tuple[TransformDef, VerificationReport]] = field(default_factory=list)
    outcomes: list[AcceptanceOutcome] = field(default_factory=list)
    search_stats: Optional[SearchStats] = None
    base_program: Optional[CandidateProgram] = None
    fixes: tuple[Fix, ...] = ()


class Anvil:
    def __init__(
        self,
        *,
        seed: int = 0,
        beam: int = 8,
        depth: int = 4,
        spine=None,
        ledger=None,
    ) -> None:
        self.seed = seed
        self.beam = min(beam, 8)
        self.depth = min(depth, 4)
        self.acceptor = Acceptor(spine=spine, ledger=ledger)
        self.last_run: Optional[SynthesisRun] = None

    # ------------------------------------------------------------------ api

    def synthesize(
        self,
        df: pd.DataFrame,
        table_profile: TableProfile,
        target_class: ClassDef,
        ontology: Ontology,
        *,
        extra_tables: Optional[dict[str, pd.DataFrame]] = None,
        inds: Sequence[IND] = (),
    ) -> list[tuple[TransformDef, VerificationReport]]:
        run = SynthesisRun()
        self.last_run = run
        extra_tables = extra_tables or {}
        synth_idx, _holdout_idx = split_indices(len(df), seed=self.seed)
        df_synth = df.iloc[synth_idx].reset_index(drop=True)

        # ---------------------------------------------------------- mapping
        props = [p for p in target_class.properties if not p.is_link]
        mapping = match_columns(props, list(df.columns))

        # --------------------------------------------------------------- T0
        base = self._build_t0_program(df_synth, table_profile, target_class, props, mapping)
        run.base_program = base
        run.fixes = base.fixes

        # --------------------------------------------------------------- T1
        candidates: list[CandidateProgram] = [base] if base.columns else []
        searched, stats = t1_search(
            base,
            df_synth,
            table_profile,
            target_class,
            extra_tables=extra_tables,
            inds=inds,
            beam=self.beam,
            depth=self.depth,
        )
        run.search_stats = stats
        candidates.extend(searched)

        # ------------------------------------------------- verify + accept
        coverage_denom = max(1, len(props))
        verified: list[tuple[CandidateProgram, VerificationReport]] = []
        for prog in candidates:
            report = verify_candidate(
                prog, df, target_class, seed=self.seed, extra_tables=extra_tables
            )
            verified.append((prog, report))

        # MDL preference: best candidate per output-column signature
        verified.sort(
            key=lambda pr: (
                -pr[1].holdout_pass_rate,
                pr[1].program_complexity,
                pr[0].signature(),
            )
        )
        seen_outputs: set[tuple[str, ...]] = set()
        for prog, report in verified:
            sig = prog.output_columns()
            if sig in seen_outputs:
                continue
            seen_outputs.add(sig)
            coverage = len(sig) / coverage_denom
            outcome = self.acceptor.decide(
                prog, report, target_class, coverage=coverage,
                source_id=table_profile.source_id,
            )
            run.outcomes.append(outcome)
            if outcome.status == "accepted" and outcome.transform is not None:
                run.accepted.append((outcome.transform, report))
        return list(run.accepted)

    # -------------------------------------------------------------- helpers

    def _build_t0_program(
        self,
        df_synth: pd.DataFrame,
        table_profile: TableProfile,
        target_class: ClassDef,
        props: list[PropertyDef],
        mapping: dict[str, Optional[str]],
    ) -> CandidateProgram:
        fixes: list[Fix] = []
        columns: list[ColumnExpr] = []
        constant = {f.column for f in detect_constant_columns(table_profile)}
        fixes.extend(detect_constant_columns(table_profile))

        for prop in sorted(props, key=lambda p: p.name):
            col = mapping.get(prop.name)
            if col is None or col not in df_synth.columns or col in constant:
                continue
            profile = table_profile.columns.get(col)
            values = df_synth[col].tolist()
            col_fixes = detect_column_fixes(col, prop, profile, values) if profile else []
            # a type-producing fix (date/numeric/unit) embeds its own normalization;
            # drop the case fix when one is present (it would mangle suffix evidence),
            # and keep only the LAST type-producing fix (unit conversion subsumes
            # numeric-string parsing for its column)
            producing = [cf for cf in col_fixes if cf.produces is not None]
            if producing:
                keep = producing[-1]
                col_fixes = [
                    cf for cf in col_fixes
                    if cf.fix.kind != "case" and (cf.produces is None or cf is keep)
                ]
            expr = f"s.{qident(col)}"
            ops: list[str] = ["project"]
            produced: Optional[Datatype] = None
            for cf in col_fixes:
                expr = cf.rewrite(expr)
                ops.append(cf.fix.kind)
                fixes.append(cf.fix)
                if cf.produces is not None:
                    produced = cf.produces
            if produced is None and prop.datatype not in (Datatype.STRING, Datatype.TEXT):
                cast = {
                    Datatype.INTEGER: "BIGINT",
                    Datatype.FLOAT: "DOUBLE",
                    Datatype.DATE: "DATE",
                    Datatype.DATETIME: "TIMESTAMP",
                    Datatype.BOOLEAN: "BOOLEAN",
                }[prop.datatype]
                expr = f"TRY_CAST({expr} AS {cast})"
                ops.append("cast")
            columns.append(ColumnExpr(prop.name, expr, (col,), tuple(ops)))

        program = CandidateProgram(
            source_table=table_profile.table,
            columns=columns,
            tier="anvil:T0",
        )

        header = detect_header_rows(df_synth, [c.inputs[0] for c in columns if c.inputs])
        if header is not None:
            fix, predicate = header
            program.row_filter = predicate
            fixes.append(fix)

        # exact-duplicate detection considers WHOLE source rows (a duplicate
        # projection of distinct rows is not a duplicate row)
        dup = detect_duplicate_rows(df_synth, list(df_synth.columns))
        if dup is not None and columns:
            program.dedupe_keys = tuple(c.target for c in columns)
            fixes.append(dup)

        program.fixes = tuple(fixes)
        program.notes.extend(f.note for f in fixes if f.kind == "unit_convert")
        return program


def synthesize(
    df: pd.DataFrame,
    table_profile: TableProfile,
    target_class: ClassDef,
    ontology: Ontology,
    **kwargs,
) -> list[tuple[TransformDef, VerificationReport]]:
    """§11.2 M8 interface: synthesize(source_obj, target=O,Σ) -> [programs + reports]."""
    anvil_kwargs = {k: kwargs.pop(k) for k in ("seed", "beam", "depth", "spine", "ledger") if k in kwargs}
    return Anvil(**anvil_kwargs).synthesize(df, table_profile, target_class, ontology, **kwargs)
