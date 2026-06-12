"""M14 fixtures: the shared estate world (tests/m11/world.py) + one snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "m11"))
from world import build_world  # noqa: E402


@pytest.fixture(scope="package")
def world(tmp_path_factory):
    w = build_world(tmp_path_factory.mktemp("m14_world"))
    yield w
    w["ledger"].close()


@pytest.fixture(scope="package")
def bundle(world, tmp_path_factory) -> Path:
    from ontoforge.amber import snapshot

    out = tmp_path_factory.mktemp("amber") / "bundle"
    snapshot(out, world["hearth"], world["ontology"], world["ledger"])
    return out
