import math
import random

import pytest

from utils import find_angle, get_dist, get_ring, is_prime


@pytest.mark.parametrize(
    "coords,ring",
    [((1280, 0), 1), ((2816, 0), 1), ((1279, 0), 0), ((2817, 0), 0), ((0, 5000), 2),
     ((23552, 0), 8), ((24320, 0), 8), ((24321, 0), 0), ((0, 0), 0)],
)
def test_get_ring_includes_both_boundaries(coords, ring):
    assert get_ring(coords) == ring


def old_find_angle(pos, nexsh):
    """The original quadrant-by-quadrant implementation, kept as a reference."""
    opposite = int(pos[0]) - int(nexsh[0])
    adjacent = int(pos[1]) - int(nexsh[1])
    if adjacent == 0:
        return 90.0 if opposite >= 0 else -90.0
    reference = abs(math.degrees(math.atan(opposite / adjacent)))
    if opposite >= 0 and adjacent >= 0:
        angle = 180 - reference
    elif opposite < 0 and adjacent < 0:
        angle = -reference
    elif opposite < 0 and adjacent >= 0:
        angle = reference - 180
    else:
        angle = reference
    return round(angle, 2)


@pytest.mark.parametrize(
    "target,expected",
    [((0, 100), 0), ((-100, 0), 90), ((0, -100), 180), ((100, 0), -90), ((100, 100), -45)],
)
def test_find_angle_matches_minecraft_yaw(target, expected):
    assert find_angle((0, 0), target) == pytest.approx(expected)


def test_find_angle_matches_original_formula():
    rng = random.Random(7)
    for _ in range(500):
        pos = (rng.randint(-20000, 20000), rng.randint(-20000, 20000))
        target = (rng.randint(-20000, 20000), rng.randint(-20000, 20000))
        new, old = find_angle(pos, target), old_find_angle(pos, target)
        assert new == pytest.approx(old, abs=0.011) or abs(abs(new) - 180) < 0.011


def test_get_dist_and_is_prime():
    assert get_dist((0, 0), (3, 4)) == 5
    assert [n for n in range(20) if is_prime(n)] == [2, 3, 5, 7, 11, 13, 17, 19]
