"""Documented-wart presence tests (whitepaper §17.2.1 warts column; AMD-0006).

The warts ARE the test features: ER stressors (name variants, misspellings,
N-number reuse), ANVIL bait (mixed cost formats, meters-suffix altitudes),
profiler stressors (trailing-space padding, blank permissible fields).
"""

from __future__ import annotations

import re
from datetime import date

FT_PER_M = 3.28084
TAIL_RE = re.compile(r"N[0-9]{2,5}[A-Z]{0,2}")


def registry_tails(estate) -> set[str]:
    master = estate["tables"]["faa_master"]
    return {"N" + n.strip() for n in master["N-NUMBER"]}


# ---------------------------------------------------------------- overlap

def test_asrs_narratives_reference_registry_tails(estate):
    """>=55% of ASRS rows must mention a registry tail in free text — the
    structured<->unstructured join surface (§17.2.1 entity-overlap structure)."""
    tails = registry_tails(estate)
    asrs = estate["tables"]["asrs_reports"]
    hits = sum(
        1 for n in asrs["NARRATIVE"] if any(t in tails for t in TAIL_RE.findall(n))
    )
    ratio = hits / len(asrs)
    assert ratio >= 0.55, f"asrs->registry overlap too low: {ratio:.2f}"
    assert ratio <= 0.80, f"overlap suspiciously high (fixture drift?): {ratio:.2f}"


def test_ntsb_registrations_resolve_to_registry(estate):
    tails = registry_tails(estate)
    ntsb = estate["tables"]["ntsb_events"]
    resolved = sum(
        1
        for r in ntsb["ACFT_REGIST_NMBR"]
        if r.strip() and (r.strip() if r.startswith("N") else "N" + r.strip()) in tails
    )
    assert resolved / len(ntsb) >= 0.6


def test_erp_tails_mostly_resolve_with_documented_orphans(estate):
    tails = registry_tails(estate)
    erp = estate["tables"]["maintenance_erp"]
    orphans = [t for t in erp["TAIL_NUMBER"] if t not in tails]
    assert 1 <= len(orphans) <= 10, "expected a small documented orphan slice"


# ---------------------------------------------------------------- unit warts

def test_meters_wart_slice_present(estate):
    asrs = estate["tables"]["asrs_reports"]
    alts = [a.strip() for a in asrs["ALTITUDE.AGL.SINGLE VALUE"]]
    meters = [a for a in alts if re.fullmatch(r"[0-9]+m", a)]
    assert len(meters) >= 20, "meters wart slice missing/too small"
    # the trap must bite both ways: at least one meters value above 10,000 ft
    # (naive numeric read would wrongly include it) and one below
    fts = [int(m[:-1]) * FT_PER_M for m in meters]
    assert any(v > 10000 for v in fts)
    assert any(v < 10000 for v in fts)


def test_blank_altitudes_are_permissible_not_errors(estate):
    asrs = estate["tables"]["asrs_reports"]
    blanks = sum(1 for a in asrs["ALTITUDE.AGL.SINGLE VALUE"] if not a.strip())
    assert blanks >= 5


def test_erp_cost_mixes_lexical_forms(estate):
    erp = estate["tables"]["maintenance_erp"]
    styled = [c for c in erp["COST"] if re.fullmatch(r"USD [0-9,]+\.[0-9]{2}", c)]
    bare = [c for c in erp["COST"] if re.fullmatch(r"[0-9]+\.[0-9]{2}", c)]
    assert len(styled) >= 100 and len(bare) >= 100
    assert len(styled) + len(bare) == len(erp)


# ---------------------------------------------------------------- name warts

def test_manufacturer_name_variants_in_acftref(estate):
    mfrs = set(estate["tables"]["faa_acftref"]["MFR"])
    assert {"ROCKWELL INTERNATIONAL CORP", "ROCKWELL INTL"} <= mfrs
    assert {"BOEING", "THE BOEING COMPANY"} <= mfrs


def test_registrant_name_variants_in_registry(estate):
    names = {n.strip() for n in estate["tables"]["faa_master"]["REGISTRANT NAME"]}
    # at least one operator appears under two registry spellings
    assert {"DELTA AIR LINES INC", "DELTA AIR LINES, INC."} <= names or {
        "UNITED AIRLINES INC", "UNITED AIR LINES INC"
    } <= names or {
        "WELLS FARGO TRUST CO NA TRUSTEE", "WELLS FARGO BANK NA TRUSTEE"
    } <= names


def test_asrs_operator_misspellings_present(estate):
    ops = {o.strip() for o in estate["tables"]["asrs_reports"]["AIRCRAFT 1 OPERATOR"]}
    misspellings = {"Untied Airlines", "Detla Air Lines", "Americian Airlines",
                    "South West Airlines", "Ameriflite", "Blueridge Helicopters",
                    "Gulf Coast Areal Survey", "Sky West Airlines"}
    assert ops & misspellings, "no misspelled operator names found in ASRS"


def test_ntsb_dropped_n_prefix_wart(estate):
    tails = registry_tails(estate)
    ntsb = estate["tables"]["ntsb_events"]
    dropped = [
        r.strip()
        for r in ntsb["ACFT_REGIST_NMBR"]
        if r.strip() and not r.startswith("N") and ("N" + r.strip()) in tails
    ]
    assert len(dropped) >= 10, "dropped-'N' registration wart missing"


# ---------------------------------------------------------------- layout warts

def test_faa_master_trailing_space_padding(estate):
    master = estate["tables"]["faa_master"]
    for col in ["N-NUMBER", "REGISTRANT NAME", "SERIAL NUMBER", "CITY"]:
        assert any(str(v).endswith(" ") for v in master[col].head(200)), (
            f"column {col} lost its fixed-width padding wart"
        )


def test_faa_master_blank_permissible_fields(estate):
    master = estate["tables"]["faa_master"]
    for col in ["YEAR MFR", "AIR WORTH DATE", "FRACT OWNER"]:
        assert any(not str(v).strip() for v in master[col]), f"no blanks in {col}"


# ---------------------------------------------------------------- reuse trap

def test_n_number_reuse_with_disjoint_windows(estate):
    """The §17.2.1 temporal-identity ER trap: N-numbers reused across different
    airframes whose registration windows must NOT overlap."""
    master = estate["tables"]["faa_master"]
    by_tail: dict[str, list[dict]] = {}
    for rec in master.to_dict(orient="records"):
        by_tail.setdefault(rec["N-NUMBER"].strip(), []).append(rec)
    reused = {t: rows for t, rows in by_tail.items()
              if len({r["SERIAL NUMBER"].strip() for r in rows}) > 1}
    assert 5 <= len(reused) <= 12, f"expected ~8 reused tails, got {len(reused)}"
    for tail, rows in reused.items():
        assert len(rows) == 2
        windows = sorted(
            (
                date.fromisoformat(f"{r['CERT ISSUE DATE'].strip()[:4]}-"
                                   f"{r['CERT ISSUE DATE'].strip()[4:6]}-"
                                   f"{r['CERT ISSUE DATE'].strip()[6:]}"),
                date.fromisoformat(f"{r['EXPIRATION DATE'].strip()[:4]}-"
                                   f"{r['EXPIRATION DATE'].strip()[4:6]}-"
                                   f"{r['EXPIRATION DATE'].strip()[6:]}"),
                r["STATUS CODE"].strip(),
            )
            for r in rows
        )
        (s1, e1, st1), (s2, e2, st2) = windows
        assert e1 < s2, f"reuse windows overlap for {tail}"
        assert st1 == "D" and st2 == "V", f"unexpected status codes for {tail}"
