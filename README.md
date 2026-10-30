# Installation

Install the Python dependencies:

```sh
pip install -r requirements.txt
```

# Usage

The core workflow is exposed by `AllPortalsEngine`. Record one stronghold
from each ring; the in-memory OR-Tools solver runs automatically after the
eighth coordinate:

```python
from AllPortals import AllPortalsEngine

engine = AllPortalsEngine(output_dir=".")
for x, z in ring_coordinates:
    engine.add_ring_stronghold(x, z)

print(engine.get_current_target())
engine.next_stronghold()
engine.mark_empty_sector()
engine.reset_spawn()
engine.set_count(10)
engine.update_map()
```

No `.qs` file, external Concorde installation, or interactive `input()` loop
is required. Running `python AllPortals.py` only prints a migration message;
applications should use the engine methods above.

## Engine actions

These methods return dictionaries describing the resulting state:

- `add_ring_stronghold(x, z)` records the first stronghold in a ring.
- `next_stronghold()` marks the current target complete.
- `mark_empty_sector()` skips an empty sector without increasing the count.
- `reset_spawn(x=0, z=0)` recalculates the remaining route from a position.
- `set_count(count)` overrides the displayed completion count.
- `get_current_target()` returns Overworld and Nether coordinates, ring, and
  remaining count.

## OBS exports

`engine.update_map()` regenerates these files in `output_dir`:

- `sh_count.txt`: the completion count out of 128.
- `fun_facts.txt`: facts for notable and prime counts.
- `output.png`: a headless map showing visited, current, and unvisited
  strongholds.
## FastAPI service

Run the collaborative backend on the host's Tailscale interface with:

```sh
uvicorn app:app --host 0.0.0.0 --port 5000
```

Friends can then use the machine's Tailscale IP or MagicDNS hostname. Generated
assets are available under `/static`; the API state is available at
`/api/state`.

