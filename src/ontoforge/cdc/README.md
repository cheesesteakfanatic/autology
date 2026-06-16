# M1 — CDC & Ingestion

Whitepaper §11.2 M1: `pull(source) → Δbatch` per connector; MVP plan §4.1.
Connectors turn files into streams of `contracts.ledger.AtomDelta` over
`contracts.atoms.Atom` with stable, content-addressed URIs. The ingest driver
registers atoms with anything implementing the contracts `Ledger` protocol and
mirrors every pulled snapshot to the RAW layer (whitepaper §2).

## Layout

| file | contents |
|---|---|
| `base.py` | `Connector` protocol, `AtomRegistrar` slice of the Ledger protocol, xxh3 hashing, URI quoting, encoding-robust text reader, state versioning |
| `tabular.py` | `CsvConnector`, `ParquetConnector` — per-row hash diff, per-cell deltas; `parse_csv_text` (the single CSV-parsing rule) |
| `docs.py` | `DocConnector` — paragraph span atoms with content-hash re-anchoring |
| `sql.py` | `SqlConnector` — SQLAlchemy table snapshot-diff (Postgres/MySQL/SQLite) |
| `objectstore.py` | `ObjectStoreConnector` — S3-compatible CSV/Parquet object (fsspec, local fallback) |
| `largecsv.py` | `LargeCsvConnector` — chunked, constant-memory CSV-at-scale |
| `ingest.py` | `ingest()` driver + `RawMirror` (content-addressed Parquet snapshots) |

## Source connectors (P0 — nothing flows without these)

All three new connectors are **OPEN-SHELL** (`docs/IP_ARCHITECTURE.md`) and conform
to the exact same `Connector` contract and delta semantics as the file connectors:
`pull(state) -> (DeltaBatch, new_state)`, cell atoms with stable content-addressed
URIs, per-cell update granularity, tombstone deletes, keyless content-addressed row
fallback, JSON-able state, and a `snapshot_tables()` RAW Parquet mirror. The `SQL`
and `ObjectStore` connectors **reuse `_TabularConnector`** so the diff engine, URI
grammar, and state format are byte-for-byte identical to CSV/Parquet.

**Optional-driver policy.** Each connector lazy-imports its optional driver *inside*
`pull()` — importing `ontoforge.cdc` (or instantiating a connector) pulls in only
stdlib + pyarrow, never the heavy driver, so the engine still ships keyless and
offline. A missing driver raises a clear error naming the `connectors` extra
(`pip install 'ontoforge[connectors]'`). The extra declares `sqlalchemy` and `fsspec`
only; DBAPI drivers (psycopg2, PyMySQL) and object-store backends (s3fs, gcsfs) are
user-chosen per URL and intentionally not pinned.

### `SqlConnector` (`sql.py`)

`SqlConnector(source_id, url, table, key_columns=None, *, schema=None, chunk_size=10_000)`.
One interface for Postgres / MySQL / SQLite behind a SQLAlchemy connection URL
(`postgresql+psycopg2://…`, `mysql+pymysql://…`, `sqlite:///file.db`). It introspects
the table (columns + primary key) via SQLAlchemy reflection, pulls rows in
deterministic chunks (`ORDER BY` the key columns, else every column — so the snapshot,
the RAW bytes, and the keyless `~n` suffixes are stable regardless of physical row
order), and maps every cell to a cell atom. `key_columns` omitted ⇒ the introspected
PK is used; no PK ⇒ keyless content-addressed rows. Typed DB values (int/float/str/…)
are preserved through `value_repr`; an in-memory SQLite key int `1` renders as row key
`"1"`, matching CSV/Parquet for the same logical key. A snapshot-diff (not logical
decoding) keeps it deterministic, privilege-free, and offline-testable.

### `ObjectStoreConnector` (`objectstore.py`)

`ObjectStoreConnector(source_id, uri, key_columns=(), *, fmt=None, object_name=None, storage_options=None)`.
Reads one CSV or Parquet object from S3-compatible storage. Transport is behind a
tiny `_open_bytes()` adapter: fsspec when installed (`s3://`, `gcs://`, … via the
registered filesystem), else a clean local-filesystem fallback for `file://` URLs and
bare paths (so it is usable and offline-testable without the extra; a real `s3://`
URL without fsspec raises the actionable error). Format is chosen by suffix unless
`fmt` is given. Bytes are parsed in-memory with the identical CSV/Parquet semantics
as the file connectors (CSV via the shared `parse_csv_text`), so an object and its
on-disk twin yield identical atoms.

