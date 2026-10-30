import math

import pytest

from AllPortals import MAX_PLAYERS, RING_OFFSETS, AllPortalsEngine, UnknownPlayer
from solver import route_length


def nn_length(start, points):
    remaining, here, total = set(range(len(points))), start, 0.0
    while remaining:
        nxt = min(remaining, key=lambda i: math.dist(here, points[i]))
        total += math.dist(here, points[nxt])
        here = points[nxt]
        remaining.remove(nxt)
    return total


def log_rings(engine, ring_coords, player=None):
    for x, z in ring_coords:
        engine.add_ring_stronghold(x, z, player)


@pytest.fixture
def solo(tmp_path, ring_coords):
    engine = AllPortalsEngine(output_dir=tmp_path)
    log_rings(engine, ring_coords)
    assert engine.solve()
    return engine


@pytest.fixture
def duo(tmp_path, ring_coords):
    engine = AllPortalsEngine(output_dir=tmp_path, players=("Syd", "Friend"))
    log_rings(engine, ring_coords)
    assert engine.solve()
    return engine


def route_points(engine, name):
    return [engine.estimated_strongholds[i] for i in engine.players[name].route]


# ---------------------------------------------------------------- rings
def test_slots_are_stable_whatever_order_rings_are_logged(tmp_path, ring_coords):
    forward = AllPortalsEngine(output_dir=tmp_path, players=("A",))
    backward = AllPortalsEngine(output_dir=tmp_path, players=("A",))
    log_rings(forward, ring_coords)
    log_rings(backward, list(reversed(ring_coords)))
    assert forward.estimated_strongholds == backward.estimated_strongholds
    assert all(sh is not None for sh in forward.estimated_strongholds)
    for ring, (x, z) in enumerate(ring_coords):
        assert forward.estimated_strongholds[RING_OFFSETS[ring]] == (x, z)


def test_ring_input_is_validated(tmp_path):
    engine = AllPortalsEngine(output_dir=tmp_path)
    with pytest.raises(ValueError):
        engine.add_ring_stronghold(3500, 0)  # between rings 1 and 2
    engine.add_ring_stronghold(2048, 0)
    with pytest.raises(ValueError):
        engine.add_ring_stronghold(0, 2048)  # ring 1 again
    assert engine.completed_count == 1


def test_logging_rings_works_before_anyone_joins(tmp_path):
    engine = AllPortalsEngine(output_dir=tmp_path, players=())
    result = engine.add_ring_stronghold(2048, 0)
    assert result["target"] is None and engine.completed_count == 1
    assert engine.mode == "Pre-TSP (Gathering Rings)"


# -------------------------------------------------------------- planning
def test_single_player_route_covers_everything_once(solo):
    route = solo.players["Player 1"].route
    assert sorted(route) == solo.unvisited_indices()
    assert len(route) == 120 and solo.mode == "Active (TSP Solved)"


def test_route_beats_nearest_neighbour(solo):
    pts = route_points(solo, "Player 1")
    assert route_length((0, 0), pts, range(len(pts))) < nn_length((0, 0), pts)


def test_route_is_planned_from_the_player_position(tmp_path, ring_coords):
    far = (20000, 20000)
    engine = AllPortalsEngine(output_dir=tmp_path)
    engine.reset_spawn(*far)
    log_rings(engine, ring_coords)
    engine.solve()
    pts = route_points(engine, "Player 1")
    assert route_length(far, pts, range(len(pts))) < nn_length(far, pts)


def test_reset_spawn_replans_instead_of_sorting_by_distance(solo):
    for _ in range(10):
        solo.next_stronghold()
    solo.reset_spawn(-18000, 18000)
    assert solo.plan_needed
    assert solo.solve()
    start, pts = (-18000, 18000), route_points(solo, "Player 1")
    by_distance = sorted(pts, key=lambda p: math.dist(start, p))
    assert sorted(solo.players["Player 1"].route) == solo.unvisited_indices()
    assert pts != by_distance
    assert route_length(start, pts, range(len(pts))) < nn_length(start, pts)


