"""Route planning for the 128 strongholds.

Pure functions only: nothing here touches engine state, so a solve can run on a
background thread while the engine keeps answering requests.
"""
from __future__ import annotations

import math
from typing import Sequence

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

Point = tuple[float, float]


def plan_time_limit(target_count: int) -> float:
    """Seconds to spend searching. Guided local search always uses its full
    budget, so small problems get a small one."""
    return min(5.0, max(0.3, 0.04 * target_count))


def solve_routes(
    starts: Sequence[Point],
    targets: Sequence[Point],
    time_limit_s: float | None = None,
    balance: int = 100,
) -> list[list[int]]:
    """Plan one open route per start position covering every target once.

    Routes are open paths: they begin at the player's position and end wherever
    the last stronghold is (no return trip is costed). With several starts the
    longest route is minimised first, so the players finish together.

    Returns, per start, the indices into ``targets`` in visiting order.
    """
    start_count, count = len(starts), len(targets)
    if start_count == 0:
        raise ValueError("at least one start position is required")
    if count == 0:
        return [[] for _ in starts]

    # Nodes: 0..count-1 targets, then one node per start, then a shared dummy
    # end node that every route finishes on at zero cost.
    end_node = count + start_count
    points = list(targets) + list(starts)

    def cost(a: int, b: int) -> int:
        if b == end_node or a == end_node:
            return 0
        return round(math.hypot(points[a][0] - points[b][0], points[a][1] - points[b][1]))

    size = end_node + 1
    matrix = [[cost(a, b) for b in range(size)] for a in range(size)]

    manager = pywrapcp.RoutingIndexManager(
        size, start_count, list(range(count, count + start_count)), [end_node] * start_count
    )
    routing = pywrapcp.RoutingModel(manager)
    transit = routing.RegisterTransitCallback(
        lambda i, j: matrix[manager.IndexToNode(i)][manager.IndexToNode(j)]
    )
    routing.SetArcCostEvaluatorOfAllVehicles(transit)
    if start_count > 1:
        routing.AddDimension(transit, 0, 10**9, True, "Distance")
        routing.GetDimensionOrDie("Distance").SetGlobalSpanCostCoefficient(balance)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    limit = plan_time_limit(count) if time_limit_s is None else time_limit_s
    params.time_limit.FromMilliseconds(int(limit * 1000))

    solution = routing.SolveWithParameters(params)
    if solution is None:
        raise RuntimeError("OR-Tools could not solve the stronghold tour")

    routes: list[list[int]] = []
    for vehicle in range(start_count):
        route, index = [], routing.Start(vehicle)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node < count:
                route.append(node)
            index = solution.Value(routing.NextVar(index))
        routes.append(route)
    return routes


def route_length(start: Point, targets: Sequence[Point], route: Sequence[int]) -> float:
    """Length of an open path from ``start`` through ``route``."""
    total, here = 0.0, start
    for index in route:
        total += math.hypot(here[0] - targets[index][0], here[1] - targets[index][1])
        here = targets[index]
    return total
