"""The dataset catalog: every downloaded dataset OntoForge can build a
playground world from, enumerated with deterministic, keyless metadata.

Three corpora feed the catalog, all read-only from disk (zero network):

* **wild** — ``fixtures/wild/*.csv`` snapshotted from the public internet. The
  pinned ``manifest.lock.json`` (when present) supplies source + url + row/col
  counts; the catalog falls back to the filesystem (so it survives a mid-fetch
  manifest rewrite) and reads the header row for column names either way.
* **meridian** — the bundled 10-table enterprise corpus (``fixtures/meridian/``).
* **aviation** — the FAA/NTSB/ASRS/ERP fixtures (``fixtures/aviation/``).

``domain`` and ``description`` are derived DETERMINISTICALLY (no LLM): the
domain from the source + a column-token vote against a fixed keyword map, the
description from the source, row/col shape and the leading column names. Equal
inputs always produce equal output, so the catalog is byte-stable across runs.

The dataset ``id`` is ``"<corpus>:<slug>"`` (e.g. ``wild:ds_airport_codes``,
``meridian:products``); :func:`resolve_file` maps an id back to its source CSV
for the playground builder. ``id`` is the stable handle the build API and the
UI multi-select pass around.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

__all__ = [
    "DOMAIN_KEYWORDS",
    "CatalogEntry",
    "build_catalog",
    "catalog_domains",
    "resolve_file",
]

#: number of header columns to name in the auto description
_DESC_COLS = 4
#: byte cap when sniffing a CSV header (headers are short; never read the body)
_HEADER_BYTES = 64 * 1024

# --------------------------------------------------------------- domain mapping

#: deterministic domain vote: a domain wins by counting how many of these tokens
#: appear (as substrings) across the column-name tokens + table name. Ordered so
#: ties break toward the EARLIER (more specific) domain. Keyless and fixed.
DOMAIN_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("aviation", ("aircraft", "acft", "flight", "airport", "airline", "iata",
                  "icao", "tail", "ntsb", "asrs", "faa", "registrant", "mfr")),
    ("supply chain", ("supplier", "vendor", "purchase", "po", "shipment", "carrier",
                      "incoterms", "freight", "bom", "component", "part", "material",
                      "warehouse", "plant", "lead")),
    ("finance", ("price", "cost", "spend", "revenue", "sales", "currency", "usd",
                 "amount", "yield", "bond", "budget", "invoice", "payment", "msrp",
                 "rent", "lease")),
    ("geography", ("country", "city", "state", "region", "province", "latitude",
                   "longitude", "lat", "lon", "postal", "address", "iso", "geo")),
    ("sports", ("match", "tournament", "player", "team", "score", "win", "loss",
                "tennis", "atp", "league", "season")),
    ("health", ("patient", "cancer", "disease", "diagnosis", "mortality", "death",
                "hospital", "clinical", "symptom", "cases")),
    ("commerce", ("product", "sku", "gtin", "store", "retail", "order", "customer",
                  "ticket", "warranty", "csat", "channel")),
    ("workforce", ("employee", "headcount", "fte", "department", "staff", "salary",
                   "requisition", "hire", "mechanic")),
    ("technology", ("browser", "version", "os", "device", "platform", "user_agent",
                    "release")),
    ("demographics", ("population", "census", "age", "gender", "households",
                      "literacy", "birth")),
    ("time series", ("date", "year", "month", "quarter", "fiscal", "timestamp",
                     "snapshot", "annual", "quarterly", "monthly")),
)

#: human-friendly source labels (description prose)
_SOURCE_LABEL = {
    "wild": "public internet snapshot",
    "meridian": "Meridian enterprise corpus",
    "aviation": "FAA/NTSB aviation fixtures",
    "datasets-org": "datasets.io open-data org",
    "openflights": "OpenFlights",
    "fivethirtyeight": "FiveThirtyEight",
    "vega": "Vega example data",
    "seaborn": "seaborn example data",
}


def _tokens(*texts: str) -> list[str]:
    """Lowercased alphanumeric tokens from column/table names (snake + camel)."""
    out: list[str] = []
    for t in texts:
        piece = ""
        prev_lower = False
        for ch in str(t):
            if ch.isalnum():
                if ch.isupper() and prev_lower:
                    if piece:
                        out.append(piece.lower())
                    piece = ch
                else:
                    piece += ch
                prev_lower = ch.islower() or ch.isdigit()
            else:
                if piece:
                    out.append(piece.lower())
                piece = ""
                prev_lower = False
        if piece:
            out.append(piece.lower())
    return out


def derive_domain(source: str, table: str, columns: Iterable[str]) -> str:
    """Deterministic domain label from source + column/table tokens.

    A domain scores one point per keyword that is a substring of any token; the
    highest score wins, ties broken toward the earlier (more specific) entry in
    :data:`DOMAIN_KEYWORDS`. No match -> ``"general"``. Pure function."""
    toks = _tokens(table, *columns)
    blob = " ".join(toks)
    best_domain = "general"
    best_score = 0
    for domain, keywords in DOMAIN_KEYWORDS:
        score = sum(1 for kw in keywords if kw in blob)
        if score > best_score:
            best_score = score
            best_domain = domain
    return best_domain


def derive_description(
    source: str, name: str, rows: int, cols: int, columns: list[str]
) -> str:
    """A one-line, deterministic description: source + shape + leading columns."""
    label = _SOURCE_LABEL.get(source, source)
    lead = ", ".join(columns[:_DESC_COLS])
    if len(columns) > _DESC_COLS:
        lead += ", …"
    shape = f"{rows} rows × {cols} cols"
    if lead:
        return f"{name} — {label}; {shape}; columns: {lead}."
    return f"{name} — {label}; {shape}."


# ---------------------------------------------------------------- catalog entry


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One catalog dataset (the ``GET /api/catalog`` ``datasets[]`` shape)."""

    id: str
    name: str
    source: str
    domain: str
    rows: int
    cols: int
    columns: tuple[str, ...]
    description: str
    file: str            # absolute path to the backing CSV (server-internal)

    def to_public(self) -> dict[str, Any]:
        """The wire dict — ``file`` is internal and intentionally omitted."""
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "domain": self.domain,
            "rows": self.rows,
            "cols": self.cols,
            "columns": list(self.columns),
            "description": self.description,
        }


