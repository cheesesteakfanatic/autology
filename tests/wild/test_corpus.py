"""Every committed wild CSV is pipeline-consumable; the OpenFlights cluster
keeps its documented headers and its cross-references (the closure-truncation
guarantee documented in docs/WILD_CORPUS.md)."""

from __future__ import annotations

from ontoforge.estates.wild import MAX_COLS, MIN_COLS, MIN_ROWS, OPENFLIGHTS_HEADERS, ROW_CAP

from conftest import load_csv


def test_every_csv_parses_within_the_gates(datasets, fixtures_dir):
    for d in datasets:
        df = load_csv(fixtures_dir / f"{d['slug']}.csv")
        assert len(df) >= MIN_ROWS, f"{d['slug']}: {len(df)} rows"
        assert len(df) <= ROW_CAP, f"{d['slug']}: over the row cap"
        assert MIN_COLS <= df.shape[1] <= MAX_COLS, f"{d['slug']}: {df.shape[1]} cols"
        assert len(df) == d["rows_kept"] and df.shape[1] == d["cols"], f"{d['slug']}: manifest drift"


def test_openflights_documented_headers_present(fixtures_dir):
    for name, headers in OPENFLIGHTS_HEADERS.items():
        df = load_csv(fixtures_dir / f"of_{name}.csv")
        assert list(df.columns) == headers, f"of_{name}: headers diverge from openflights.org/data.php"


def test_openflights_cluster_is_genuinely_joinable(fixtures_dir):
    """The snapshot's routes resolve inside the snapshot's airports/airlines —
    the whole point of keeping a REAL joinable cluster in the corpus."""
    routes = load_csv(fixtures_dir / "of_routes.csv")
    airports = set(load_csv(fixtures_dir / "of_airports.csv")["Airport ID"].str.strip())
    airlines = set(load_csv(fixtures_dir / "of_airlines.csv")["Airline ID"].str.strip())

    def refs(*cols):
        out = set()
        for c in cols:
            out |= {v for v in routes[c].str.strip() if v and v != r"\N"}
        return out

    assert refs("Source airport ID", "Destination airport ID") <= airports
    assert refs("Airline ID") <= airlines
