"""Geometry, pathfinding, and headless rendering helpers for AllPortals."""

import math
import os
import time
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

RING_BOUNDS = (
    (1280, 2816), (4352, 5888), (7424, 8960), (10496, 12032),
    (13568, 15104), (16640, 18176), (19712, 21248), (22784, 24320),
)
WORLD_HALF = 24320   # outer edge of ring 8; the map spans +-WORLD_HALF blocks
MAP_SIZE = 870       # output.png size in pixels


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    return all(n % i for i in range(3, int(math.sqrt(n)) + 1, 2))


def get_dist(x1: Sequence[float], x2: Sequence[float]) -> float:
    return float(math.hypot(x1[0] - x2[0], x1[1] - x2[1]))


def get_ring(coords: Sequence[float]) -> int:
    """Ring (1-8) containing the coordinates, or 0 if they fall between rings."""
    distance = get_dist((0, 0), coords)
    for index, (lower, upper) in enumerate(RING_BOUNDS, 1):
        if lower <= distance <= upper:
            return index
    return 0


def find_angle(pos: Sequence[float], target: Sequence[float]) -> float:
    """Minecraft yaw (F3 'facing') to look from ``pos`` towards ``target``.

    0 is south (+Z), 90 is west (-X), +-180 is north, -90 is east.
    """
    return round(math.degrees(math.atan2(pos[0] - target[0], target[1] - pos[1])), 2)


def _replace(tmp: Path, dest: Path, attempts: int = 5) -> None:
    """os.replace with retries: on Windows OBS can briefly hold a file open."""
    for attempt in range(attempts - 1):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, dest)


def write_text_atomic(path: str | Path, text: str) -> None:
    """Write a file so readers (OBS) never see it half-written."""
    dest = Path(path)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace(tmp, dest)


def write_counter_files(count: int, output_dir: str | Path = ".") -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_text_atomic(output / "sh_count.txt", f"{count}/128\n")
    facts = []
    if count == 69:
        facts.append("nice.")
    if count == 71:
        facts.append("BRA7-1L")
    if is_prime(count):
        facts.append("(prime)")
    write_text_atomic(output / "fun_facts.txt", "\n".join(facts) + ("\n" if facts else ""))


DONE_COLOR = "#22c55e"
SKIPPED_COLOR = "#ef4444"
UNVISITED_COLOR = "#9ca3af"
RING_MARKER_COLOR = "cyan"


def update_map(engine, output_dir: str | Path = ".") -> dict:
    """Render the engine state and regenerate all OBS-friendly exports."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    size = MAP_SIZE
    image = Image.new("RGBA", (size, size), "white")
    background = Path(getattr(engine, "background_path", "rings.png"))
    if background.exists():
        with Image.open(background) as rings:
            image.alpha_composite(rings.convert("RGBA").resize((size, size)))
    draw = ImageDraw.Draw(image)

    def point(coords):
        return (
            round((coords[0] + WORLD_HALF) * size / (2 * WORLD_HALF)),
            round((WORLD_HALF - coords[1]) * size / (2 * WORLD_HALF)),
        )

    def dot(coords, radius, fill, outline=None, width=1):
        x, y = point(coords)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill, outline=outline, width=width)

    # Where each player has been (their colour) and what they skipped (grey).
    for player in engine.players.values():
        for start, end, kind in player.path_history:
            draw.line((point(start), point(end)), fill=player.color if kind == "done" else UNVISITED_COLOR, width=2)

    for index, sh in enumerate(engine.estimated_strongholds):
        if sh is None:
            continue
        if index in engine.completed:
            dot(sh, 4, DONE_COLOR)
        elif index in engine.skipped:
            dot(sh, 4, SKIPPED_COLOR)
        else:
            dot(sh, 3, UNVISITED_COLOR)

    # Manually logged ring strongholds stay visible even before a route exists.
    for sh in engine.ring_strongholds:
        if sh is not None:
            dot(sh, 6, RING_MARKER_COLOR, outline="black")

    # Each player's position, current target and the leg between them.
    targets = engine.current_targets()
    for player in engine.players.values():
        index = targets.get(player.name)
        if index is not None:
            target = engine.estimated_strongholds[index]
            draw.line((point(player.position), point(target)), fill=player.color, width=2)
            dot(target, 8, None, outline=player.color, width=3)
        dot(player.position, 5, player.color, outline="white")

    # Legend (large enough to read in an OBS scene)
    font = ImageFont.load_default(size=18)
    for row, player in enumerate(engine.players.values()):
        top = 8 + row * 24
        draw.rectangle((8, top, 24, top + 16), fill=player.color)
        draw.text((32, top - 1), player.name, fill="black", font=font)

    tmp_png = output / "output.png.tmp"
    image.save(tmp_png, format="PNG")
    _replace(tmp_png, output / "output.png")
    write_counter_files(engine.completed_count, output)

    return {
        "output": str(output / "output.png"),
        "count": str(output / "sh_count.txt"),
        "facts": str(output / "fun_facts.txt"),
    }
