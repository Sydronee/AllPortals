# AllPortals

Route planner for all-portals Minecraft runs (1.9+), for one player or a small
team on the same world. Log one stronghold from each of the eight rings and it
predicts the other 120, plans the shortest walking routes, and keeps everyone's
progress in sync through a web page.

Forked from [TheTalkingMime/AllPortals](https://github.com/TheTalkingMime/AllPortals),
whose original script and ring maths this builds on.

# Installation

Python 3.10 or newer:

```sh
pip install -r requirements.txt
```

# Running a co-op run

```sh
python app.py
```

This listens on your machine's [Tailscale](https://tailscale.com) address
(falling back to `127.0.0.1` if Tailscale isn't found) and prints the URL.
Pass `--host` / `--port` to change that.

1. Everyone opens the URL and joins with a name (up to four players).
2. Log one stronghold per ring. After the eighth, routes are planned in the
   background. The page stays usable while it works, and everyone gets the
   nearest unclaimed stronghold in the meantime.
3. Each player sees their own target (Overworld and Nether coordinates, ring,
   distance and the yaw to face) plus where their teammates are heading.
   **Found it** and **Empty sector** act on the stronghold shown on screen,
   so a double click or a teammate getting there first never counts twice.
4. Died, used a bed, or hopped through a portal? **Reset my position** re-plans
   from where you are now. **Undo** reverts the last action by anyone, and the
   activity log shows who did what.

With several players the planner builds one open route per person, starting at
each player's position, and minimises the longest route so you finish together.
If someone runs out of stops while others have work left, routes are re-planned
automatically.

The run is saved to `data/state.json` after every action and reloaded on
startup, so restarting the server loses nothing.

## Keeping it private

By default access control is whatever your Tailscale network provides (the
server binds to the Tailscale address only). To also require a shared secret:

```sh
ALLPORTALS_TOKEN=pick-something python app.py
```

Friends open `http://<tailscale-ip>:5000/?token=pick-something` once and the
browser remembers it. To let a friend onto your tailnet without adding them to
it, use Tailscale's "share node" feature. Don't use Funnel: that puts the page
on the public internet.

## API

All endpoints return the full state (the same document as `GET /api/state`).

| Endpoint | Body |
| --- | --- |
| `GET /api/state` | |
| `POST /api/players` | `{"name"}` (idempotent) |
| `DELETE /api/players/{name}` | |
| `POST /api/ring-stronghold` | `{"x", "z", "player"?}` |
| `POST /api/complete` / `POST /api/skip` | `{"player", "index"?}` |
| `POST /api/reset-spawn` | `{"player", "x", "z"}` |
| `POST /api/count` | `{"count", "player"?}` |
| `POST /api/undo`, `POST /api/reoptimize` | `{"player"?}` |

Generated assets are served under `/static`; the saved run is not.

## OBS overlay

Add a **Browser** source in OBS and use `http://<tailscale-ip>:5000/overlay`
(add `?token=...` if you set a token). It refreshes itself every second and has
a transparent background. Choose what to show with `?show=`:

- `/overlay` shows the map (the default)
- `/overlay?show=map,count,fact` adds the completed count and the trivia line
- `/overlay?show=count` shows only the count

# Using the engine from Python

```python
from AllPortals import AllPortalsEngine

engine = AllPortalsEngine(output_dir="output", players=("Syd", "Friend"))
for x, z in ring_coordinates:
    engine.add_ring_stronghold(x, z)
engine.solve()                      # the web app does this on a background thread

print(engine.get_targets())         # every player's current target
engine.next_stronghold("Syd")       # mark Syd's target found
engine.mark_empty_sector("Friend")
engine.reset_spawn(500, -200, "Syd")
engine.undo()
engine.update_map()
```

Strongholds live in 128 fixed slots (ring 1 first), so an index always refers
to the same stronghold regardless of the order the rings were logged in.
`next_stronghold` and `mark_empty_sector` accept an explicit `index` and are
idempotent.

## OBS exports

`engine.update_map()` (called after every web action) writes these to
`output/`, atomically, so OBS never reads a half-written file:

- `sh_count.txt`: the completion count out of 128.
- `fun_facts.txt`: facts for notable and prime counts.
- `output.png`: the map. Each player has a colour, found strongholds are
  green, empty sectors red and unvisited ones grey.

# Development

```sh
pip install -r requirements-dev.txt
python -m pytest
```

GitHub Actions runs the tests on every push.
