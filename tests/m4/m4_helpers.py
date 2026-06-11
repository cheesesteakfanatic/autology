"""Shared M4 test helpers: gold comparator + synthetic micro estate.

Gold comparator (whitepaper §11.2 M4 "hero gold-ontology comparator")
---------------------------------------------------------------------
Best-match scoring between induced and gold classes over three evidence
channels, each computed from artifacts only (no hand-built answer key):

- property overlap (weight .40): F1 of fuzzy property-name matching between
  the induced concept's full intent properties and the gold class's
  properties+synonyms; two names match when their normalized-token Jaccard
  is >= .5;
- member-table overlap (weight .35): Jaccard between the induced concept's
  evidence tables and the gold class's associated tables, the latter DERIVED
  by matching gold property names against estate column names (skipped, with
  weight renormalization, for purely abstract gold classes that ground in no
  table);
- class-name similarity (weight .25): normalized-token Jaccard.

A class is matched when its best counterpart scores >= MATCH_THETA. Matching
is best-match per side (n:m), not injective: faa_master legitimately carries
BOTH gold Aircraft and gold Registration (their extents coincide 1:1 in this
estate), so forcing injectivity would punish a correct induction.

Hierarchy edges are scored on matched classes with ancestor-closure credit:
an induced edge is correct when the gold image of the parent is a gold
ancestor-or-equal of the gold image of the child, and a gold edge is
recovered when the induced image of the parent is an induced ancestor-or-
equal of the induced image of the child.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ontoforge.contracts import Ontology
from ontoforge.strata import StrataResult
from ontoforge.strata._norm import name_tokens, token_jaccard

MATCH_THETA = 0.4
W_PROPS, W_TABLES, W_NAME = 0.40, 0.35, 0.25
FUZZY_TOKEN_JACCARD = 0.5
GOLD_TABLE_ASSOC_FLOOR = 0.5


# ---------------------------------------------------------------------------
# gold comparator
# ---------------------------------------------------------------------------


def _fuzzy_in(tok: tuple[str, ...], toks: set[tuple[str, ...]]) -> bool:
    return any(token_jaccard(tok, t) >= FUZZY_TOKEN_JACCARD for t in toks)


def _gold_prop_tokens(gold_class) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for p in gold_class.properties:
        out.add(tuple(name_tokens(p.name)))
        for syn in p.synonyms:
            out.add(tuple(name_tokens(syn)))
    return out


@dataclass
class GoldComparison:
    precision: float
    recall: float
    n_induced: int
    n_gold: int
    gold_matches: dict[str, tuple[float, str]]      # gold name -> (score, induced name)
    induced_matches: dict[str, tuple[float, str]]   # induced name -> (score, gold name)
    hierarchy_precision: float = 0.0
    hierarchy_recall: float = 0.0
    n_induced_edges: int = 0
    n_gold_edges: int = 0
    report: str = ""


def compare_to_gold(result: StrataResult, gold: Ontology, profiles) -> GoldComparison:
    ctx, lattice = result.context, result.lattice

    # gold class -> associated estate tables, derived from property/column names
    col_norms = {tp.table: {tuple(name_tokens(c)) for c in tp.columns} for tp in profiles}
    gold_tables: dict[str, set[str]] = {}
    for gc in gold.classes.values():
        pts = _gold_prop_tokens(gc)
        gold_tables[gc.name] = {
            t
            for t, cols in col_norms.items()
            if pts and sum(_fuzzy_in(p, cols) for p in pts) / len(pts) >= GOLD_TABLE_ASSOC_FLOOR
        }

    # induced class -> (evidence tables, full-intent property tokens, name tokens)
    ind_info: dict[str, tuple[set[str], set[tuple[str, ...]], set[str]]] = {}
    for c in result.ontology.classes.values():
        concept = lattice.concepts[c.intent_hash]
        tables = {
            t
            for g in concept.extent
            if g in ctx.candidates
            for t in ctx.candidates[g].evidence_tables
        }
        props = {
            tuple(name_tokens(a.split(":", 1)[1]))
            for a in concept.intent
            if a.startswith("has-prop:")
        }
        ind_info[c.name] = (tables, props, set(name_tokens(c.name)))

    def score(gc, iname: str) -> float:
        itables, iprops, intoks = ind_info[iname]
        gprops = _gold_prop_tokens(gc)
        gt = gold_tables[gc.name]
        num = den = 0.0
        if gprops and iprops:
            rec = sum(_fuzzy_in(p, iprops) for p in gprops) / len(gprops)
            prec = sum(_fuzzy_in(p, gprops) for p in iprops) / len(iprops)
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            num += W_PROPS * f1
            den += W_PROPS
        if gt:
            num += W_TABLES * (len(gt & itables) / len(gt | itables))
            den += W_TABLES
        num += W_NAME * token_jaccard(set(name_tokens(gc.name)), intoks)
        den += W_NAME
        return num / den if den else 0.0

    gold_matches: dict[str, tuple[float, str]] = {}
    for gc in gold.classes.values():
        sc, iname = max((score(gc, i), i) for i in ind_info)
        if sc >= MATCH_THETA:
            gold_matches[gc.name] = (round(sc, 3), iname)
    induced_matches: dict[str, tuple[float, str]] = {}
    for iname in ind_info:
        sc, gname = max((score(gc, iname), gc.name) for gc in gold.classes.values())
        if sc >= MATCH_THETA:
            induced_matches[iname] = (round(sc, 3), gname)

    precision = len(induced_matches) / len(ind_info) if ind_info else 0.0
    recall = len(gold_matches) / len(gold.classes) if gold.classes else 0.0

    # ---- hierarchy edges on matched classes (ancestor-closure credit) ------
    i2g = {i: v[1] for i, v in induced_matches.items()}
    g2i = {g: v[1] for g, v in gold_matches.items()}
    iuri2name = {u: c.name for u, c in result.ontology.classes.items()}
    guri2name = {u: c.name for u, c in gold.classes.items()}
    ind_edges = {
        (c.name, iuri2name[p]) for c in result.ontology.classes.values() for p in c.parents
    }
    gold_edges = {
        (c.name, guri2name[p]) for c in gold.classes.values() for p in c.parents
    }

    def closure(edges: set[tuple[str, str]], node: str) -> set[str]:
        out: set[str] = set()
        stack = [node]
        while stack:
            x = stack.pop()
            for child, parent in edges:
                if child == x and parent not in out:
                    out.add(parent)
                    stack.append(parent)
        return out

    scored_ind = [(c, p) for c, p in ind_edges if c in i2g and p in i2g]
    correct_ind = [
        (c, p) for c, p in scored_ind if i2g[p] == i2g[c] or i2g[p] in closure(gold_edges, i2g[c])
    ]
    scored_gold = [(c, p) for c, p in gold_edges if c in g2i and p in g2i]
    recovered = [
        (c, p)
        for c, p in scored_gold
        if g2i[p] == g2i[c] or g2i[p] in closure(ind_edges, g2i[c])
    ]
    h_p = len(correct_ind) / len(scored_ind) if scored_ind else 1.0
    h_r = len(recovered) / len(scored_gold) if scored_gold else 1.0

    lines = [
        f"class precision {precision:.3f} ({len(induced_matches)}/{len(ind_info)}), "
        f"recall {recall:.3f} ({len(gold_matches)}/{len(gold.classes)})",
        f"hierarchy-edge precision {h_p:.3f} ({len(correct_ind)}/{len(scored_ind)}), "
        f"recall {h_r:.3f} ({len(recovered)}/{len(scored_gold)})",
        "gold matches:",
    ]
    lines += [f"  {g:16s} -> {v[1]:30s} {v[0]:.3f}" for g, v in sorted(gold_matches.items())]
    lines.append(
        "unmatched gold: " + ", ".join(sorted(set(guri2name.values()) - set(gold_matches)))
    )
    lines.append(
        "unmatched induced: " + ", ".join(sorted(set(ind_info) - set(induced_matches)))
    )
    return GoldComparison(
        precision=precision,
        recall=recall,
        n_induced=len(ind_info),
        n_gold=len(gold.classes),
        gold_matches=gold_matches,
        induced_matches=induced_matches,
        hierarchy_precision=h_p,
        hierarchy_recall=h_r,
        n_induced_edges=len(scored_ind),
        n_gold_edges=len(scored_gold),
        report="\n".join(lines),
    )


# ---------------------------------------------------------------------------
# synthetic micro estate (hand-designed, fully deterministic)
# ---------------------------------------------------------------------------

#: aircraft reference: 8 airframes, 'code' is the key. Codes deliberately only
#: partially referenced from flights so the IND is one-directional.
MICRO_AIRCRAFT = {
    "code": ["AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7", "AC8"],
    "model": ["B737", "A320", "C172", "B737", "PA28", "C172", "DA40", "B747"],
    "year_built": ["1998", "2004", "1979", "2001", "1985", "2012", "2015", "1992"],
    "seats": ["189", "180", "4", "189", "4", "4", "4", "416"],
}

#: site reference: 5 sites, 'site_code' is the key.
MICRO_SITES = {
    "site_code": ["KJFK", "KLAX", "KORD", "KSEA", "KDEN"],
    "site_name": ["Kennedy", "Los Angeles", "OHare", "Seattle Tacoma", "Denver Intl"],
    "region": ["east", "west", "central", "west", "central"],
}

#: flight event log: append-mostly, timestamped, references aircraft + sites.
MICRO_FLIGHTS = {
    "flight_id": [f"F{i:03d}" for i in range(1, 13)],
    "event_date": [
        "2024-01-03", "2024-01-07", "2024-01-11", "2024-02-02", "2024-02-14",
        "2024-02-27", "2024-03-05", "2024-03-09", "2024-03-21", "2024-04-01",
        "2024-04-12", "2024-04-30",
    ],
    "aircraft_code": [
        "AC1", "AC2", "AC3", "AC1", "AC4", "AC5", "AC2", "AC6", "AC3", "AC1",
        "AC4", "AC2",
    ],
    "site_code": [
        "KJFK", "KLAX", "KORD", "KSEA", "KJFK", "KLAX", "KORD", "KJFK", "KSEA",
        "KLAX", "KJFK", "KORD",
    ],
    "severity": [
        "minor", "major", "minor", "none", "minor", "major", "none", "minor",
        "none", "major", "minor", "none",
    ],
}


def micro_estate() -> dict[str, pd.DataFrame]:
    return {
        "aircraft": pd.DataFrame(MICRO_AIRCRAFT),
        "sites": pd.DataFrame(MICRO_SITES),
        "flights": pd.DataFrame(MICRO_FLIGHTS),
    }


def micro_profiles_and_inds():
    """Profile the micro estate with the REAL M3 profiler and mark the flight
    log append-mostly (the CDC signal the §3.5 event rule requires)."""
    from ontoforge.profiling import discover_inds, profile_table

    tables = micro_estate()
    profiles = [profile_table(df, "micro", name) for name, df in tables.items()]
    for tp in profiles:
        if tp.table == "flights":
            tp.append_mostly = True
    return profiles, discover_inds(tables)