### `LargeCsvConnector` (`largecsv.py`)

`LargeCsvConnector(source_id, path, key_columns=(), object_name=None, *, chunk_size=50_000)`.
CSV-at-scale: streams the file in fixed-size row chunks, never materializing all rows
or a full in-memory snapshot. Each row is diffed against prior state the moment it is
read; the RAW snapshot is written incrementally to a content-addressed temp Parquet
via `pq.ParquetWriter` (never held whole). Deletes (keys in prior state that never
reappear) are detected after the stream by key-set difference — O(distinct keys),
the irreducible cost of emitting deletes. Output is **identical to `CsvConnector` at
any `chunk_size`** (tested: 1/7/100/10000) — `chunk_size` bounds memory only.

## Connector contract (`base.py`)

`pull(state) -> (DeltaBatch, new_state)`.

- **State is JSON-able** (`json.dumps` round-trips it) and owned by the caller;
  connectors persist nothing. Every state dict carries `format`
  (`ontoforge.cdc/1`) and `kind` tags so format drift fails loudly instead of
  being silently misread.
- **First pull** (`state=None` or `{}`) emits every atom as `kind="insert"`.
- `snapshot_tables()` exposes the last pulled snapshot for the RAW mirror.
- M1 depends on M0 only through the structural `AtomRegistrar` protocol
  (`register_atoms` — the single slice of `contracts.Ledger` it needs), per the
  §18.1 ownership rules. The integration test suite additionally exercises the
  now-complete `ontoforge.ledger.SqliteLedger` (import only, never edited).

## Tabular delta semantics (`tabular.py`)

Row identity = `key_columns` values (quoted, `|`-joined; the separator is
percent-escaped inside values, so composite keys are unambiguous). Per pull:

1. Every row gets an xxh3 content hash over `(column, value_repr)` pairs.
2. Rows whose hash matches the prior state emit **nothing** (delta
   proportionality: unchanged data costs zero deltas downstream).
3. Changed rows are diffed **per cell**: only cells whose atom_id changed emit
   an `AtomDelta(kind="update", superseded_atom_id=<previous atom at that uri>)`.
4. New rows/columns → inserts; vanished rows/columns → deletes.

**Delete representation.** A delete carries a tombstone `Atom(uri, value=None)`
with `superseded_atom_id` pointing at the vanished atom. Tombstones are never
registered in the ledger (they carry no source value); the disappearance reaches
M0 through `DeltaBatch.changed_atom_ids` — the exact invalidation key set
(whitepaper §4.2 dictionary-side join).

**Keyless rows (documented limitation).** Rows missing a key value (or
`key_columns=[]`) get a content-addressed row key (`row-<xxh3 of all cells>`),
with an encounter-order `~n` suffix for byte-identical duplicates. Such rows
cannot be *tracked* across edits: an edit is observed as delete(old)+insert(new),
never as a per-cell update.

**Value semantics.** CSV values are all strings; a present-but-empty field is
`""`, a missing trailing field is `None` — distinct under `contracts.value_repr`,
hence distinct atoms. Parquet values are pyarrow's typed Python projections
(int/float/str/date/Decimal/...), with float `repr()` round-trip stability via
`value_repr`. Duplicate CSV headers and over-long rows raise `ValueError`
(failing loudly beats silent data loss).

## Doc connector & span stability (`docs.py`)

`.txt`/`.md` files (recursive) → one span atom per paragraph (maximal run of
non-blank lines), `atom://{source}/{doc_path}#span:{start}-{end}`, offsets over
CRLF/CR→LF-normalized text.

**Deviation (recorded):** the contracts default content address
`xxh3(uri, value)` is offset-fragile for spans — inserting a paragraph above an
unchanged one would shift its offsets, change its uri, and mint a new atom_id,
breaking every citation to text that did not change. `DocConnector` therefore
passes an explicit atom_id (the `Atom` dataclass supports this):

