# test_engine.py
from AllPortals import AllPortalsEngine

def test_engine():
    print("Initializing AllPortalsEngine...")
    engine = AllPortalsEngine()

    # Approximate coordinates for one stronghold in each of the 8 rings
    # (Using the magnitudes defined in the engine: 2048, 5120, 8192, etc.)
    sample_ring_strongholds = [
        (2048, 0),
        (5120, 0),
        (8192, 0),
        (11264, 0),
        (14336, 0),
        (17408, 0),
        (20480, 0),
        (23552, 0)
    ]

    print("Feeding 8 initial ring strongholds...")
    for i, (x, z) in enumerate(sample_ring_strongholds, 1):
        result = engine.add_ring_stronghold(x, z)
        print(f"Ring {i} logged -> Completed count: {engine.completed_count}")

    print(f"\nTSP Solved Status: {engine.solved}")
    assert engine.solved, "TSP should be solved automatically after 8 ring strongholds!"

    # Check current target
    target_info = engine.get_current_target()
    print(f"First Target Overworld: {target_info['overworld']}")
    print(f"First Target Nether: {target_info['nether']}")
    print(f"Remaining Strongholds: {target_info['remaining']}")

    print("\nTesting map rendering and export files...")
    export_paths = engine.update_map()
    print(f"Generated files: {export_paths}")

    print("\nTesting advancement (next stronghold)...")
    next_info = engine.next_stronghold()
    print(f"Advanced! New remaining count: {next_info['remaining']}")

    print("\n✅ All Step 2 engine tests passed successfully!")

if __name__ == "__main__":
    test_engine()