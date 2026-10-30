"""Programmatic stronghold route engine.

The command-line interface, when run as a script, is only an adapter around
``AllPortalsEngine``. Importing this module never reads input or starts work.
"""

from pathlib import Path
from typing import Sequence

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from utils import find_angle, getRing, update_map


class AllPortalsEngine:
    # The eight ring totals add up to the 128 strongholds in the game.
    sh_per_ring = (3, 6, 10, 15, 21, 28, 36, 9)
    magnitude_per_ring = (2048, 5120, 8192, 11264, 14336, 17408, 20480, 23552)

    def __init__(self, output_dir: str | Path = ".", background_path: str | Path = "rings.png"):
        self.output_dir = Path(output_dir)
        self.background_path = Path(background_path)
        self.ring_strongholds: list[tuple[int, int]] = []
        self.estimated_strongholds: list[tuple[int, int]] = []
        self.tour_order: list[int] = []
        self.current_target_index: int | None = None
        self.completed_count = 0
        self.completed_indices: set[int] = set()
        self.path_history: list[tuple[tuple[float, float], tuple[float, float], str]] = []
        self.current_position: tuple[float, float] = (0, 0)
        self._ring_index = 0
        self._skipped_indices: set[int] = set()

    @property
    def solved(self) -> bool:
        return bool(self.tour_order)

    def add_ring_stronghold(self, x: float, z: float) -> dict:
        if self._ring_index >= 8:
            raise ValueError("All eight ring strongholds have already been recorded")
        coordinates = (int(x), int(z))
        self.ring_strongholds.append(coordinates)
        self._ring_index += 1
        self.completed_count = len(self.ring_strongholds)
        if self._ring_index == 8:
            self._predict_strongholds()
            self.solve_tsp()
        return self.get_current_target()

    def _predict_strongholds(self) -> None:
        self.estimated_strongholds = list(self.ring_strongholds)
        for ring, (x, z) in enumerate(self.ring_strongholds):
            angle = np.arctan2(z, x)
            for _ in range(self.sh_per_ring[ring] - 1):
                angle += 2 * np.pi / self.sh_per_ring[ring]
                self.estimated_strongholds.append((
                    round(self.magnitude_per_ring[ring] * np.cos(angle)),
                    round(self.magnitude_per_ring[ring] * np.sin(angle)),
                ))

    def solve_tsp(self) -> list[int]:
        if len(self.estimated_strongholds) != 128:
            raise ValueError("Eight ring strongholds are required before solving")
        size = len(self.estimated_strongholds)
        distances = [
            [round(np.hypot(a[0] - b[0], a[1] - b[1])) for b in self.estimated_strongholds]
            for a in self.estimated_strongholds
        ]
        manager = pywrapcp.RoutingIndexManager(size, 1, 0)
        routing = pywrapcp.RoutingModel(manager)
        callback = routing.RegisterTransitCallback(
            lambda from_index, to_index: distances[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
        )
        routing.SetArcCostEvaluatorOfAllVehicles(callback)
        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        params.time_limit.seconds = 10
        solution = routing.SolveWithParameters(params)
        if solution is None:
            raise RuntimeError("OR-Tools could not solve the stronghold tour")
        order = []
        index = routing.Start(0)
        while not routing.IsEnd(index):
            order.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        # The eight manually found strongholds are already completed.
        self.completed_indices = set(range(8))
        remaining = [i for i in order if i not in self.completed_indices]
        start = min(range(len(remaining)), key=lambda i: self._distance(self.ring_strongholds[-1], self.estimated_strongholds[remaining[i]]))
        self.tour_order = remaining[start:] + remaining[:start]
        self.current_target_index = self.tour_order[0] if self.tour_order else None
        self.current_position = self.ring_strongholds[-1]
        return self.tour_order

    def _distance(self, a, b):
        return float(np.hypot(a[0] - b[0], a[1] - b[1]))

    def _advance(self, completed: bool) -> dict:
        if self.current_target_index is None:
            return self.get_current_target()
        target = self.estimated_strongholds[self.current_target_index]
        if completed:
            self.completed_indices.add(self.current_target_index)
            self.completed_count += 1
            color = "green"
        else:
            self._skipped_indices.add(self.current_target_index)
            color = "gray"
        self.path_history.append((self.current_position, target, color))
        self.current_position = target
        next_index = len(self.completed_indices) + len(self._skipped_indices) - 8
        self.current_target_index = self.tour_order[next_index] if next_index < len(self.tour_order) else None
        return self.get_current_target()

    def next_stronghold(self) -> dict:
        return self._advance(True)

    def mark_empty_sector(self) -> dict:
        return self._advance(False)

    def reset_spawn(self, x: float = 0, z: float = 0) -> dict:
        self.current_position = (x, z)
        remaining = [i for i in self.tour_order if i not in self.completed_indices and i not in self._skipped_indices]
        if remaining:
            self.tour_order = sorted(remaining, key=lambda i: self._distance(self.current_position, self.estimated_strongholds[i]))
            self.current_target_index = self.tour_order[0]
        return self.get_current_target()

    def set_count(self, count: int) -> dict:
        if not 0 <= count <= 128:
            raise ValueError("count must be between 0 and 128")
        self.completed_count = int(count)
        return self.get_current_target()

    def get_current_target(self) -> dict:
        target = self.estimated_strongholds[self.current_target_index] if self.current_target_index is not None else None
        return {
            "target": target,
            "overworld": target,
            "nether": (round(target[0] / 8), round(target[1] / 8)) if target else None,
            "ring": getRing(target) if target else None,
            "remaining": max(0, 128 - self.completed_count),
            "completed": self.completed_count,
            "angle": find_angle(self.current_position, target) if target else None,
        }

    def update_map(self) -> dict:
        return update_map(self, self.output_dir)


def main() -> None:
    print("Use AllPortalsEngine from Python; the interactive CLI has been removed.")


if __name__ == "__main__":
    main()