```
atom_id = xxh3("span", source_id, doc_path, xxh3(paragraph_text), occurrence)
```

`occurrence` = 1-based index among byte-identical paragraphs in the same doc,
in document order.

**Stability guarantee (tested):**
1. An unchanged paragraph keeps its atom_id across pulls even if it moves;
   pure moves emit **zero** deltas, so no downstream invalidation fires.
2. The registered uri reflects offsets at first sighting; state tracks current
   offsets. Citations resolve via atom_id (content addressing).
3. Duplicate-text paragraphs get per-occurrence identity; any citation into the
   set still resolves to identical text.
4. An edited paragraph supersedes the old one: removed/added paragraphs are
   aligned positionally inside `difflib.SequenceMatcher` replace blocks
   (restricted to paragraphs that do not survive elsewhere — survivors are
   moves) and emitted as `kind="update"`; unpairable leftovers degrade to
   insert/delete.

## Ingest driver & RAW mirror (`ingest.py`)

`ingest(connector, ledger, state, mirror=, pulled_at=)` pulls, registers all
insert/update atoms (they are the provenance leaves every downstream `N[X]`
term bottoms out in — constraint H), skips tombstones, and mirrors snapshots.

**RAW mirror layout:** `{root}/raw/{source_id}/{object_name}/` containing
content-addressed Parquet (`{xxh3(bytes)}.parquet`) plus append-only
`manifest.jsonl` lines with `(cycle, pulled_at, content_hash, row/column
counts)`.

**Design decision:** `(cycle, pulled_at)` live in the manifest, *not* in the
Parquet file metadata, precisely so unchanged data serializes to byte-identical
Parquet, hashes to the same filename, and is never rewritten — the
byte-equality audit (§11.2 M1 tests) falls out of content addressing.

## Encoding robustness

`read_text_robust`: decode `utf-8-sig` first (strips BOM, accepts plain UTF-8),
fall back to `latin-1` (total, byte-preserving). CRLF/CR/LF all accepted; CSV
quoted embedded newlines preserved verbatim; CRLF and LF encodings of the same
data yield identical atoms (uri **and** atom_id).

## Test map (`tests/m1/`)

- `test_tabular_delta.py` — first-pull inserts, per-cell update granularity,
  tombstones, keyless fallback, URI quoting, JSON state round-trip, and
  **mutation fuzzing** (5 fixed seeds × 8 cycles): random insert/update/delete
  between pulls; the delta stream must reconstruct the new snapshot exactly
  from the old against an independent CSV-parsing oracle — no missed, no
  phantom changes (§11.2 M1 "delta completeness on mutation fuzzing",
  "atom-URI stability fuzzing").
- `test_parquet_connector.py` — same contract over typed Parquet; null vs `""`.
- `test_doc_connector.py` — offsets, edit/update pairing, **move keeps
  atom_id with zero deltas**, duplicates, doc deletion, CRLF/BOM/latin-1.
- `test_encoding_robustness.py` — utf-8-sig, CRLF, latin-1, quoted newlines.
- `test_raw_mirror.py` — lossless read-back (null vs `""` distinct),
  byte-stability for unchanged data, cycle-addressable snapshots.
- `test_ingest_driver.py` — registration policy (tombstones excluded),
  dedup-on-content (FakeLedger).
- `test_sqlite_ledger_integration.py` — real `ontoforge.ledger.SqliteLedger`:
  dedup-on-content across two cold pulls of unchanged data, append-only
  supersession, `get_atom` round-trip fidelity.

## Deviations / notes

- Explicit span atom_id (see above) — required by the citation-stability spec
  line; uses the `Atom.atom_id` field the contracts dataclass provides.
- **SQL / object-store / large-CSV connectors (§17.3 backlog promotion):** now
  implemented behind the optional `connectors` extra as deterministic snapshot
  diffs (see "Source connectors" above). They are P0 — the MVP file-only scope is
  superseded. Postgres *logical decoding* / binlog CDC, API-cursor, and
  OSM/GLEIF/EDGAR connectors remain backlog: a snapshot-diff over a SQLAlchemy
  table covers the relational source need privilege-free and offline-testable.
- Cell delete granularity: a vanished row tombstones each of its cells (one
  delta per atom), keeping `changed_atom_ids` exact for invalidation.
