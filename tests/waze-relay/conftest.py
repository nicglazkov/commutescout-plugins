"""Shared by the relay's tests."""
from __future__ import annotations

import pytest
import server as relay
import store as store_module


@pytest.fixture(autouse=True)
def _no_fetching_inside_requests(monkeypatch):
    """A steering request fetches the tiles it made due before it answers
    (see Store.catch_up), and the stores in these tests have no network.
    So it is off unless a test turns it on, and unpaced when it does."""
    monkeypatch.setattr(relay, "CATCH_UP_S", 0.0)
    monkeypatch.setattr(store_module, "PACE_S", 0.0)
