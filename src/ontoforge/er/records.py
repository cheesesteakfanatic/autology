"""Record extraction: estate DataFrames -> EntityMention streams (M5 step 1).

Per-table field maps pull the cross-source join surfaces of the aviation
estate (whitepaper §17.2.1):

- aircraft: FAA registry rows (tail + serial + model-code -> acftref join +
  registration validity window), ASRS reports (tail extracted from the
  NARRATIVE text), NTSB events (ACFT_REGIST_NMBR), maintenance ERP work
  orders (TAIL_NUMBER).
- operator: registrant / operator name fields on the same four tables.

Normalization contract (orchestration spec): strip, uppercase, collapse
spaces. Tail numbers are canonicalized to the registry's N-less form
("N3484Z" -> "3484Z") so the leading-'N' variants block together — but a
mention NEVER loses its serial or its date evidence, so the documented
temporal N-number-reuse trap (§17.2.1 "N-numbers reused across aircraft over
time") remains separable downstream: same tail + different serial + disjoint
date ranges is decided by the matcher, never by normalization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional

__all__ = [
    "EntityMention",
    "extract_mentions",
    "norm_text",
    "norm_tail",
    "norm_serial",
    "norm_name",
    "core_tokens",
    "parse_date_ordinal",
    "TAIL_IN_TEXT_RE",
    "NAME_STOP_TOKENS",
]

# US registration in free text: 'N' + 1-5 digits + 0-2 trailing letters.
TAIL_IN_TEXT_RE = re.compile(r"\bN[0-9]{1,5}[A-Z]{0,2}\b")

# Legal/suffix tokens carrying no identity signal for operator names.
NAME_STOP_TOKENS = frozenset(
    {"INC", "LLC", "CO", "CORP", "CORPORATION", "COMPANY", "LTD", "INCORPORATED", "PLC", "GMBH"}
)

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]")
_DROP_PUNCT_RE = re.compile(r"[.'’]")
_PAREN_RE = re.compile(r"\([^)]*\)")


def norm_text(s: Any) -> str:
    """strip, upper, collapse internal whitespace (the spec's base normalizer)."""
    return _WS_RE.sub(" ", str(s or "").strip().upper())


def norm_name(s: Any) -> str:
    """Name normalizer: periods/apostrophes deleted IN PLACE (U.S. -> US,
    L.L.C. -> LLC), other punctuation -> space, then base normalization."""
    s = _DROP_PUNCT_RE.sub("", str(s or "").upper())
    s = _NON_ALNUM_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def core_tokens(name_norm: str) -> list[str]:
    """Identity-bearing tokens of a normalized name: legal suffixes and
    single-letter fragments dropped (kept order, duplicates preserved)."""
    return [t for t in name_norm.split() if len(t) > 1 and t not in NAME_STOP_TOKENS]


def norm_tail(s: Any) -> str:
    """Canonical tail: upper, spaces/dashes removed, leading 'N' dropped when
    followed by a digit (the FAA registry stores tails N-less)."""
    t = str(s or "").strip().upper().replace("-", "").replace(" ", "")
    if len(t) >= 2 and t[0] == "N" and t[1].isdigit():
        t = t[1:]
    return t


def norm_serial(s: Any) -> str:
    """Canonical serial: upper + all non-alphanumerics removed ('28-1974519'
    and '28 1974519' compare equal; FAA trailing-space padding stripped)."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def norm_model(s: Any) -> str:
    """Model designator: parentheticals dropped ('(ABOVE 200 HP)'), then
    punctuation -> space and base normalization."""
    s = _PAREN_RE.sub(" ", str(s or "").upper())
    s = _NON_ALNUM_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def parse_date_ordinal(s: Any) -> Optional[int]:
    """Parse the estate's date formats to a proleptic-Gregorian ordinal day.

    Accepts YYYYMMDD (FAA), YYYY-MM-DD (NTSB/ERP), YYYYMM (ASRS month —
    mapped to the 15th). Returns None for blanks/unparseable values.
    """
    raw = str(s or "").strip()
    if not raw:
        return None
    digits = re.sub(r"[^0-9]", "", raw)
    try:
        if len(digits) == 8:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])).toordinal()
        if len(digits) == 6:
            return date(int(digits[:4]), int(digits[4:6]), 15).toordinal()
    except ValueError:
        return None
    return None


@dataclass(frozen=True, slots=True)
class EntityMention:
    """One source-row mention of a real-world entity (M5 step 1).

    fields is a flat dict of normalized evidence. Shared keys:

    - aircraft: tail, serial, model, name (registrant/operator), date_lo,
      date_hi (registration validity window for registry rows, event date /
      work-order span otherwise), is_registry ('1'/'0'), year.
    - operator: name (raw-ish), name_norm, tail (the row's aircraft, for
      relational blocking), date.
    """

    mention_id: str
    source_id: str
    table: str
    row_key: str
    entity_kind: str  # 'aircraft' | 'operator'
    fields: dict[str, Any] = field(default_factory=dict)


def _mid(kind: str, table: str, row_key: str) -> str:
    return f"{kind}/{table}/{row_key}"


def _faa_row_key(row: dict[str, Any]) -> str:
    return f"{str(row['N-NUMBER']).strip()}|{str(row['SERIAL NUMBER']).strip()}"


def _acftref_index(estate: dict[str, Any]) -> dict[str, str]:
    """MFR MDL CODE -> normalized 'MFR MODEL' designator string."""
    df = estate["tables"]["faa_acftref"]
    out: dict[str, str] = {}
    for row in df.to_dict("records"):
        code = str(row["CODE"]).strip()
        out[code] = norm_model(f"{row['MFR']} {row['MODEL']}")
    return out


def _iter_rows(estate: dict[str, Any], table: str) -> Iterable[dict[str, Any]]:
    return estate["tables"][table].to_dict("records")


# ---------------------------------------------------------------- aircraft

def _aircraft_mentions(estate: dict[str, Any]) -> list[EntityMention]:
    ref = _acftref_index(estate)
    out: list[EntityMention] = []

    for row in _iter_rows(estate, "faa_master"):
        rk = _faa_row_key(row)
        out.append(
            EntityMention(
                mention_id=_mid("aircraft", "faa_master", rk),
                source_id="faa_registry",
                table="faa_master",
                row_key=rk,
                entity_kind="aircraft",
                fields={
                    "tail": norm_tail(row["N-NUMBER"]),
                    "serial": norm_serial(row["SERIAL NUMBER"]),
                    "model": ref.get(str(row["MFR MDL CODE"]).strip(), ""),
                    "name": norm_name(row["REGISTRANT NAME"]),
                    "year": norm_text(row["YEAR MFR"]),
                    "date_lo": parse_date_ordinal(row["CERT ISSUE DATE"]),
                    "date_hi": parse_date_ordinal(row["EXPIRATION DATE"]),
                    "is_registry": "1",
                },
            )
        )

    for row in _iter_rows(estate, "asrs_reports"):
        rk = str(row["ACN"]).strip()
        m = TAIL_IN_TEXT_RE.search(str(row["NARRATIVE"]).upper())
        ev = parse_date_ordinal(row["DATE"])
        out.append(
            EntityMention(
                mention_id=_mid("aircraft", "asrs_reports", rk),
                source_id="asrs",
                table="asrs_reports",
                row_key=rk,
                entity_kind="aircraft",
                fields={
                    "tail": norm_tail(m.group(0)) if m else "",
                    "serial": "",
                    "model": norm_model(row["AIRCRAFT 1 MAKE MODEL"]),
                    "name": norm_name(row["AIRCRAFT 1 OPERATOR"]),
                    "year": "",
                    "date_lo": ev,
                    "date_hi": ev,
                    "is_registry": "0",
                },
            )
        )

    for row in _iter_rows(estate, "ntsb_events"):
        rk = str(row["EV_ID"]).strip()
        ev = parse_date_ordinal(row["EV_DATE"])
        out.append(
            EntityMention(
                mention_id=_mid("aircraft", "ntsb_events", rk),
                source_id="ntsb",
                table="ntsb_events",
                row_key=rk,
                entity_kind="aircraft",
                fields={
                    "tail": norm_tail(row["ACFT_REGIST_NMBR"]),
                    "serial": "",
                    "model": norm_model(f"{row['ACFT_MAKE']} {row['ACFT_MODEL']}"),
                    "name": norm_name(row["OPERATOR"]),
                    "year": "",
                    "date_lo": ev,
                    "date_hi": ev,
                    "is_registry": "0",
                },
            )
        )

    for row in _iter_rows(estate, "maintenance_erp"):
        rk = str(row["WORK_ORDER_ID"]).strip()
        lo = parse_date_ordinal(row["OPEN_DATE"])
        hi = parse_date_ordinal(row["CLOSE_DATE"])
        out.append(
            EntityMention(
                mention_id=_mid("aircraft", "maintenance_erp", rk),
                source_id="erp",
                table="maintenance_erp",
                row_key=rk,
                entity_kind="aircraft",
                fields={
                    "tail": norm_tail(row["TAIL_NUMBER"]),
                    "serial": "",
                    "model": "",
                    "name": norm_name(row["OPERATOR_NAME"]),
                    "year": "",
                    "date_lo": lo if lo is not None else hi,
                    "date_hi": hi if hi is not None else lo,
                    "is_registry": "0",
                },
            )
        )
    return out


# ---------------------------------------------------------------- operator

_OPERATOR_FIELD = {
    "faa_master": "REGISTRANT NAME",
    "asrs_reports": "AIRCRAFT 1 OPERATOR",
    "ntsb_events": "OPERATOR",
    "maintenance_erp": "OPERATOR_NAME",
}

_OPERATOR_SOURCE = {
    "faa_master": "faa_registry",
    "asrs_reports": "asrs",
    "ntsb_events": "ntsb",
    "maintenance_erp": "erp",
}


def _operator_mentions(estate: dict[str, Any]) -> list[EntityMention]:
    out: list[EntityMention] = []
    for table, col in _OPERATOR_FIELD.items():
        for row in _iter_rows(estate, table):
            raw = str(row[col]).strip()
            if not raw:
                continue  # blank operator field => no operator mention
            if table == "faa_master":
                rk = _faa_row_key(row)
                tail = norm_tail(row["N-NUMBER"])
            elif table == "asrs_reports":
                rk = str(row["ACN"]).strip()
                m = TAIL_IN_TEXT_RE.search(str(row["NARRATIVE"]).upper())
                tail = norm_tail(m.group(0)) if m else ""
            elif table == "ntsb_events":
                rk = str(row["EV_ID"]).strip()
                tail = norm_tail(row["ACFT_REGIST_NMBR"])
            else:
                rk = str(row["WORK_ORDER_ID"]).strip()
                tail = norm_tail(row["TAIL_NUMBER"])
            nn = norm_name(raw)
            if not nn:
                continue
            out.append(
                EntityMention(
                    mention_id=_mid("operator", table, rk),
                    source_id=_OPERATOR_SOURCE[table],
                    table=table,
                    row_key=rk,
                    entity_kind="operator",
                    fields={"name": norm_text(raw), "name_norm": nn, "tail": tail},
                )
            )
    return out


def extract_mentions(estate: dict[str, Any], kinds: tuple[str, ...] = ("aircraft", "operator")) -> list[EntityMention]:
    """All EntityMentions of the requested kinds, in deterministic order."""
    out: list[EntityMention] = []
    if "aircraft" in kinds:
        out.extend(_aircraft_mentions(estate))
    if "operator" in kinds:
        out.extend(_operator_mentions(estate))
    return out