def test_two_players_split_the_work_evenly(duo):
    a, b = duo.players["Syd"].route, duo.players["Friend"].route
    assert not set(a) & set(b)
    assert sorted(a + b) == duo.unvisited_indices()
    length_a = route_length(duo.players["Syd"].position, route_points(duo, "Syd"), range(len(a)))
    length_b = route_length(duo.players["Friend"].position, route_points(duo, "Friend"), range(len(b)))
    assert abs(length_a - length_b) / max(length_a, length_b) < 0.2


def test_players_get_different_targets_before_a_plan_exists(tmp_path):
    engine = AllPortalsEngine(output_dir=tmp_path, players=("Syd", "Friend"))
    engine.add_ring_stronghold(2048, 0)
    targets = engine.current_targets()
    assert targets["Syd"] is not None and targets["Friend"] is not None
    assert targets["Syd"] != targets["Friend"]


def test_targets_never_collide_once_planned(duo):
    for _ in range(15):
        targets = duo.current_targets()
        assert targets["Syd"] != targets["Friend"]
        duo.next_stronghold("Syd")
        duo.next_stronghold("Friend")


def test_stale_plans_are_rejected(tmp_path, ring_coords):
    engine = AllPortalsEngine(output_dir=tmp_path, players=("Syd", "Friend"))
    log_rings(engine, ring_coords)
    request = engine.plan_request()
    engine.reset_spawn(100, 100, "Syd")  # inputs changed while "solving"
    assert not engine.apply_plan(request, [[], []])
    assert engine.plan_needed


def test_plan_ignores_strongholds_completed_while_solving(tmp_path, ring_coords):
    from solver import solve_routes
    engine = AllPortalsEngine(output_dir=tmp_path, players=("Syd", "Friend"))
    log_rings(engine, ring_coords)
    request = engine.plan_request()
    routes = solve_routes(request.starts, request.points, 0.3)
    taken = request.indices[routes[0][0]]
    engine.next_stronghold("Syd", taken)  # progress made during the solve
    assert engine.apply_plan(request, routes)
    assert all(taken not in p.route for p in engine.players.values())
    assert not engine.plan_needed


def test_idle_player_triggers_a_replan(duo):
    for index in list(duo.players["Friend"].route):
        duo.next_stronghold("Friend", index)
    assert duo.plan_needed  # Friend ran out while Syd still has stops
    assert duo.solve()
    assert duo.route_remaining("Friend") > 0


# ---------------------------------------------------------------- actions
def test_complete_is_idempotent(duo):
    index = duo.current_targets()["Syd"]
    first = duo.next_stronghold("Syd", index)
    again = duo.next_stronghold("Friend", index)
    assert first["changed"] and not again["changed"]
    assert duo.completed_count == 9
    assert len([e for e in duo.log if e["action"] == "complete"]) == 1


def test_skip_then_find(duo):
    index = duo.current_targets()["Syd"]
    duo.mark_empty_sector("Syd", index)
    assert index in duo.skipped and duo.completed_count == 8
    assert not duo.mark_empty_sector("Syd", index)["changed"]
    assert duo.next_stronghold("Syd", index)["changed"]  # it turned out to hold one
    assert index in duo.completed and index not in duo.skipped
    assert not duo.mark_empty_sector("Syd", index)["changed"]
    assert duo.completed_count == 9


def test_completing_a_teammates_stop_removes_it_from_their_route(duo):
    stop = duo.players["Friend"].route[3]
    duo.next_stronghold("Syd", stop)
    assert stop not in duo.players["Friend"].route
    assert duo.players["Syd"].position == duo.estimated_strongholds[stop]


def test_unknown_stronghold_and_player(duo):
    with pytest.raises(ValueError):
        duo.next_stronghold("Syd", 500)
    with pytest.raises(UnknownPlayer):
        duo.next_stronghold("Nobody")


def test_set_count_adjusts_future_counts(solo):
    assert solo.set_count(20)["completed"] == 20
    solo.next_stronghold()
    assert solo.completed_count == 21
    for bad in (-1, 129):
        with pytest.raises(ValueError):
            solo.set_count(bad)


def test_finishing_every_stronghold(solo):
    while solo.get_current_target()["index"] is not None:
        solo.next_stronghold()
    assert solo.completed_count == 128
    assert solo.get_current_target()["remaining"] == 0


