# Portability: wheel, Docker, AMBER

How OntoForge moves: as a **wheel** (the engine), as a **Docker image** (the
demo appliance), and as an **AMBER bundle** (a materialized world with its
provenance, independent of OntoForge itself).

## 1. The wheel

```sh
uv build                       # -> dist/ontoforge-<version>-py3-none-any.whl
```

The wheel ships `src/ontoforge` only (`[tool.hatch.build.targets.wheel]
packages = ["src/ontoforge"]`). **No fixture CSVs are included** — verified by
`tests/meridian` CI discipline and easy to re-check:

```sh
python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist() if '.csv' in n or 'fixtures' in n])"
# -> []
```

What still works from a bare install (validated against a clean venv):

| capability | mechanism |
|---|---|
| `ontoforge demo meridian <dir>` | the Meridian corpus generator is **code**, not data: `ontoforge.estates.meridian_gen` regenerates the 10-table estate byte-identically (seed 7) into `<dir>/meridian_source`, then runs init → ingest → profile → induce → resolve → materialize |
| `ontoforge init -p X --source <any dir of CSV/Parquet>` | the generic engine needs no bundled data at all |
| `ontoforge serve -p X` | the web UI's static assets live inside the package (`ontoforge/server/static`) |
| `ontoforge demo aviation` | **source checkout only** — the aviation fixtures are deliberately excluded from the wheel and that estate's loader reads repo-relative files; the CLI degrades with an explicit message pointing at the meridian demo |

`scripts/build_meridian_corpus.py` is a thin shim over the packaged generator
(`python -m ontoforge.estates.meridian_gen` works too), so the repo fixture
tree and any wheel-side regeneration can never drift apart: the meridian test
suite asserts `fixtures/meridian` equals the seed-7 generator output
byte-for-byte.

## 2. Docker

```sh
docker build -t ontoforge .
docker run -p 8765:8765 -v ontoforge-data:/data ontoforge
# open http://localhost:8765
```

`Dockerfile` is a standard two-stage build:

1. **build stage** (`python:3.12-slim` + the official `uv` binary): copies
   `pyproject.toml` + `src/` only (`.dockerignore` excludes fixtures, tests,
   docs, scripts) and runs `uv build --wheel`;
2. **runtime stage** (`python:3.12-slim`): installs the wheel into
   `/opt/ontoforge` with `uv pip`, `EXPOSE 8765`, and a CMD that materializes
   the Meridian demo into `/data` on first start (`ontoforge demo meridian
   /data`, ~2 minutes, skipped when `/data/state.json` already exists) and
   then `exec`s `ontoforge serve -p /data --host 0.0.0.0 --port 8765`.

The image needs no network at runtime and contains no fixture files — the
demo estate regenerates from code inside the container, exactly like the
wheel path.

> **Status:** the Dockerfile is validated by inspection only — the Docker
> daemon was not running on the development machine, so `docker build` has not
> been executed here. The wheel-install-into-clean-venv path it automates WAS
> executed and verified (demo + ask + serve assets).

## 3. AMBER: the world itself is portable

A materialized project is more than the engine: the induced ontology, the
bitemporal entity store, and the provenance ledger together are the asset.
M14 AMBER freezes all of it into a self-describing bundle:

```sh
ontoforge snapshot <bundle_dir> -p <project>
```

The bundle is **plain Parquet + Turtle + JSONL** (no proprietary formats, no
OntoForge dependency to read it): the ontology as OWL 2 + SHACL, every HEARTH
value/link shard with full bitemporal history, the current-stance RDF graph
for any SPARQL engine, every transform as readable SQL, the decision ledger,
and the complete interned provenance term/atom tables. `manifest.json` carries
per-file sha256s and an explicit capability-loss declaration.
`ontoforge.amber.verify(bundle_dir)` re-checks hashes and provenance
completeness; `import_bundle(bundle_dir, new_root)` rehydrates a working
(Hearth, Ontology, Ledger) triple on another machine.

So the portability story composes end to end: build the wheel anywhere, run
the demo appliance in Docker, and hand the resulting WORLD to someone else as
an AMBER bundle they can audit without trusting your runtime.
