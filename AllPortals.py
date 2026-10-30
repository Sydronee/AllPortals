"""Stronghold route engine for all-portals runs, for one or more players.

Importing this module never reads input or starts work. Applications drive it
through ``AllPortalsEngine``; ``app.py`` wraps it in a web service.

Strongholds live in 128 fixed slots (ring 1 first), so an index always means the
same stronghold no matter in which order the rings were logged.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from solver import solve_routes
from utils import (
    find_angle,
    get_dist,
    get_ring,
    update_map as render_map,
    write_text_atomic,
)

# The eight ring totals add up to the 128 strongholds in the game.
SH_PER_RING = (3, 6, 10, 15, 21, 28, 36, 9)
MAGNITUDE_PER_RING = (2048, 5120, 8192, 11264, 14336, 17408, 20480, 23552)
RING_OFFSETS = tuple(sum(SH_PER_RING[:i]) for i in range(8))
TOTAL_STRONGHOLDS = sum(SH_PER_RING)

MAX_PLAYERS = 4
PLAYER_COLORS = ("#2563eb", "#f97316", "#a855f7", "#14b8a6")
DEFAULT_PLAYER = "Player 1"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9 _.-]{1,20}$")
STATE_FORMAT = 1

Point = tuple[float, float]


class UnknownPlayer(ValueError):
    """Raised when an action names a player who has not joined."""


def ring_of(index: int) -> int:
    """Ring (1-8) a stronghold slot belongs to."""
    return max(r for r in range(8) if RING_OFFSETS[r] <= index) + 1


@dataclass
class Player:
    name: str
    color: str
    position: Point = (0, 0)
    route: list[int] = field(default_factory=list)  # planned stops, in order
    path_history: list[tuple[Point, Point, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "color": self.color,
            "position": list(self.position),
            "route": list(self.route),
            "path_history": [[list(a), list(b), kind] for a, b, kind in self.path_history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Player":
        return cls(
            name=data["name"],
            color=data["color"],
            position=tuple(data["position"]),
            route=[int(i) for i in data["route"]],
            path_history=[(tuple(a), tuple(b), kind) for a, b, kind in data["path_history"]],
        )


@dataclass(frozen=True)
class PlanRequest:
    """Everything a background solve needs; carries no references to the engine."""

    epoch: int
    players: tuple[str, ...]
    starts: tuple[Point, ...]
    indices: tuple[int, ...]  # stronghold slots still to visit
    points: tuple[Point, ...]


class AllPortalsEngine:
    sh_per_ring = SH_PER_RING
    magnitude_per_ring = MAGNITUDE_PER_RING
    undo_limit = 50
    log_limit = 200

    def __init__(
        self,
        output_dir: str | Path = ".",
        background_path: str | Path = "rings.png",
        players: tuple[str, ...] = (DEFAULT_PLAYER,),
    ):
        self.output_dir = Path(output_dir)
        self.background_path = Path(background_path)
        self.ring_strongholds: list[tuple[int, int] | None] = [None] * 8
        self.estimated_strongholds: list[Point | None] = [None] * TOTAL_STRONGHOLDS
        self.completed: set[int] = set()
        self.skipped: set[int] = set()
        self.count_offset = 0  # manual correction applied by set_count
        self.players: dict[str, Player] = {}
        self.plan_valid = False  # routes come from a real solve
        self.plan_dirty = False  # routes need recomputing
        self.solver_error: str | None = None
        self.version = 0  # bumps on every visible change
        self.epoch = 0  # bumps when an in-flight solve would be out of date
        self.log: list[dict] = []
        self._undo: list[dict] = []
        for name in players:
            self._insert_player(name)

    # ------------------------------------------------------------------ state
    @property
    def completed_count(self) -> int:
        return len(self.completed) + self.count_offset

    @property
    def rings_known(self) -> int:
        return sum(sh is not None for sh in self.ring_strongholds)

    @property
    def all_rings_known(self) -> bool:
        return self.rings_known == 8

    @property
    def solved(self) -> bool:
        return self.plan_valid

    @property
    def plan_needed(self) -> bool:
        return self.all_rings_known and self.plan_dirty and bool(self.players)

    @property
    def mode(self) -> str:
        if not self.all_rings_known:
            return "Pre-TSP (Gathering Rings)"
        if not self.players:
            return "Waiting for players"
        if self.plan_needed:
            return "Solving route"
        if self.plan_valid:
            return "Active (TSP Solved)"
        return "Active (nearest-stronghold fallback)"

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    def _touch(self) -> None:
        self.version += 1

    def _mark_dirty(self) -> None:
        self.plan_dirty = True
        self.epoch += 1

    def _done(self) -> set[int]:
        return self.completed | self.skipped

    def unvisited_indices(self) -> list[int]:
        done = self._done()
        return [i for i, sh in enumerate(self.estimated_strongholds) if sh is not None and i not in done]

    # ---------------------------------------------------------------- players
    def _insert_player(self, name: str) -> Player:
        name = name.strip()
        if not NAME_PATTERN.match(name):
            raise ValueError("Player names are 1-20 letters, digits, spaces, dots, dashes or underscores")
        used = {p.color for p in self.players.values()}
        color = next((c for c in PLAYER_COLORS if c not in used), PLAYER_COLORS[0])
        player = Player(name=name, color=color)
        self.players[name] = player
        return player

    def _player(self, name: str | None) -> Player:
        if not self.players:
            raise ValueError("No players have joined yet")
        if name is None:
            return next(iter(self.players.values()))
        if name in self.players:
            return self.players[name]
        for existing in self.players.values():
            if existing.name.casefold() == name.strip().casefold():
                return existing
        raise UnknownPlayer(f"Unknown player '{name}'")

    def add_player(self, name: str) -> dict:
        """Join (idempotent: joining under an existing name just returns it)."""
        clean = name.strip()
        if any(p.name.casefold() == clean.casefold() for p in self.players.values()):
            return self.get_current_target(clean)
        if len(self.players) >= MAX_PLAYERS:
            raise ValueError(f"At most {MAX_PLAYERS} players can join")
        if not NAME_PATTERN.match(clean):
            raise ValueError("Player names are 1-20 letters, digits, spaces, dots, dashes or underscores")
        self._checkpoint("join", clean, "joined the run")
        self._insert_player(clean)
        if self.all_rings_known:
            self._mark_dirty()
        self._touch()
        return self.get_current_target(clean)

    def remove_player(self, name: str) -> None:
        player = self._player(name)
        if len(self.players) == 1:
            raise ValueError("The last player cannot be removed")
        self._checkpoint("leave", player.name, "left the run")
        del self.players[player.name]
        if self.all_rings_known:
            self._mark_dirty()
        self._touch()

    # ------------------------------------------------------------ ring input
    def add_ring_stronghold(self, x: float, z: float, player: str | None = None) -> dict:
        coordinates = (int(x), int(z))
        ring = get_ring(coordinates)
        if ring == 0:
            raise ValueError("Coordinates do not fall within one of the eight ring layers")
        ring_index = ring - 1
        if self.ring_strongholds[ring_index] is not None:
            raise ValueError(f"Ring {ring} has already been recorded")
        actor = self._player(player).name if player and self.players else ""
        self._checkpoint("ring", actor, f"ring {ring} stronghold at ({coordinates[0]}, {coordinates[1]})")
        self.ring_strongholds[ring_index] = coordinates
        self._predict_ring(ring_index)
        self.completed.add(RING_OFFSETS[ring_index])
        if self.all_rings_known:
            self._mark_dirty()
        self._touch()
        return self.get_current_target(player)

    def _predict_ring(self, ring_index: int) -> None:
        """Fill one ring's slots; the logged stronghold is slot 0, the rest are evenly spaced."""
        x, z = self.ring_strongholds[ring_index]
        count, magnitude = SH_PER_RING[ring_index], MAGNITUDE_PER_RING[ring_index]
        base, angle = RING_OFFSETS[ring_index], math.atan2(z, x)
        self.estimated_strongholds[base] = (x, z)
        for k in range(1, count):
            step = angle + 2 * math.pi * k / count
            self.estimated_strongholds[base + k] = (
                round(magnitude * math.cos(step)),
                round(magnitude * math.sin(step)),
            )

    # -------------------------------------------------------------- targeting
    def current_targets(self) -> dict[str, int | None]:
        """Each player's current stronghold slot. Planned routes win; players
        without one get the nearest stronghold nobody else is heading to."""
        done = self._done()
        targets: dict[str, int | None] = {}
        claimed: set[int] = set()
        for name, player in self.players.items():
            targets[name] = next(
                (i for i in player.route if i not in done and self.estimated_strongholds[i] is not None),
                None,
            )
            if targets[name] is not None:
                claimed.add(targets[name])
        for name, player in self.players.items():
            if targets[name] is None:
                options = [i for i in self.unvisited_indices() if i not in claimed]
                if options:
                    targets[name] = min(
                        options, key=lambda i: get_dist(player.position, self.estimated_strongholds[i])
                    )
                    claimed.add(targets[name])
        return targets

    def route_remaining(self, name: str) -> int:
        done = self._done()
        return sum(1 for i in self.players[name].route if i not in done)

    def _target_info(self, player: Player | None, index: int | None) -> dict:
        target = self.estimated_strongholds[index] if index is not None else None
        return {
            "player": player.name if player else None,
            "index": index,
            "target": target,
            "overworld": target,
            "nether": (round(target[0] / 8), round(target[1] / 8)) if target else None,
            "ring": ring_of(index) if target else None,
            "remaining": max(0, TOTAL_STRONGHOLDS - self.completed_count),
            "completed": self.completed_count,
            "angle": find_angle(player.position, target) if target and player else None,
            "distance": round(get_dist(player.position, target), 2) if target and player else None,
        }

    def get_current_target(self, player: str | None = None) -> dict:
        if not self.players:
            return self._target_info(None, None)
        resolved = self._player(player)
        return self._target_info(resolved, self.current_targets()[resolved.name])

    def get_targets(self) -> dict[str, dict]:
        targets = self.current_targets()
        return {name: self._target_info(p, targets[name]) for name, p in self.players.items()}

    # ---------------------------------------------------------------- actions
    def next_stronghold(self, player: str | None = None, index: int | None = None) -> dict:
        """Mark a stronghold found (default: the player's current target)."""
        return self._advance(player, index, completed=True)

    def mark_empty_sector(self, player: str | None = None, index: int | None = None) -> dict:
        """Skip an empty sector without increasing the count."""
        return self._advance(player, index, completed=False)

    def _advance(self, player_name: str | None, index: int | None, completed: bool) -> dict:
        """Idempotent: repeating an action on the same stronghold changes nothing."""
        player = self._player(player_name)
        if index is None:
            index = self.current_targets()[player.name]
            if index is None:
                return {**self._target_info(player, None), "changed": False}
        if not 0 <= index < TOTAL_STRONGHOLDS or self.estimated_strongholds[index] is None:
            raise ValueError(f"Stronghold #{index} is not known yet")
        if index in self.completed or (not completed and index in self.skipped):
            return {**self.get_current_target(player.name), "changed": False}

        target = self.estimated_strongholds[index]
        self._checkpoint(
            "complete" if completed else "skip",
            player.name,
            f"stronghold #{index} ({target[0]}, {target[1]}), ring {ring_of(index)}",
        )
        self.skipped.discard(index)  # a skipped sector can still turn out to hold one
        (self.completed if completed else self.skipped).add(index)
        player.path_history.append((player.position, target, "done" if completed else "skipped"))
        player.position = target
        for other in self.players.values():
            if index in other.route:
                other.route.remove(index)
        self._rebalance_if_idle()
        self._touch()
        return {**self.get_current_target(player.name), "changed": True}

    def _rebalance_if_idle(self) -> None:
        """If a player ran out of stops while others still have work, re-plan."""
        if len(self.players) < 2 or not self.plan_valid or self.plan_dirty:
            return
        if self.unvisited_indices() and any(self.route_remaining(n) == 0 for n in self.players):
            self._mark_dirty()

    def reset_spawn(self, x: float = 0, z: float = 0, player: str | None = None) -> dict:
        """Move a player (death, bed, portal hop) and re-plan from there."""
        who = self._player(player)
        self._checkpoint("reset", who.name, f"position set to ({x}, {z})")
        who.position = (x, z)
        if self.all_rings_known:
            self._mark_dirty()
        self._touch()
        return self.get_current_target(who.name)

    def set_count(self, count: int, player: str | None = None) -> dict:
        if not 0 <= count <= TOTAL_STRONGHOLDS:
            raise ValueError(f"count must be between 0 and {TOTAL_STRONGHOLDS}")
        actor = self._player(player).name if self.players else ""
        self._checkpoint("count", actor, f"count set to {count}")
        self.count_offset = int(count) - len(self.completed)
        self._touch()
        return self.get_current_target(player)

    def reoptimize(self, player: str | None = None) -> None:
        """Throw the current routes away and plan again from everyone's positions."""
        if not self.all_rings_known:
            raise ValueError("Log all eight ring strongholds before planning a route")
        actor = self._player(player).name if player and self.players else ""
        self._log({"t": time.time(), "player": actor, "action": "replan", "detail": "asked for a fresh route plan"})
        self._mark_dirty()
        self._touch()

    # ------------------------------------------------------------ undo / log
    def _core_state(self) -> dict:
        return {
            "ring_strongholds": [list(sh) if sh else None for sh in self.ring_strongholds],
            "completed": sorted(self.completed),
            "skipped": sorted(self.skipped),
            "count_offset": self.count_offset,
            "players": [p.to_dict() for p in self.players.values()],
            "plan_valid": self.plan_valid,
            "plan_dirty": self.plan_dirty,
        }

    def _restore_core(self, core: dict) -> None:
        self.ring_strongholds = [tuple(sh) if sh else None for sh in core["ring_strongholds"]]
        self.estimated_strongholds = [None] * TOTAL_STRONGHOLDS
        for ring_index, sh in enumerate(self.ring_strongholds):
            if sh is not None:
                self._predict_ring(ring_index)
        self.completed = {int(i) for i in core["completed"]}
        self.skipped = {int(i) for i in core["skipped"]}
        self.count_offset = int(core["count_offset"])
        self.players = {p["name"]: Player.from_dict(p) for p in map(dict, core["players"])}
        self.plan_valid = bool(core["plan_valid"])
        self.plan_dirty = bool(core["plan_dirty"])

    def _checkpoint(self, action: str, player: str, detail: str) -> None:
        """Record an undo point and a log line. Call before changing anything."""
        entry = {"t": time.time(), "player": player, "action": action, "detail": detail}
        self._undo.append({"core": self._core_state(), "entry": entry})
        del self._undo[: -self.undo_limit]
        self._log(entry)

    def _log(self, entry: dict) -> None:
        self.log.append(entry)
        del self.log[: -self.log_limit]

    def undo(self, player: str | None = None) -> dict:
        """Revert the most recent action by anyone."""
        if not self._undo:
            raise ValueError("Nothing to undo")
        item = self._undo.pop()
        self._restore_core(item["core"])
        actor = player if player and self.players and player in self.players else ""
        undone = item["entry"]
        self._log({
            "t": time.time(), "player": actor, "action": "undo",
            "detail": f"undid {undone['player'] or 'someone'}'s {undone['action']}: {undone['detail']}",
        })
        self.epoch += 1  # any solve in flight was computed for the state we just left
        self._touch()
        return self.get_current_target(actor or None)

    # --------------------------------------------------------------- planning
    def plan_request(self) -> PlanRequest | None:
        if not self.plan_needed:
            return None
        indices = self.unvisited_indices()
        if not indices:
            self.plan_valid, self.plan_dirty = True, False
            return None
        names = tuple(self.players)
        return PlanRequest(
            epoch=self.epoch,
            players=names,
            starts=tuple(self.players[n].position for n in names),
            indices=tuple(indices),
            points=tuple(self.estimated_strongholds[i] for i in indices),
        )

    def apply_plan(self, request: PlanRequest, routes: list[list[int]]) -> bool:
        """Install a finished solve. Returns False if it went stale meanwhile."""
        if request.epoch != self.epoch:
            return False
        done = self._done()
        for name, route in zip(request.players, routes):
            if name in self.players:
                self.players[name].route = [
                    request.indices[k] for k in route if request.indices[k] not in done
                ]
        self.plan_valid, self.plan_dirty, self.solver_error = True, False, None
        self._touch()
        return True

    def abandon_plan(self, request: PlanRequest, reason: str) -> None:
        """A solve failed: keep nearest-stronghold guidance instead of retrying forever."""
        if request.epoch == self.epoch:
            self.plan_dirty, self.solver_error = False, reason
            self._touch()

    def solve(self, time_limit_s: float | None = None) -> bool:
        """Plan synchronously (library use and tests; the web app solves in a thread)."""
        request = self.plan_request()
        if request is None:
            return False
        routes = solve_routes(request.starts, request.points, time_limit_s)
        return self.apply_plan(request, routes)

    # ------------------------------------------------------------ persistence
    def to_state(self) -> dict:
        return {
            "format": STATE_FORMAT,
            **self._core_state(),
            "log": list(self.log),
            "undo": list(self._undo),
        }

    def load_state(self, state: dict) -> None:
        if state.get("format") != STATE_FORMAT:
            raise ValueError("Unsupported state file")
        self._restore_core(state)
        self.log = list(state.get("log", []))
        self._undo = list(state.get("undo", []))
        self.epoch += 1
        self.solver_error = None
        if self.all_rings_known and self.players and not self.plan_valid:
            self.plan_dirty = True
        self._touch()

    def save(self, path: str | Path) -> None:
        write_text_atomic(path, json.dumps(self.to_state()))

    def load(self, path: str | Path) -> None:
        self.load_state(json.loads(Path(path).read_text(encoding="utf-8")))

    def update_map(self) -> dict:
        return render_map(self, self.output_dir)


def main() -> None:
    print("Use AllPortalsEngine from Python, or run the web app: python app.py")


if __name__ == "__main__":
    main()
