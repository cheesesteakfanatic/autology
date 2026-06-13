"""WILD corpus: hundreds of REAL datasets fetched from the public internet.

The autonomy showcase corpus — mixed domains, deliberately uncurated semantics:
a genuinely joinable aviation cluster (OpenFlights), a world-data cluster where
ISO country/currency codes thread through dozens of tables (the `datasets`
GitHub org), and a long tail of wonderful randoms (FiveThirtyEight, vega,
seaborn). Point the generic pipeline at it and watch the ontology emerge.

Two strictly separated phases:

* **fetch time** (network): :func:`fetch` downloads, normalizes and snapshots
  every dataset into ``fixtures/wild/`` plus ``manifest.lock.json``. Run via
  ``scripts/fetch_wild_corpus.py`` or ``ontoforge.estates.wild:fetch``.
* **run time** (zero network): the committed snapshot is the corpus. Tests and
  `ontoforge demo wild` only ever read the pinned files.

Normalization contract (every admitted dataset):

* parsed with pandas (separator sniffed via ``engine="python"``; encoding
  utf-8 with latin-1 fallback), wart-preserving strings;
* gates: >= MIN_ROWS rows, MIN_COLS..MAX_COLS columns;
* truncated to the FIRST ``ROW_CAP`` rows (breadth over depth — the corpus is
  hundreds of tables, not deep tables; this caps pipeline cost);
* written as UTF-8 comma CSV named ``<source prefix>_<slug>.csv``.

Documented deviation (recorded in docs/WILD_CORPUS.md): the OpenFlights
cluster uses *reference-closure* truncation instead of a plain head — the
first ROW_CAP routes are kept verbatim, then airports/airlines keep the rows
those routes reference (in file order) topped up with the file head to exactly
ROW_CAP rows. A plain head would keep the cluster's tables but sever every
cross-references (routes' head lists Aeroflot-regional routes; airports' head
lists Papua New Guinea), silently turning the flagship joinable cluster into
silos. Same row budget, same "first rows" spirit, joins preserved honestly.

OpenFlights ``.dat`` files are headerless; the documented headers below are
transcribed verbatim from https://openflights.org/data.php (fetched 2026-06-12).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import pandas as pd

__all__ = [
    "MANIFEST_NAME",
    "OPENFLIGHTS_HEADERS",
    "ROW_CAP",
    "default_fixtures_dir",
    "fetch",
    "load_manifest",
]

USER_AGENT = "OntoForge-Research/0.1 (glenn.hubbard.career@gmail.com)"
RETRIES = 3
TIMEOUT = 60.0
#: per-download byte cap; bodies larger than this are cut at the last full line
#: (we only ever keep the first ROW_CAP rows, so the tail is dead weight)
MAX_FETCH_BYTES = 6 * 1024 * 1024

ROW_CAP = 150
MIN_ROWS = 20
MIN_COLS = 2
MAX_COLS = 60

MANIFEST_NAME = "manifest.lock.json"

#: demo row limit (sticky --limit): None = full breadth. The measured full
#: corpus runs comfortably under the capacity budget (timings in
#: docs/WILD_CORPUS.md), so the demo runs unlimited.
DEMO_ROW_LIMIT: Optional[int] = None

_GH_RAW = "https://raw.githubusercontent.com"
_GH_API = "https://api.github.com"

#: documented column headers for the headerless OpenFlights .dat snapshots,
#: verbatim from the format tables at https://openflights.org/data.php
OPENFLIGHTS_HEADERS: dict[str, list[str]] = {
    "airports": [
        "Airport ID", "Name", "City", "Country", "IATA", "ICAO",
        "Latitude", "Longitude", "Altitude", "Timezone", "DST",
        "Tz database timezone", "Type", "Source",
    ],
    "airlines": [
        "Airline ID", "Name", "Alias", "IATA", "ICAO", "Callsign",
        "Country", "Active",
    ],
    "routes": [
        "Airline", "Airline ID", "Source airport", "Source airport ID",
        "Destination airport", "Destination airport ID", "Codeshare",
        "Stops", "Equipment",
    ],
    "planes": ["Name", "IATA code", "ICAO code"],
    "countries": ["name", "iso_code", "dafif_code"],
}

_OPENFLIGHTS_LICENSE = "Open Database License (ODbL) — openflights.org/data.php"
_FTE_LICENSE = "CC BY 4.0 — github.com/fivethirtyeight/data"
_VEGA_LICENSE = "BSD-3-Clause repo; public example data — github.com/vega/vega-datasets"
_SEABORN_LICENSE = "public example datasets collected for seaborn docs — github.com/mwaskom/seaborn-data"
_DATASETS_FALLBACK_LICENSE = "open data (see repo; datasets-org packages are typically PDDL/CC0/ODC-BY)"

SEABORN_DATASETS = [
    "iris", "tips", "titanic", "penguins", "diamonds", "planets", "flights",
    "exercise", "mpg", "taxis", "car_crashes", "anagrams", "attention",
    "brain_networks", "dots", "fmri", "gammas", "geyser", "glue", "healthexp",
    "seaice", "anscombe",
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: licenses we must not redistribute excerpts of inside an Apache-2.0 repo:
#: non-commercial / no-derivative CC variants and restrictive vendor terms.
_LICENSE_DENY_RE = re.compile(
    r"(cc[- ]?by[- ]?nc|non[- ]?commercial|cc[- ]?by[- ]?nd|no[- ]?derivatives?"
    r"|john snow|bis[- ]?terms)",
    re.IGNORECASE,
)


# --------------------------------------------------------------- snapshot side


def default_fixtures_dir() -> Path:
    """Repo-relative default (editable install): <repo>/fixtures/wild.

    The snapshot is committed in the repo but EXCLUDED from the wheel (the
    wheel packages ``src/ontoforge`` only) — `ontoforge demo wild` needs a
    source checkout, exactly like the aviation demo.
    """
    return Path(__file__).resolve().parents[3] / "fixtures" / "wild"


def load_manifest(fixtures_dir: str | Path | None = None) -> dict[str, Any]:
    base = Path(fixtures_dir) if fixtures_dir is not None else default_fixtures_dir()
    return json.loads((base / MANIFEST_NAME).read_text(encoding="utf-8"))


def slugify(name: str) -> str:
    """Stable lowercase identifier (same alphabet as pipeline discovery slugs)."""
    s = _SLUG_RE.sub("_", name.lower()).strip("_")
    return s or "dataset"


# ------------------------------------------------------------------ HTTP layer


class _Budget:
    """GitHub API call budget (unauthenticated limit is 60/hr; we spend <= 15)."""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.used = 0

    def take(self) -> None:
        if self.used >= self.max_calls:
            raise RuntimeError(f"GitHub API budget exhausted ({self.max_calls} calls)")
        self.used += 1


def _http_bytes(
    url: str,
    *,
    retries: int = RETRIES,
    timeout: float = TIMEOUT,
    max_bytes: int = MAX_FETCH_BYTES,
) -> Optional[bytes]:
    """GET ``url`` with the project UA; up to ``retries`` attempts.

    Returns the body (cut at the last full line when over ``max_bytes``), or
    None on failure. 4xx responses are misses, not transient errors — they do
    not retry (the 538 primary-CSV probe tolerates misses by design).
    """
    last_err: Optional[BaseException] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:  # over cap: drop the partial last line
                cut = body.rfind(b"\n", 0, max_bytes)
                body = body[: cut + 1] if cut > 0 else body[:max_bytes]
            return body
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                return None  # a real miss (404 etc.) — tolerated, never retried
            last_err = e
            if e.code == 429:  # rate limited: back off harder before retrying
                time.sleep(10.0 * (attempt + 1))
        except Exception as e:  # URLError, timeout, IncompleteRead, ...
            last_err = e
        time.sleep(min(2.0 * (attempt + 1), 5.0))
    del last_err
    return None


def _api_json(url: str, budget: _Budget) -> Optional[Any]:
    budget.take()
    body = _http_bytes(url, max_bytes=20 * 1024 * 1024)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


# ------------------------------------------------------------------- normalize


def _decode(raw: bytes) -> str:
    """utf-8 first, latin-1 fallback (some 538 exports are cp1252-ish)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_csv_bytes(raw: bytes, *, headers: Optional[list[str]] = None) -> pd.DataFrame:
    """Raw download -> wart-preserving string DataFrame.

    Separator is sniffed (``sep=None, engine="python"``) for headed files;
    headerless OpenFlights .dat files use the documented comma + headers.
    """
    text = _decode(raw)
    kwargs: dict[str, Any] = dict(dtype=str, keep_default_na=False, engine="python")
    if headers is not None:
        kwargs.update(sep=",", header=None, names=headers)
    else:
        kwargs.update(sep=None, header=0)
    try:
        return pd.read_csv(io.StringIO(text), **kwargs)
    except Exception:
        # one tolerant retry: skip malformed lines (real-world CSVs are wild)
        return pd.read_csv(io.StringIO(text), on_bad_lines="skip", **kwargs)


