"""Geometry, pathfinding, and headless rendering helpers for AllPortals."""

import math
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    return all(n % i for i in range(3, int(math.sqrt(n)) + 1, 2))


def get_dist(x1: Sequence[float], x2: Sequence[float]) -> float:
    return float(math.hypot(x1[0] - x2[0], x1[1] - x2[1]))


def getRing(coords: Sequence[float]) -> int:
    distance = get_dist((0, 0), coords)
    bounds = [
        (1280, 2816), (4352, 5888), (7424, 8960), (10496, 12032),
        (13568, 15104), (16640, 18176), (19712, 21248), (22784, 24320),
    ]
    for index, (lower, upper) in enumerate(bounds, 1):
        if lower < distance < upper:
            return index
    return 0


def find_angle(pos: Sequence[float], nexsh: Sequence[float]) -> float:
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


def generatePath(shs: Sequence[Sequence[float]], pos: Sequence[float]):
    """Return an in-memory nearest-neighbour path.

    Kept as a small compatibility helper for callers of the old utility
    function. The engine uses its OR-Tools solver directly.
    """
    if not shs:
        return {}, -1
    remaining = list(range(len(shs)))
    current = min(remaining, key=lambda i: get_dist(shs[i], pos))
    order = [current]
    remaining.remove(current)
    while remaining:
        current = min(remaining, key=lambda i: get_dist(shs[order[-1]], shs[i]))
        order.append(current)
        remaining.remove(current)
    return {order[i]: order[i + 1] for i in range(len(order) - 1)}, order[0]


def updateCount(count: int, output_dir: str | Path = ".") -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "sh_count.txt").write_text(f"{count}/128\n", encoding="utf-8")
    facts = []
    if count == 69:
        facts.append("nice.")
    if count == 71:
        facts.append("BRA7-1L")
    if is_prime(count):
        facts.append("(prime)")
    (output / "fun_facts.txt").write_text("\n".join(facts) + ("\n" if facts else ""), encoding="utf-8")


def update_map(engine, output_dir: str | Path = ".") -> dict:
    """Render the engine state and regenerate all OBS-friendly exports."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    size = 870
    image = Image.new("RGBA", (size, size), "white")
    background = Path(getattr(engine, "background_path", "rings.png"))
    if background.exists():
        with Image.open(background) as rings:
            image.alpha_composite(rings.convert("RGBA").resize((size, size)))
    draw = ImageDraw.Draw(image)

    def point(coords):
        return (round((coords[0] + 24320) * size / 48640), round((24320 - coords[1]) * size / 48640))

    for index, sh in enumerate(engine.estimated_strongholds):
        color = "gray"
        if sh in engine.completed_coordinates:
            color = "green"
        elif index == engine.current_target_index:
            color = "blue"
        x, y = point(sh)
        radius = 4 if color != "gray" else 3
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    # Show manually logged ring coordinates even while the tour is unavailable.
    for ring, sh in enumerate(engine.ring_strongholds, 1):
        if sh is not None:
            x, y = point(sh)
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="cyan", outline="black", width=1)

    history = engine.path_history
    for start, end, color in history:
        draw.line((point(start), point(end)), fill=color, width=2)
    if engine.current_position is not None and engine.get_current_target()["target"] is not None:
        draw.line((point(engine.current_position), point(engine.get_current_target()["overworld"])), fill="blue", width=2)

    image.save(output / "output.png")
    updateCount(engine.completed_count, output)
    return {
        "output": str(output / "output.png"),
        "count": str(output / "sh_count.txt"),
        "facts": str(output / "fun_facts.txt"),
    }


def printHelp() -> None:
    print("Use the AllPortalsEngine API: next_stronghold, mark_empty_sector, reset_spawn, set_count.")


def graphAddSH(*args, **kwargs):
    """Deprecated plotting hook retained for source compatibility."""
    return None, None