# ------------------------------------------------------------------ players
def test_player_management(tmp_path):
    engine = AllPortalsEngine(output_dir=tmp_path, players=())
    engine.add_player("Syd")
    engine.add_player("syd")  # same person, different case: no duplicate
    assert list(engine.players) == ["Syd"]
    for name in ("B", "C", "D"):
        engine.add_player(name)
    assert len(engine.players) == MAX_PLAYERS
    assert len({p.color for p in engine.players.values()}) == MAX_PLAYERS
    with pytest.raises(ValueError):
        engine.add_player("E")
    for bad in ("", "<script>", "x" * 21):
        with pytest.raises(ValueError):
            engine.add_player(bad)
    engine.remove_player("B")
    assert "B" not in engine.players
    for name in ("C", "D"):
        engine.remove_player(name)
    with pytest.raises(ValueError):
        engine.remove_player("Syd")  # last player stays


def test_joining_after_the_plan_exists_replans(solo):
    solo.add_player("Friend")
    assert solo.plan_needed and solo.solve()
    assert solo.route_remaining("Friend") > 0 and solo.route_remaining("Player 1") > 0


# ------------------------------------------------------------- undo / save
def test_undo_restores_everything(duo):
    before = duo.to_state()
    index = duo.current_targets()["Syd"]
    duo.next_stronghold("Syd", index)
    duo.undo("Friend")
    after = duo.to_state()
    for key in ("completed", "skipped", "players", "count_offset"):
        assert after[key] == before[key]
    assert duo.log[-1]["action"] == "undo" and "Syd" in duo.log[-1]["detail"]


def test_undo_can_step_back_through_several_actions(solo):
    positions = [solo.players["Player 1"].position]
    for _ in range(3):
        solo.next_stronghold()
        positions.append(solo.players["Player 1"].position)
    for expected in reversed(positions[:-1]):
        solo.undo()
        assert solo.players["Player 1"].position == expected
    assert solo.completed_count == 8


def test_undo_with_nothing_to_undo(tmp_path):
    with pytest.raises(ValueError):
        AllPortalsEngine(output_dir=tmp_path, players=()).undo()


def test_undo_discards_a_solve_in_flight(tmp_path, ring_coords):
    engine = AllPortalsEngine(output_dir=tmp_path, players=("Syd",))
    for x, z in ring_coords[:7]:
        engine.add_ring_stronghold(x, z)
    engine.add_ring_stronghold(*ring_coords[7])
    request = engine.plan_request()
    engine.undo()  # take the last ring back
    assert not engine.apply_plan(request, [[0]])


def test_undo_history_is_capped(solo):
    solo.undo_limit = 5
    for _ in range(10):
        solo.next_stronghold()
    undone = 0
    while solo.can_undo:
        solo.undo()
        undone += 1
    assert undone == 5


def test_save_and_load_round_trip(duo, tmp_path):
    duo.next_stronghold("Syd")
    duo.mark_empty_sector("Friend")
    duo.save(tmp_path / "state.json")

    restored = AllPortalsEngine(output_dir=tmp_path, players=())
    restored.load(tmp_path / "state.json")
    assert restored.estimated_strongholds == duo.estimated_strongholds
    assert restored.completed == duo.completed and restored.skipped == duo.skipped
    assert restored.current_targets() == duo.current_targets()
    assert restored.players["Syd"].path_history == duo.players["Syd"].path_history
    assert restored.log == duo.log
    restored.undo()  # undo history survives a restart: this reverts Friend's skip
    assert restored.skipped == set()
    assert restored.completed == duo.completed


def test_load_rejects_unknown_formats(tmp_path):
    engine = AllPortalsEngine(output_dir=tmp_path)
    with pytest.raises(ValueError):
        engine.load_state({"format": 99})


def test_loaded_run_without_a_plan_asks_for_one(duo, tmp_path):
    state = duo.to_state()
    state["plan_valid"], state["plan_dirty"] = False, False
    restored = AllPortalsEngine(output_dir=tmp_path, players=())
    restored.load_state(state)
    assert restored.plan_needed


# ------------------------------------------------------------------ exports
def test_update_map_writes_exports_atomically(duo, tmp_path):
    paths = duo.update_map()
    assert set(paths) == {"output", "count", "facts"}
    assert (tmp_path / "output.png").stat().st_size > 1000
    assert (tmp_path / "sh_count.txt").read_text().strip() == "8/128"
    assert not list(tmp_path.glob("*.tmp"))
