"""Programmatic stronghold route engine.

The command-line interface, when run as a script, is only an adapter around
``AllPortalsEngine``. Importing this module never reads input or starts work.
"""

import math
from pathlib import Path

from solver import solve_routes
from utils import find_angle, get_dist, get_ring, update_map


class AllPortalsEngine:
    # The eight ring totals add up to the 128 strongholds in the game.
    sh_per_ring = (3, 6, 10, 15, 21, 28, 36, 9)
    magnitude_per_ring = (2048, 5120, 8192, 11264, 14336, 17408, 20480, 23552)

    def __init__(self, output_dir: str | Path = ".", background_path: str | Path = "rings.png"):
        self.output_dir = Path(output_dir)
        self.background_path = Path(background_path)
        self.ring_strongholds: list[tuple[int, int] | None] = [None] * 8
        self.estimated_strongholds: list[tuple[int, int]] = []
        self.estimated_rings: list[int] = []
        self.tour_order: list[int] = []
        self.current_target_index: int | None = None
        self.completed_count = 0
        self.completed_indices: set[int] = set()
        self.completed_coordinates: set[tuple[int, int]] = set()
        self.skipped_coordinates: set[tuple[int, int]] = set()
        self.path_history: list[tuple[tuple[float, float], tuple[float, float], str]] = []
        self.current_position: tuple[float, float] = (0, 0)
        self._skipped_indices: set[int] = set()
        self._tsp_solved = False

    @property
    def solved(self) -> bool:
        return self._tsp_solved

    @property
    def mode(self) -> str:
        return "Active (TSP Solved)" if self.solved else "Pre-TSP (Gathering Rings)"

    def add_ring_stronghold(self, x: float, z: float) -> dict:
        coordinates = (int(x), int(z))
        ring = get_ring(coordinates)
        if ring == 0:
            raise ValueError("Coordinates do not fall within one of the eight ring layers")
        ring_index = ring - 1
        if self.ring_strongholds[ring_index] is not None:
            raise ValueError(f"Ring {ring} has already been recorded")
        self.ring_strongholds[ring_index] = coordinates
        self.completed_count += 1
        self.completed_coordinates.add(coordinates)
        self._predict_ring(ring_index, coordinates)
        if sum(stronghold is not None for stronghold in self.ring_strongholds) == 8:
            self._predict_strongholds()
            self.solve_tsp()
        else:
            self._select_pre_tsp_target()
        return self.get_current_target()

    def _predict_ring(self, ring: int, stronghold: tuple[int, int]) -> None:
        """Replace one ring's estimates without requiring the other rings."""
        self.estimated_strongholds = [
            sh for sh, existing_ring in zip(self.estimated_strongholds, self.estimated_rings)
            if existing_ring != ring + 1
        ]
        self.estimated_rings = [
            existing_ring for existing_ring in self.estimated_rings if existing_ring != ring + 1
        ]
        x, z = stronghold
        angle = math.atan2(z, x)
        estimates = [stronghold]
        for _ in range(self.sh_per_ring[ring] - 1):
            angle += 2 * math.pi / self.sh_per_ring[ring]
            estimates.append((
                round(self.magnitude_per_ring[ring] * math.cos(angle)),
                round(self.magnitude_per_ring[ring] * math.sin(angle)),
            ))
        self.estimated_strongholds.extend(estimates)
        self.estimated_rings.extend([ring + 1] * len(estimates))

    def _predict_strongholds(self) -> None:
        if any(stronghold is None for stronghold in self.ring_strongholds):
            raise ValueError("Eight ring strongholds are required before prediction")
        self.estimated_strongholds = []
        self.estimated_rings = []
        for ring, stronghold in enumerate(self.ring_strongholds):
            self._predict_ring(ring, stronghold)

    def solve_tsp(self) -> list[int]:
        """Plan an open route over every unvisited stronghold, starting from
        the current position (no return trip is costed)."""
        if len(self.estimated_strongholds) != 128:
            raise ValueError("Eight ring strongholds are required before solving")

        # The eight manually found strongholds are already completed.
        self.completed_indices = {
            index for index, stronghold in enumerate(self.estimated_strongholds)
            if stronghold in self.completed_coordinates
        }
        self._skipped_indices = {
            index for index, stronghold in enumerate(self.estimated_strongholds)
            if stronghold in self.skipped_coordinates
        }
        remaining = [
            i for i in range(len(self.estimated_strongholds))
            if i not in self.completed_indices and i not in self._skipped_indices
        ]
        routes = solve_routes(
            [self.current_position], [self.estimated_strongholds[i] for i in remaining]
        )
        self.tour_order = [remaining[k] for k in routes[0]]
        self._tsp_solved = True
        self.current_target_index = self.tour_order[0] if self.tour_order else None
        return self.tour_order

    def _distance(self, a, b):
        return get_dist(a, b)

    def _advance(self, completed: bool) -> dict:
        if self.current_target_index is None:
            return self.get_current_target()
        target = self.estimated_strongholds[self.current_target_index]
        if completed:
            self.completed_indices.add(self.current_target_index)
            self.completed_coordinates.add(target)
            self.completed_count += 1
            color = "green"
        else:
            self._skipped_indices.add(self.current_target_index)
            self.skipped_coordinates.add(target)
            color = "gray"
        self.path_history.append((self.current_position, target, color))
        self.current_position = target
        if self.solved:
            self._select_tsp_target()
        else:
            self._select_pre_tsp_target()
        return self.get_current_target()

    def _select_pre_tsp_target(self) -> None:
        candidates = [
            index for index, stronghold in enumerate(self.estimated_strongholds)
            if stronghold not in self.completed_coordinates
            and stronghold not in self.skipped_coordinates
        ]
        self.current_target_index = (
            min(candidates, key=lambda index: self._distance(self.current_position, self.estimated_strongholds[index]))
            if candidates else None
        )

    def _select_tsp_target(self) -> None:
        self.current_target_index = next(
            (
                index for index in self.tour_order
                if index not in self.completed_indices and index not in self._skipped_indices
            ),
            None,
        )

    def next_stronghold(self) -> dict:
        return self._advance(True)

    def mark_empty_sector(self) -> dict:
        return self._advance(False)

    def reset_spawn(self, x: float = 0, z: float = 0) -> dict:
        """Move to a new position and re-plan the remaining route from there."""
        self.current_position = (x, z)
        if self.solved:
            self.solve_tsp()
        else:
            self._select_pre_tsp_target()
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
            "ring": get_ring(target) if target else None,
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
