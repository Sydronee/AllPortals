import math

import pytest

from AllPortals import AllPortalsEngine
from solver import route_length


def nearest_neighbour_length(start, points):
    remaining, here, total = set(range(len(points))), start, 0.0
    while remaining:
        nxt = min(remaining, key=lambda i: math.dist(here, points[i]))
        total += math.dist(here, points[nxt])
        here = points[nxt]
        remaining.remove(nxt)
    return total


@pytest.fixture
def engine(tmp_path, ring_coords):
    engine = AllPortalsEngine(output_dir=tmp_path)
    for x, z in ring_coords:
        engine.add_ring_stronghold(x, z)
    return engine


def unvisited_points(engine):
    done = engine.completed_indices | engine._skipped_indices
    indices = [i for i in range(128) if i not in done]
    return indices, [engine.estimated_strongholds[i] for i in indices]


def test_solves_after_eighth_ring(engine):
    assert engine.solved
    assert len(engine.estimated_strongholds) == 128
    assert engine.completed_count == 8
    assert len(engine.tour_order) == 120
    assert len(set(engine.tour_order)) == 120


def test_ring_input_is_validated(tmp_path):
    engine = AllPortalsEngine(output_dir=tmp_path)
    with pytest.raises(ValueError):
        engine.add_ring_stronghold(3500, 0)  # between rings 1 and 2
    engine.add_ring_stronghold(2048, 0)
    with pytest.raises(ValueError):
        engine.add_ring_stronghold(0, 2048)  # ring 1 again


def test_route_beats_nearest_neighbour(engine):
    indices, points = unvisited_points(engine)
    order = [indices.index(i) for i in engine.tour_order]
    assert route_length(engine.current_position, points, order) < nearest_neighbour_length(
        engine.current_position, points
    )


def test_route_is_planned_from_the_player_position(tmp_path, ring_coords):
    """The first target should be near wherever the player actually is."""
    far = (20000, 20000)
    engine = AllPortalsEngine(output_dir=tmp_path)
    engine.reset_spawn(*far)
    for x, z in ring_coords:
        engine.add_ring_stronghold(x, z)
    first = engine.estimated_strongholds[engine.tour_order[0]]
    nearest = min(
        (p for i, p in enumerate(engine.estimated_strongholds) if i not in engine.completed_indices),
        key=lambda p: math.dist(far, p),
    )
    assert math.dist(far, first) <= math.dist(far, nearest) * 1.5


def test_reset_spawn_replans_instead_of_sorting_by_distance(engine):
    for _ in range(10):
        engine.next_stronghold()
    engine.reset_spawn(-18000, 18000)
    indices, points = unvisited_points(engine)
    order = [indices.index(i) for i in engine.tour_order]

    assert sorted(engine.tour_order) == indices  # every unvisited stronghold exactly once
    assert engine.current_position == (-18000, 18000)
    by_distance = sorted(range(len(points)), key=lambda i: math.dist((-18000, 18000), points[i]))
    assert order != by_distance  # the old bug: a distance sort, not a route
    start = (-18000, 18000)
    assert route_length(start, points, order) < route_length(start, points, by_distance)
    assert route_length(start, points, order) < nearest_neighbour_length(start, points)


def test_reset_spawn_before_solving_still_works(tmp_path):
    engine = AllPortalsEngine(output_dir=tmp_path)
    engine.add_ring_stronghold(2048, 0)
    result = engine.reset_spawn(2000, 100)
    assert not engine.solved
    assert result["target"] is not None


def test_advance_and_skip(engine):
    first = engine.get_current_target()["target"]
    engine.next_stronghold()
    assert engine.completed_count == 9
    second = engine.get_current_target()["target"]
    assert second != first
    engine.mark_empty_sector()
    assert engine.completed_count == 9  # skipping does not count


def test_set_count_bounds(engine):
    assert engine.set_count(10)["completed"] == 10
    for bad in (-1, 129):
        with pytest.raises(ValueError):
            engine.set_count(bad)


def test_update_map_writes_exports_atomically(engine, tmp_path):
    paths = engine.update_map()
    for key in ("output", "count", "facts"):
        assert (tmp_path / paths[key].split("/")[-1]).exists()
    assert (tmp_path / "sh_count.txt").read_text().strip() == "8/128"
    assert not list(tmp_path.glob("*.tmp"))