def _engine_normalize_name(name: str) -> str:
    """The induction engine's canonical column-name form (STRATA drops bare
    numeric tokens, so 'Investor Country 1/2/3' all collapse to
    'investor_country'). Falls back to the local slug if strata is absent."""
    try:
        from ontoforge.strata._norm import normalize_name

        return normalize_name(name) or slugify(name)
    except Exception:
        return slugify(name)


def dedupe_normalized_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop columns that collide under the engine's name normalizer (keep the
    FIRST of each group). Wild tables love repeating groups ('Crop 1..3' —
    the classic 1NF violation); the engine requires per-table distinct
    normalized column names, so the snapshot must satisfy that invariant."""
    seen: set[str] = set()
    keep: list[str] = []
    for c in df.columns:
        n = _engine_normalize_name(str(c))
        if n in seen:
            continue
        seen.add(n)
        keep.append(c)
    return df[keep] if len(keep) < df.shape[1] else df


def normalize(df: pd.DataFrame, *, row_cap: int = ROW_CAP) -> Optional[pd.DataFrame]:
    """Apply the admission gates and the head-``row_cap`` truncation.

    Returns the normalized frame, or None when the dataset fails a gate
    (too few rows pre-truncation, too few / too many columns after the
    normalized-name dedupe).
    """
    if df is None or len(df) < MIN_ROWS:
        return None
    if df.shape[1] > MAX_COLS:
        return None
    df = df.head(row_cap).copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = dedupe_normalized_columns(df)
    if not (MIN_COLS <= df.shape[1] <= MAX_COLS):
        return None
    return df


def _closure_truncate(
    df: pd.DataFrame, column: str, referenced: set[str], *, row_cap: int = ROW_CAP
) -> pd.DataFrame:
    """Reference-closure truncation (OpenFlights deviation, module docstring):
    rows whose ``column`` value is in ``referenced`` (file order) first, topped
    up with the remaining file head to exactly ``row_cap`` rows, emitted in
    original file order."""
    values = df[column].astype(str).str.strip()
    keep = df.index[values.isin(referenced)][:row_cap]
    fill = df.index.difference(keep, sort=False)[: max(0, row_cap - len(keep))]
    kept = keep.union(fill, sort=False).sort_values()
    return df.loc[kept]


# ------------------------------------------------------------------ the fetcher


class _Corpus:
    """Accumulates admitted datasets + per-source fetch stats, writes the lot."""

    def __init__(self, dest: Path, row_cap: int, log: Callable[[str], None]) -> None:
        self.dest = dest
        self.row_cap = row_cap
        self.log = log
        self.datasets: list[dict[str, Any]] = []
        self.slugs: set[str] = set()
        self.stats: dict[str, dict[str, Any]] = {}

    def _stat(self, source: str) -> dict[str, Any]:
        return self.stats.setdefault(
            source,
            {"attempted": 0, "kept": 0, "rejected": 0, "failed": 0,
             "license_screened": 0, "misses": []},
        )

    def unique_slug(self, base: str) -> str:
        slug, n = base, 2
        while slug in self.slugs:
            slug, n = f"{base}_{n}", n + 1
        return slug

    def admit(
        self,
        source: str,
        slug: str,
        url: str,
        license_note: str,
        df: Optional[pd.DataFrame],
        *,
        pre_truncated: bool = False,
    ) -> bool:
        """Gate + truncate + write one dataset; record manifest row and stats."""
        st = self._stat(source)
        st["attempted"] += 1
        if df is None:
            st["failed"] += 1
            st["misses"].append(url)
            return False
        if _LICENSE_DENY_RE.search(license_note):
            st["license_screened"] += 1
            self.log(f"  - {slug}: license screened out ({license_note})")
            return False
        out = df if pre_truncated else normalize(df, row_cap=self.row_cap)
        if out is None or not (MIN_COLS <= out.shape[1] <= MAX_COLS) or len(out) < MIN_ROWS:
            st["rejected"] += 1
            return False
        slug = self.unique_slug(slug)
        path = self.dest / f"{slug}.csv"
        out.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.slugs.add(slug)
        self.datasets.append(
            {
                "slug": slug,
                "url": url,
                "source": source,
                "license_note": license_note,
                "rows_kept": int(len(out)),
                "cols": int(out.shape[1]),
                "sha256": digest,
            }
        )
        st["kept"] += 1
        self.log(f"  + {slug}.csv  ({len(out)} rows x {out.shape[1]} cols)")
        return True

    def write_manifest(self, api_calls_used: int) -> dict[str, Any]:
        self.datasets.sort(key=lambda d: d["slug"])
        total_bytes = sum((self.dest / f"{d['slug']}.csv").stat().st_size for d in self.datasets)
        manifest = {
            "version": 1,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "user_agent": USER_AGENT,
            "row_cap": self.row_cap,
            "gates": {"min_rows": MIN_ROWS, "min_cols": MIN_COLS, "max_cols": MAX_COLS},
            "stats": {
                "datasets_kept": len(self.datasets),
                "total_bytes": total_bytes,
                "github_api_calls": api_calls_used,
                "per_source": {
                    s: {k: v for k, v in st.items() if k != "misses"} | {"misses": st["misses"][:40]}
                    for s, st in sorted(self.stats.items())
                },
            },
            "datasets": self.datasets,
        }
        (self.dest / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest


# ------------------------------------------------------------- source planners


def _fetch_openflights(corpus: _Corpus) -> None:
    """The genuinely joinable aviation cluster, reference-closure truncated."""
    corpus.log("[openflights] fetching the joinable aviation cluster")
    base = f"{_GH_RAW}/jpatokal/openflights/master/data"
    urls = {name: f"{base}/{name}.dat" for name in OPENFLIGHTS_HEADERS}
    frames: dict[str, Optional[pd.DataFrame]] = {}
    for name, url in urls.items():
        raw = _http_bytes(url)
        try:
            frames[name] = parse_csv_bytes(raw, headers=OPENFLIGHTS_HEADERS[name]) if raw else None
        except Exception:
            frames[name] = None

    routes = frames.get("routes")
    truncated: dict[str, Optional[pd.DataFrame]] = dict(frames)
    if routes is not None:
        routes = routes.head(corpus.row_cap)
        truncated["routes"] = routes

        def refs(cols: list[str]) -> set[str]:
            out: set[str] = set()
            for c in cols:
                out |= {v for v in routes[c].astype(str).str.strip() if v and v != r"\N"}
            return out

        if frames.get("airports") is not None:
            truncated["airports"] = _closure_truncate(
                frames["airports"], "Airport ID",
                refs(["Source airport ID", "Destination airport ID"]),
                row_cap=corpus.row_cap,
            )
        if frames.get("airlines") is not None:
            truncated["airlines"] = _closure_truncate(
                frames["airlines"], "Airline ID", refs(["Airline ID"]), row_cap=corpus.row_cap
            )
        if frames.get("countries") is not None:
            country_refs: set[str] = set()
            for t in ("airports", "airlines"):
                df = truncated.get(t)
                if df is not None:
                    country_refs |= {
                        v for v in df["Country"].astype(str).str.strip() if v and v != r"\N"
                    }
            truncated["countries"] = _closure_truncate(
                frames["countries"], "name", country_refs, row_cap=corpus.row_cap
            )
    for name in ("planes", "countries"):
        if truncated.get(name) is not None:
            truncated[name] = truncated[name].head(corpus.row_cap)

    for name in OPENFLIGHTS_HEADERS:
        df = truncated.get(name)
        if df is not None:
            df = df.copy()
            df.columns = [str(c).strip() for c in df.columns]
            if len(df) < MIN_ROWS or not (MIN_COLS <= df.shape[1] <= MAX_COLS):
                df = None
        corpus.admit(
            "openflights", f"of_{slugify(name)}", urls[name], _OPENFLIGHTS_LICENSE,
            df, pre_truncated=True,
        )


def _fetch_seaborn(corpus: _Corpus) -> None:
    corpus.log("[seaborn] fetching example datasets")
    for name in SEABORN_DATASETS:
        url = f"{_GH_RAW}/mwaskom/seaborn-data/master/{name}.csv"
        raw = _http_bytes(url)
        df = None
        if raw:
            try:
                df = parse_csv_bytes(raw)
            except Exception:
                df = None
        corpus.admit("seaborn", f"sb_{slugify(name)}", url, _SEABORN_LICENSE, df)


def _fetch_vega(corpus: _Corpus, budget: _Budget) -> None:
    corpus.log("[vega-datasets] listing data/ (1 API call)")
    listing = _api_json(f"{_GH_API}/repos/vega/vega-datasets/contents/data", budget)
    names = sorted(
        e["name"] for e in (listing or [])
        if isinstance(e, dict) and e.get("type") == "file" and str(e.get("name", "")).endswith(".csv")
    )
    for name in names:
        url = f"{_GH_RAW}/vega/vega-datasets/main/data/{name}"
        raw = _http_bytes(url)
        df = None
        if raw:
            try:
                df = parse_csv_bytes(raw)
            except Exception:
                df = None
        corpus.admit("vega", f"vg_{slugify(name[:-4])}", url, _VEGA_LICENSE, df)


def _fetch_fivethirtyeight(corpus: _Corpus, budget: _Budget, max_kept: int = 60) -> None:
    """The wonderful randoms: ~60 directories' primary CSVs (misses tolerated).

    One git-trees API call lists every file; each directory's *primary* CSV is
    ``<dir>/<dir>.csv`` when present, else the first CSV in the directory.
    """
    corpus.log("[fivethirtyeight] listing the git tree (1 API call)")
    tree = _api_json(
        f"{_GH_API}/repos/fivethirtyeight/data/git/trees/master?recursive=1", budget
    )
    by_dir: dict[str, list[str]] = {}
    for e in (tree or {}).get("tree", []) if isinstance(tree, dict) else []:
        path = str(e.get("path", ""))
        if e.get("type") != "blob" or not path.lower().endswith(".csv"):
            continue
        parts = path.split("/")
        if len(parts) == 2 and not parts[0].startswith("."):
            by_dir.setdefault(parts[0], []).append(parts[1])

    kept = 0
    for d in sorted(by_dir):
        if kept >= max_kept:
            break
        files = sorted(by_dir[d])
        primary = f"{d}.csv" if f"{d}.csv" in files else files[0]
        url = f"{_GH_RAW}/fivethirtyeight/data/master/{d}/{primary}"
        raw = _http_bytes(url)
        df = None
        if raw:
            try:
                df = parse_csv_bytes(raw)
            except Exception:
                df = None
        stem = slugify(primary[:-4])
        slug = f"fte_{slugify(d)}" if stem == slugify(d) else f"fte_{slugify(d)}_{stem}"
        if corpus.admit("fivethirtyeight", slug, url, _FTE_LICENSE, df):
            kept += 1


def _datapackage_csvs(dp: dict[str, Any]) -> list[str]:
    """data/*.csv resource paths out of a frictionless datapackage.json."""
    out: list[str] = []
    for res in dp.get("resources", []) or []:
        if not isinstance(res, dict):
            continue
        paths = res.get("path") or res.get("url") or []
        if isinstance(paths, str):
            paths = [paths]
        for p in paths:
            if isinstance(p, str) and p.lower().endswith(".csv"):
                out.append(p)
    return out


def _datapackage_license(dp: dict[str, Any]) -> Optional[str]:
    lic = dp.get("licenses") or dp.get("license")
    if isinstance(lic, list) and lic and isinstance(lic[0], dict):
        name = lic[0].get("name") or lic[0].get("id") or lic[0].get("title")
        name = str(name).strip() if name else ""
        return f"{name} (datapackage.json)" if name else None
    if isinstance(lic, str) and lic.strip():
        return f"{lic.strip()} (datapackage.json)"
    return None


def _fetch_datasets_org(corpus: _Corpus, budget: _Budget, csvs_per_repo: int = 3) -> None:
    """The world-data joinable cluster: the `datasets` GitHub org (Frictionless
    core data). Repo list costs <= 3 API calls; per-repo discovery rides the
    rate-unlimited raw host via each repo's datapackage.json."""
    corpus.log("[datasets-org] listing repos (<= 3 API calls)")
    repos: list[dict[str, Any]] = []
    for page in (1, 2, 3):
        batch = _api_json(f"{_GH_API}/orgs/datasets/repos?per_page=100&page={page}", budget)
        if not isinstance(batch, list) or not batch:
            break
        repos.extend(e for e in batch if isinstance(e, dict) and e.get("name"))
        if len(batch) < 100:
            break

    for repo in sorted(repos, key=lambda r: str(r["name"])):
        name = str(repo["name"])
        branches: list[str] = []
        for b in (str(repo.get("default_branch") or "main"), "main", "master"):
            if b not in branches:
                branches.append(b)
        dp, branch = None, branches[0]
        for b in branches:
            raw = _http_bytes(f"{_GH_RAW}/datasets/{name}/{b}/datapackage.json", retries=1)
            if raw:
                try:
                    dp = json.loads(_decode(raw))
                    branch = b
                    break
                except Exception:
                    dp = None
        csv_paths = _datapackage_csvs(dp)[:csvs_per_repo] if isinstance(dp, dict) else []
        if not csv_paths:  # standard-layout fallback: data/<name>.csv
            csv_paths = [f"data/{name}.csv"]
        license_note = (
            _datapackage_license(dp) if isinstance(dp, dict) else None
        ) or _DATASETS_FALLBACK_LICENSE

        repo_slug = slugify(name)
        for path in csv_paths:
            if path.startswith(("http://", "https://")):
                url = path
            else:
                url = f"{_GH_RAW}/datasets/{name}/{branch}/{path.lstrip('./')}"
            stem = slugify(Path(path).stem)
            slug = f"ds_{repo_slug}" if stem in (repo_slug, "data") else f"ds_{repo_slug}_{stem}"
            raw = _http_bytes(url)
            df = None
            if raw:
                try:
                    df = parse_csv_bytes(raw)
                except Exception:
                    df = None
            corpus.admit("datasets-org", slug, url, license_note, df)


_SOURCES: dict[str, str] = {
    "openflights": "OpenFlights .dat cluster (joinable: airports/airlines/routes/planes/countries)",
    "datasets-org": "github.com/datasets org (world data; ISO codes thread through)",
    "fivethirtyeight": "fivethirtyeight/data primary CSVs",
    "vega": "vega/vega-datasets data/*.csv",
    "seaborn": "mwaskom/seaborn-data example CSVs",
}


def fetch(
    dest: str | Path | None = None,
    *,
    sources: Optional[Iterable[str]] = None,
    api_budget: int = 15,
    row_cap: int = ROW_CAP,
    verbose: bool = True,
) -> dict[str, Any]:
    """Download + normalize the wild corpus into ``dest`` (NETWORK).

    Callable as ``ontoforge.estates.wild:fetch``; the script wrapper is
    ``scripts/fetch_wild_corpus.py``. Returns the manifest dict it wrote.
    """
    base = Path(dest) if dest is not None else default_fixtures_dir()
    base.mkdir(parents=True, exist_ok=True)
    wanted = list(sources) if sources is not None else list(_SOURCES)
    unknown = [s for s in wanted if s not in _SOURCES]
    if unknown:
        raise ValueError(f"unknown sources {unknown}; valid: {sorted(_SOURCES)}")

    # a fresh snapshot owns the directory: drop stale CSVs + manifest. A
    # partial fetch (--sources ...) refreshes ONLY those sources' datasets and
    # carries every other source over from the existing manifest.
    carried: list[dict[str, Any]] = []
    carried_stats: dict[str, Any] = {}
    if sources is None:
        for p in base.glob("*.csv"):
            p.unlink()
        (base / MANIFEST_NAME).unlink(missing_ok=True)
    elif (base / MANIFEST_NAME).is_file():
        previous = load_manifest(base)
        for d in previous.get("datasets", []):
            if d.get("source") in wanted:
                (base / f"{d['slug']}.csv").unlink(missing_ok=True)
            else:
                carried.append(d)
        carried_stats = {
            s: st for s, st in previous.get("stats", {}).get("per_source", {}).items()
            if s not in wanted
        }

    log: Callable[[str], None] = print if verbose else (lambda _msg: None)
    corpus = _Corpus(base, row_cap, log)
    corpus.datasets.extend(carried)
    corpus.slugs.update(d["slug"] for d in carried)
    corpus.stats.update(carried_stats)
    budget = _Budget(api_budget)
    t0 = time.monotonic()
    if "openflights" in wanted:
        _fetch_openflights(corpus)
    if "datasets-org" in wanted:
        _fetch_datasets_org(corpus, budget)
    if "fivethirtyeight" in wanted:
        _fetch_fivethirtyeight(corpus, budget)
    if "vega" in wanted:
        _fetch_vega(corpus, budget)
    if "seaborn" in wanted:
        _fetch_seaborn(corpus)

    manifest = corpus.write_manifest(budget.used)
    manifest["stats"]["fetch_seconds"] = round(time.monotonic() - t0, 1)
    (base / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    log(
        f"wild corpus: {manifest['stats']['datasets_kept']} datasets, "
        f"{manifest['stats']['total_bytes'] / 1e6:.1f} MB, "
        f"{budget.used} GitHub API calls, {manifest['stats']['fetch_seconds']}s"
    )
    return manifest
