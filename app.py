"""FastAPI service for collaborative AllPortals tracking (up to four players).

Run with:

    python app.py                      # binds to this machine's Tailscale IP
    python app.py --host 0.0.0.0       # or any address you choose

Set ALLPORTALS_TOKEN to require a shared secret: friends open
``http://<tailscale-ip>:5000/?token=<secret>`` once and the browser remembers it.
Without a token, access control is whatever your Tailscale network provides.
"""
import argparse
import hmac
import os
import shutil
import subprocess
from pathlib import Path
from threading import Lock, Thread
from time import time_ns

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from AllPortals import TOTAL_STRONGHOLDS, AllPortalsEngine, UnknownPlayer
from solver import solve_routes

ROOT_DIR = Path(__file__).resolve().parent
HOME_DIR = Path(os.environ.get("ALLPORTALS_HOME") or ROOT_DIR)
OUTPUT_DIR = HOME_DIR / "output"  # served under /static and read by OBS
DATA_DIR = HOME_DIR / "data"  # private: the saved run lives here
STATE_FILE = DATA_DIR / "state.json"
INDEX_FILE = ROOT_DIR / "index.html"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="AllPortals API", version="2.0")
engine = AllPortalsEngine(output_dir=OUTPUT_DIR, background_path=ROOT_DIR / "rings.png", players=())
engine_lock = Lock()
_solving = False  # guarded by engine_lock

# Keep source files private; only generated assets are exposed.
app.mount("/static", StaticFiles(directory=OUTPUT_DIR), name="static")


# --------------------------------------------------------------------- auth
def _token() -> str | None:
    return os.environ.get("ALLPORTALS_TOKEN") or None


