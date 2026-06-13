# The WILD corpus — real internet datasets, autonomously ontologized

`fixtures/wild/` is a committed snapshot of **~280 REAL datasets downloaded
from the public internet**, mixed domains, deliberately uncurated semantics:
some genuinely joinable (an aviation cluster, an ISO-coded world-data
cluster), many random silos. It is the autonomy showcase — point OntoForge at
the wild internet, watch it build the ontology:

```bash
ontoforge demo wild wild_project        # init -> ingest -> profile -> induce
                                        #   -> resolve -> materialize
```

Module: `src/ontoforge/estates/wild.py` (fetcher + manifest + loader helpers).
Fetcher entry points: `scripts/fetch_wild_corpus.py` or
`ontoforge.estates.wild:fetch` (NETWORK; everything downstream is offline).
Tests: `tests/wild/` (zero network; one `@pytest.mark.slow` skip-if-offline
fetcher smoke).

## Sources and attribution

All five sources are public, open datasets fetched with the research UA
`OntoForge-Research/0.1 (glenn.hubbard.career@gmail.com)`, 3 retries per URL,
and a hard ≤15 GitHub API call budget per fetch (actual usage is recorded in
the manifest; raw.githubusercontent.com content fetches are rate-unlimited).

| prefix | source | what | license |
|---|---|---|---|
| `of_` | [OpenFlights](https://openflights.org/data.php) (`jpatokal/openflights`) | airports, airlines, routes, planes, countries — the genuinely joinable aviation cluster | Open Database License (ODbL) + Database Contents License |
| `ds_` | the [`datasets` GitHub org](https://github.com/datasets) (Frictionless core data) | world data: GDP, population, ISO country/currency/language codes, prices, emissions, … — ISO codes thread through dozens of tables | per-repo `datapackage.json` licenses, predominantly ODC-PDDL-1.0; recorded per dataset in the manifest |
| `fte_` | [FiveThirtyEight](https://github.com/fivethirtyeight/data) | the wonderful randoms: bad drivers, Avengers, Bechdel, polls, … | CC BY 4.0 |
| `vg_` | [vega-datasets](https://github.com/vega/vega-datasets) | example corpora (cars, gapminder, stocks, zipcodes, …) | BSD-3-Clause repo; public example data |
| `sb_` | [seaborn-data](https://github.com/mwaskom/seaborn-data) | iris, titanic, penguins, taxis, … | public example datasets collected for the seaborn docs |

Per-dataset attribution lives in `fixtures/wild/manifest.lock.json`: every
entry records `{slug, url, source, license_note, rows_kept, cols, sha256}`,
where `url` is the exact raw file fetched and `license_note` is taken from the
repo's `datapackage.json` when one exists. **License screen:** datasets whose
declared license is non-commercial / no-derivatives / restrictive vendor terms
(CC-BY-NC, CC-BY-ND, John Snow Labs, BIS terms) are rejected at admission and
never written — this repo is Apache-2.0 and redistributes 150-row excerpts,
so only open-redistributable data is committed.

## Normalization contract

Every admitted dataset is:

1. parsed with pandas — separator sniffed (`sep=None, engine="python"`),
   utf-8 with latin-1 fallback, wart-preserving strings (`dtype=str,
   keep_default_na=False`);
2. gated: ≥ 20 rows (pre-truncation), 2–60 columns;
3. truncated to the **first 150 rows** (breadth over depth — hundreds of
   shallow tables cap pipeline cost while keeping schema + value semantics);
   columns that collide under the induction engine's name normalizer are
   deduplicated keep-first (wild data loves repeating groups — `Crop 1..3`,
   the classic 1NF violation — and STRATA's candidate ids require per-table
   distinct normalized column names: bare numeric tokens are dropped by its
   normalizer, so the repeats would alias);
4. written as UTF-8 comma CSV named `<source prefix>_<slug>.csv`;
5. recorded in `manifest.lock.json` with the sha256 of the written file.

Downloads are capped at 6 MB per file (cut at the last full line — only the
first 150 rows survive anyway); GitHub API misses and per-URL failures are
tolerated and recorded in the manifest's per-source stats.

### OpenFlights headers

The `.dat` snapshots are headerless CSV; the fetcher adds the documented
headers transcribed verbatim from <https://openflights.org/data.php>
(`wild.OPENFLIGHTS_HEADERS`), e.g. `Airport ID, Name, City, Country, IATA,
ICAO, Latitude, Longitude, Altitude, Timezone, DST, Tz database timezone,
Type, Source` for `airports.dat`.

### Documented deviation: reference-closure truncation (OpenFlights)

A plain head-150 of every OpenFlights file would keep the cluster's tables but
sever every join: `routes.dat`'s first rows are Aeroflot-regional routes
between CIS airports, while `airports.dat`'s first rows are Papua New Guinea
and Canada — zero overlap, and the flagship "genuinely joinable" cluster would
silently become five silos. The fetcher therefore keeps the first 150 routes
verbatim, then truncates `airports`/`airlines` to the rows those routes
reference (in file order) topped up with the file head to exactly 150 rows,
and closes `countries` over the kept airports'/airlines' country names the
same way. Same row budget, same "first rows" spirit, joins honestly preserved
— `tests/wild/test_corpus.py::test_openflights_cluster_is_genuinely_joinable`
pins the guarantee. All other sources use the plain head.

## What the engine finds (full breadth)

Numbers from a full run on the committed snapshot (282 datasets; Python 3.12,
8-core x86-64 macOS laptop; see "Capacity" below for stage timings):
**364 induced classes, 14,846 INDs, 169 link properties, 18 cross-table
identity-domain resolutions; 34,497 entities / 137,154 cells / 25,597 links
materialized into HEARTH.** Highlights:

- the `airports↔routes` join surface is discovered in the M3 IND layer via
  IATA codes (`of_routes."Source airport" → of_airports.IATA`, coverage 0.97)
  and via OpenFlights ids (coverage 0.99). `routes` is the textbook keyless
  fact table (its natural key is 3 columns, over the profiler's 2-column
  candidate-key cap), so it backs no class of its own — the cluster's
  class-level links surface as `airports/airlines → countries` plus the IND
  evidence;
- the world-data ISO thread materializes both as direct links (World-Bank
  indicator tables linking into `ds_country_codes`' unique ISO-3 column) and
  as G-join hub classes over shared country-code/name domains spanning
  datasets — including cross-SOURCE links (`of_countries → ds_country_codes`);
- the silos stay silos — and a handful of spurious INDs appear (a percentage
  column numerically ⊆ a 1–150 id column), which is the honest price of
  schema-blind discovery at 150-row scale; they are exactly the kind of
  low-evidence edge the spine's calibration is for.

## Capacity (measured)

Stage-by-stage timings of the generic pipeline at full breadth (~280 tables ×
≤150 rows), measured on the committed snapshot in one run (Python 3.12,
8-core x86-64 macOS laptop; the subset column is the fixed 12-dataset smoke
mix pinned in `tests/wild/test_pipeline_smoke.py`):

| stage | 12-dataset smoke subset | full corpus (282) |
|---|---|---|
| discover (load + per-table profile) | 23.6 s | 98.4 s |
| profile_estate (cross-table INDs) | 0.8 s | 14.6 s |
| induce (STRATA) | 0.5 s | 48.9 s |
| resolve (generic ER) | 0.0 s | 8.1 s |
| materialize (HEARTH world) | 2.4 s | 58.3 s |
| **total** | **27.3 s** | **228.3 s (~3.8 min)** |

Discover's per-table profiling fans out to a spawn-based process pool
(`discover_sources(..., max_workers=...)`; profiles are independent and the
estate dict is byte-identical to a serial run). On the same machine and run
conditions the serial bill was **206.5 s** — the pool cuts it ~2.1×, with the
wall clock bounded by the single widest table (`fte_food_world_cup…`, 48
columns: TANE-lattice FD search is ~C(cols, 4)·rows, so the corpus's profiling
cost concentrates in its few widest tables). An auto-gate keeps small or
single-heavy-table corpora — including the smoke subset, whose cost is one
wide table — on the serial path, where a pool could only add spawn overhead.

The profile/IND budget stays far under the ~10-minute capacity line, so
`ontoforge demo wild` runs at FULL breadth (`wild.DEMO_ROW_LIMIT = None`); if
the corpus ever grows past the line, set `DEMO_ROW_LIMIT = 100` and the demo's
sticky `--limit` plumbing subsamples every table without refetching. A
measured `ontoforge demo wild` end-to-end run (init → ingest → profile →
induce → resolve → materialize, with the demo memo, same machine and day as
the capacity table) took **5 m 27 s wall** (423 s user — discover's profiling
pool overlapping cores) and reproduced the capacity run's counts exactly —
the pipeline is deterministic on the committed snapshot.

## Wheel exclusion

`fixtures/wild/` is committed to the repo but **not shipped in the wheel**
(the wheel packages `src/ontoforge` only — see `[tool.hatch.build]` in
`pyproject.toml`). `ontoforge demo wild` therefore needs a source checkout,
exactly like the aviation demo; from a wheel install use `ontoforge demo
meridian`, whose corpus regenerates from code.

## Refreshing the snapshot

```bash
uv run python scripts/fetch_wild_corpus.py                     # full (network)
uv run python scripts/fetch_wild_corpus.py --sources seaborn   # partial: other
                                                               # sources carried
                                                               # over unchanged
uv run pytest tests/wild -q                                    # offline gates
```

Landing gates (enforced by the script's exit code and pinned by
`tests/wild/test_manifest.py`): **≥ 150 datasets** (aim 200+; the committed
snapshot has 282: 176 datasets-org + 60 FiveThirtyEight + 21 vega + 20
seaborn + 5 OpenFlights) and **≤ 20 MB total** (currently 3.2 MB). The fetch
itself: 4 GitHub API calls, ~4–9 minutes depending on CDN warmth, 8 datasets
license-screened out.
