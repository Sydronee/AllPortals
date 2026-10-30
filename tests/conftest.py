import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import solver  # noqa: E402

MAGNITUDES = (2048, 5120, 8192, 11264, 14336, 17408, 20480, 23552)


@pytest.fixture(autouse=True)
def fast_solver(monkeypatch):
    """Keep tests quick: cap the search time well below the real 5 s."""
    monkeypatch.setattr(solver, "plan_time_limit", lambda count: 0.5)


@pytest.fixture
def ring_coords():
    """One realistic stronghold per ring, at varied angles."""
    return [
        (round(mag * math.cos(0.7 * i + 0.3)), round(mag * math.sin(0.7 * i + 0.3)))
        for i, mag in enumerate(MAGNITUDES)
    ]