@app.middleware("http")
async def require_token(request: Request, call_next):
    token = _token()
    if token is None:
        return await call_next(request)
    given = (
        request.query_params.get("token")
        or request.headers.get("x-allportals-token")
        or request.cookies.get("ap_token")
        or ""
    )
    if not hmac.compare_digest(given.encode(), token.encode()):
        return PlainTextResponse("Unauthorized: open the link with ?token=...", status_code=401)
    response = await call_next(request)
    if request.query_params.get("token") == token:
        response.set_cookie("ap_token", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


# ------------------------------------------------------------------ solving
def _kick_solver() -> None:
    """Start a background solve if routes are out of date. Caller holds engine_lock."""
    global _solving
    if _solving:
        return
    request = engine.plan_request()
    if request is None:
        return
    _solving = True
    Thread(target=_solve_worker, args=(request,), daemon=True).start()


def _solve_worker(request) -> None:
    """Solve without holding the lock so the page stays responsive."""
    global _solving
    routes, error = None, None
    try:
        routes = solve_routes(request.starts, request.points)
    except Exception as exc:  # noqa: BLE001 - report it, keep serving
        error = f"{type(exc).__name__}: {exc}"
    with engine_lock:
        _solving = False
        if routes is not None:
            engine.apply_plan(request, routes)
        else:
            engine.abandon_plan(request, error)
        _commit()


def _commit() -> None:
    """Re-render exports, save the run and plan again if needed. Caller holds engine_lock."""
    engine.update_map()
    engine.save(STATE_FILE)
    _kick_solver()


def _load_saved_run() -> None:
    if not STATE_FILE.exists():
        return
    try:
        engine.load(STATE_FILE)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        backup = STATE_FILE.with_suffix(".json.bak")
        shutil.move(STATE_FILE, backup)
        print(f"Could not read the saved run ({exc}); moved it to {backup} and started fresh.")


with engine_lock:
    _load_saved_run()
    _commit()


# ------------------------------------------------------------------ payload
def _read_fun_fact() -> str:
    try:
        return (OUTPUT_DIR / "fun_facts.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _xz(coords) -> dict:
    return {"x": coords[0], "z": coords[1]}


def _state_payload() -> dict:
    """Build a state response while the caller owns ``engine_lock``."""
    players = []
    for name, info in engine.get_targets().items():
        player = engine.players[name]
        target = None
        if info["index"] is not None:
            target = {
                "index": info["index"],
                "overworld": _xz(info["overworld"]),
                "nether": _xz(info["nether"]),
                "distance": info["distance"],
                "ring": info["ring"],
                "angle": info["angle"],
            }
        players.append({
            "name": name,
            "color": player.color,
            "position": _xz(player.position),
            "route_remaining": engine.route_remaining(name),
            "target": target,
        })
    image = OUTPUT_DIR / "output.png"
    stamp = image.stat().st_mtime_ns if image.exists() else time_ns()
    return {
        "version": engine.version,
        "completed_count": f"{engine.completed_count} / {TOTAL_STRONGHOLDS}",
        "ring_progress": [r for r, sh in enumerate(engine.ring_strongholds, 1) if sh is not None],
        "mode": engine.mode,
        "is_solved": engine.plan_valid and not engine.plan_dirty,
        "solving": _solving or engine.plan_needed,
        "solver_error": engine.solver_error,
        "players": players,
        "log": list(reversed(engine.log[-20:])),
        "can_undo": engine.can_undo,
        "fun_fact": _read_fun_fact(),
        "image_url": f"/static/output.png?t={stamp}",
    }


def _mutate(action) -> dict:
    """Run one engine action under the lock, then persist and answer with fresh state."""
    with engine_lock:
        try:
            action()
        except UnknownPlayer as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _commit()
        return _state_payload()


# ------------------------------------------------------------------- models
class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlayerRequest(_Request):
    name: str = Field(min_length=1, max_length=20)


class RingStrongholdRequest(_Request):
    x: StrictInt
    z: StrictInt
    player: str | None = None


class ProgressRequest(_Request):
    player: str
    index: StrictInt | None = Field(default=None, ge=0, lt=TOTAL_STRONGHOLDS)


class ResetRequest(_Request):
    player: str
    x: StrictInt = 0
    z: StrictInt = 0


class CountRequest(_Request):
    count: StrictInt = Field(ge=0, le=TOTAL_STRONGHOLDS)
    player: str | None = None


class UndoRequest(_Request):
    player: str | None = None


# ---------------------------------------------------------------- endpoints
@app.get("/")
def root() -> FileResponse:
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=404, detail="index.html is not available")
    return FileResponse(INDEX_FILE)


@app.get("/api/state")
def get_state() -> dict:
    with engine_lock:
        return _state_payload()


@app.post("/api/players")
def join(request: PlayerRequest) -> dict:
    return _mutate(lambda: engine.add_player(request.name))


@app.delete("/api/players/{name}")
def leave(name: str) -> dict:
    return _mutate(lambda: engine.remove_player(name))


@app.post("/api/ring-stronghold")
def add_ring_stronghold(request: RingStrongholdRequest) -> dict:
    return _mutate(lambda: engine.add_ring_stronghold(request.x, request.z, request.player))


@app.post("/api/complete")
def complete(request: ProgressRequest) -> dict:
    return _mutate(lambda: engine.next_stronghold(request.player, request.index))


@app.post("/api/skip")
def skip(request: ProgressRequest) -> dict:
    return _mutate(lambda: engine.mark_empty_sector(request.player, request.index))


@app.post("/api/reset-spawn")
def reset_spawn(request: ResetRequest) -> dict:
    return _mutate(lambda: engine.reset_spawn(request.x, request.z, request.player))


@app.post("/api/count")
def set_count(request: CountRequest) -> dict:
    return _mutate(lambda: engine.set_count(request.count, request.player))


@app.post("/api/undo")
def undo(request: UndoRequest) -> dict:
    return _mutate(lambda: engine.undo(request.player))


@app.post("/api/reoptimize")
def reoptimize(request: UndoRequest) -> dict:
    return _mutate(lambda: engine.reoptimize(request.player))


# --------------------------------------------------------------------- main
def tailscale_ip() -> str | None:
    """This machine's Tailscale IPv4 address, if the CLI is available."""
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = result.stdout.split()
    return lines[0] if result.returncode == 0 and lines else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the AllPortals web service")
    parser.add_argument("--host", help="address to bind (default: your Tailscale IP, else 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    host = args.host or tailscale_ip() or "127.0.0.1"
    if args.host is None and host == "127.0.0.1":
        print("Tailscale IP not found; listening on this machine only. Pass --host to change that.")
    print(f"AllPortals is available at http://{host}:{args.port}/")
    if _token():
        print("Token protection is on: share http://%s:%d/?token=<your token>" % (host, args.port))
    uvicorn.run(app, host=host, port=args.port)
