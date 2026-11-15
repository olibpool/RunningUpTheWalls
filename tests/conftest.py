from __future__ import annotations

import os

import pytest
from hypothesis import settings

# Pick with HYPOTHESIS_PROFILE=ci|pi; tests without their own max_examples use these counts.
settings.register_profile("dev", max_examples=25, deadline=None)
settings.register_profile("ci", max_examples=200, deadline=None)
settings.register_profile("pi", max_examples=50, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="Rewrite golden plan snapshots in tests/fixtures/",
    )
