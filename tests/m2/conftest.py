"""M2 conftest: shared fixtures (helpers live in m2_helpers.py)."""

from __future__ import annotations

import pytest

from ontoforge.contracts import DecisionKind, SpineProfile
from ontoforge.spine import DecisionSpine

from m2_helpers import ScriptedModelClient, gaussian_samples


@pytest.fixture
def economy_profile() -> SpineProfile:
    return SpineProfile(name="economy", budget_tokens=1_000_000, alpha=0.1)


@pytest.fixture
def crucible_profile() -> SpineProfile:
    return SpineProfile(name="crucible", budget_tokens=0, alpha=0.1)


@pytest.fixture
def scripted_client() -> ScriptedModelClient:
    return ScriptedModelClient()


@pytest.fixture
def calibrated_spine(economy_profile: SpineProfile) -> DecisionSpine:
    """An economy spine with a fitted ER calibrator (seed 1, 20k samples)."""
    spine = DecisionSpine(economy_profile)
    spine.recalibrate(DecisionKind.ER, gaussian_samples(DecisionKind.ER, seed=1, n=20_000))
    return spine
