"""Shared fixtures. Living at the repo root also puts the root on sys.path for pytest."""

from __future__ import annotations

from datetime import date

import pytest

from app import intake
from app.registry import Registry

#: A fixed "today" so ages, ordinals and the case year never drift with the clock.
TODAY = date(2026, 8, 21)

PILOT_MATTER = "dhs"
PILOT_VARIANT = "dhs_1p_1c"


@pytest.fixture(scope="session")
def registry() -> Registry:
    """The real config/ and templates/. If this raises, startup validation has
    found something wrong with the repository itself — which is the point of it."""
    reg = Registry.load()
    for key, value in intake.sample_settings(reg.schema).items():
        reg.settings[key] = value
    return reg


@pytest.fixture
def schema(registry: Registry):
    return registry.schema


@pytest.fixture
def pilot_groups(registry: Registry) -> list[str]:
    return registry.variant(PILOT_MATTER, PILOT_VARIANT).field_groups


@pytest.fixture
def today() -> date:
    return TODAY
