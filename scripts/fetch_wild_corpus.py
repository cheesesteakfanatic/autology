"""Fetch the WILD corpus: hundreds of real datasets from the public internet.

Thin wrapper over :func:`ontoforge.estates.wild.fetch` (NETWORK — run once to
build/refresh the committed snapshot; everything downstream is offline):

    uv run python scripts/fetch_wild_corpus.py                 # full corpus
    uv run python scripts/fetch_wild_corpus.py --dest /tmp/w   # elsewhere
    uv run python scripts/fetch_wild_corpus.py --sources openflights seaborn

Sources, licenses and the attribution table live in docs/WILD_CORPUS.md.
"""

from __future__ import annotations

import argparse
import sys

from ontoforge.estates import wild


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", default=None, help="output dir (default: fixtures/wild)")
    parser.add_argument(
        "--sources", nargs="+", default=None, choices=sorted(wild._SOURCES),
        help="fetch only these sources (default: all; partial fetches do not wipe the dir)",
    )
    parser.add_argument("--api-budget", type=int, default=15, help="max GitHub API calls")
    parser.add_argument("--row-cap", type=int, default=wild.ROW_CAP, help="rows kept per dataset")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    manifest = wild.fetch(
        args.dest,
        sources=args.sources,
        api_budget=args.api_budget,
        row_cap=args.row_cap,
        verbose=not args.quiet,
    )
    kept = manifest["stats"]["datasets_kept"]
    mb = manifest["stats"]["total_bytes"] / 1e6
    print(f"kept {kept} datasets ({mb:.1f} MB) -> {args.dest or wild.default_fixtures_dir()}")
    if kept < 150:
        print("GATE FAILED: fewer than 150 datasets landed", file=sys.stderr)
        return 1
    if manifest["stats"]["total_bytes"] > 20 * 1024 * 1024:
        print("GATE FAILED: corpus exceeds 20 MB", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