# ------------------------------------------------------------------ csv probing


def _read_header(path: Path) -> list[str]:
    """The first CSV row as column names (sniffed, wart-tolerant). Empty on any
    read error — a header-less or unreadable file still appears in the catalog
    with cols=0 rather than crashing the whole listing."""
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            sample = fh.read(_HEADER_BYTES)
        if not sample:
            return []
        line = sample.splitlines()[0] if sample.splitlines() else ""
        # csv.reader handles quoted commas in the header
        row = next(csv.reader([line]), [])
        return [c.strip() for c in row]
    except (OSError, csv.Error):
        return []


def _count_rows(path: Path) -> int:
    """Data-row count (lines minus header). Cheap line scan, never parses the
    body into pandas — the catalog only needs the count for display."""
    try:
        with path.open("rb") as fh:
            n = sum(1 for _ in fh)
        return max(0, n - 1)
    except OSError:
        return 0


# -------------------------------------------------------------- corpus readers


def _wild_entries(fixtures_root: Path) -> list[CatalogEntry]:
    """Wild corpus: manifest-driven when present, filesystem fallback otherwise
    (so the catalog survives a mid-fetch manifest rewrite)."""
    wild_dir = fixtures_root / "wild"
    if not wild_dir.is_dir():
        return []

    manifest: dict[str, dict[str, Any]] = {}
    mpath = wild_dir / "manifest.lock.json"
    if mpath.is_file():
        try:
            payload = json.loads(mpath.read_text(encoding="utf-8"))
            for d in payload.get("datasets", []):
                slug = d.get("slug")
                if slug:
                    manifest[str(slug)] = d
        except (OSError, ValueError):
            manifest = {}

    entries: list[CatalogEntry] = []
    for path in sorted(wild_dir.glob("*.csv"), key=lambda p: p.stem.lower()):
        slug = path.stem
        meta = manifest.get(slug, {})
        source = str(meta.get("source", "wild"))
        columns = _read_header(path)
        cols = int(meta.get("cols", len(columns)) or len(columns))
        rows = int(meta.get("rows_kept", 0)) or _count_rows(path)
        name = slug.split("_", 1)[-1].replace("_", " ").strip() or slug
        entries.append(
            CatalogEntry(
                id=f"wild:{slug}",
                name=name,
                source=source,
                domain=derive_domain(source, slug, columns),
                rows=rows,
                cols=cols,
                columns=tuple(columns),
                description=derive_description(source, name, rows, cols, columns),
                file=str(path.resolve()),
            )
        )
    return entries


def _flat_corpus_entries(
    corpus: str, corpus_dir: Path, *, exclude: tuple[str, ...] = ()
) -> list[CatalogEntry]:
    """A flat directory of CSVs (meridian, aviation) -> catalog entries."""
    if not corpus_dir.is_dir():
        return []
    entries: list[CatalogEntry] = []
    for path in sorted(corpus_dir.glob("*.csv"), key=lambda p: p.stem.lower()):
        if path.stem in exclude:
            continue
        columns = _read_header(path)
        rows = _count_rows(path)
        name = path.stem.replace("_", " ").strip() or path.stem
        entries.append(
            CatalogEntry(
                id=f"{corpus}:{path.stem}",
                name=name,
                source=corpus,
                domain=derive_domain(corpus, path.stem, columns),
                rows=rows,
                cols=len(columns),
                columns=tuple(columns),
                description=derive_description(corpus, name, rows, len(columns), columns),
                file=str(path.resolve()),
            )
        )
    return entries


# ----------------------------------------------------------------- the catalog


def build_catalog(fixtures_root: Path | str) -> list[CatalogEntry]:
    """Every downloaded dataset (wild + meridian + aviation), id-sorted.

    Deterministic: corpora are read in a fixed order and each corpus is sorted
    by slug, so two calls over the same disk produce identical lists."""
    root = Path(fixtures_root)
    entries: list[CatalogEntry] = []
    entries += _wild_entries(root)
    entries += _flat_corpus_entries("meridian", root / "meridian")
    entries += _flat_corpus_entries("aviation", root / "aviation")
    entries.sort(key=lambda e: e.id)
    return entries


def catalog_domains(entries: list[CatalogEntry]) -> list[dict[str, Any]]:
    """``[{name, count}]`` over the catalog, by descending count then name."""
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.domain] = counts.get(e.domain, 0) + 1
    return [
        {"name": name, "count": counts[name]}
        for name in sorted(counts, key=lambda n: (-counts[n], n))
    ]


def resolve_file(entries: list[CatalogEntry], dataset_id: str) -> Optional[Path]:
    """The backing CSV path for a catalog id, or None if unknown."""
    for e in entries:
        if e.id == dataset_id:
            return Path(e.file)
    return None
