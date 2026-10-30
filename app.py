"""FastAPI service for collaborative AllPortals tracking.

Run with:

    uvicorn app:app --host 0.0.0.0 --port 5000

Binding to ``0.0.0.0`` makes the service reachable through the host's
Tailscale IP or MagicDNS name. Authentication and access control should be
provided by the Tailscale network or an upstream proxy.
"""

from pathlib import Path
from threading import Lock
from time import time_ns

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from AllPortals import AllPortalsEngine
from utils import get_dist


ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
INDEX_FILE = ROOT_DIR / "index.html"

app = FastAPI(title="AllPortals API", version="1.0")
engine = AllPortalsEngine(output_dir=OUTPUT_DIR, background_path=ROOT_DIR / "rings.png")
engine_lock = Lock()

# Keep source files private; only generated assets are exposed.
app.mount("/static", StaticFiles(directory=OUTPUT_DIR), name="static")


class RingStrongholdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: StrictInt
    z: StrictInt


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    x: StrictInt | None = None
    z: StrictInt | None = None
    count: StrictInt | None = Field(default=None, ge=0, le=128)

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        command = value.strip().lower()
        if command not in {"next", "empty", "reset", "edit"}:
            raise ValueError("command must be one of: next, empty, reset, edit")
        return command


def _read_fun_fact() -> str:
    facts_file = OUTPUT_DIR / "fun_facts.txt"
    try:
        return facts_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _state_payload() -> dict:
    """Build a state response while the caller owns ``engine_lock``."""
    target_info = engine.get_current_target()
    target = target_info["overworld"]
    if target is None:
        current_target = None
    else:
        current_target = {
            "overworld": {"x": target[0], "z": target[1]},
            "nether": {
                "x": target_info["nether"][0],
                "z": target_info["nether"][1],
            },
            "distance": round(get_dist(engine.current_position, target), 2),
            "ring": target_info["ring"],
            "angle": target_info["angle"],
        }

    image = OUTPUT_DIR / "output.png"
    timestamp = image.stat().st_mtime_ns if image.exists() else time_ns()
    return {
        "completed_count": f"{engine.completed_count} / 128",
        "ring_progress": [
            ring for ring, stronghold in enumerate(engine.ring_strongholds, 1)
            if stronghold is not None
        ],
        "mode": engine.mode,
        "current_target": current_target,
        "fun_fact": _read_fun_fact(),
        "is_solved": engine.solved,
        "image_url": f"/static/output.png?t={timestamp}",
    }


def _refresh_and_return() -> dict:
    engine.update_map()
    return _state_payload()


@app.get("/")
def root() -> FileResponse:
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=404, detail="index.html is not available")
    return FileResponse(INDEX_FILE)


@app.get("/api/state")
def get_state() -> dict:
    with engine_lock:
        return _state_payload()


@app.post("/api/ring-stronghold")
def add_ring_stronghold(request: RingStrongholdRequest) -> dict:
    with engine_lock:
        try:
            engine.add_ring_stronghold(request.x, request.z)
            return _refresh_and_return()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/action")
def action(request: ActionRequest) -> dict:
    with engine_lock:
        try:
            if request.command == "next":
                engine.next_stronghold()
            elif request.command == "empty":
                engine.mark_empty_sector()
            elif request.command == "reset":
                engine.reset_spawn(request.x or 0, request.z or 0)
            elif request.command == "edit":
                if request.count is None:
                    raise HTTPException(status_code=422, detail="'count' is required for edit")
                engine.set_count(request.count)
            return _refresh_and_return()
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
