#!/usr/bin/env python3
"""Verify mock data files exist and are valid. Usage: python -m scripts.seed_mock_data"""
import json
import random
from pathlib import Path


def main() -> None:
    random.seed(42)
    mock_dir = Path("data/mock")
    mock_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(mock_dir.glob("appstore_*.json"))
    if not files:
        print("No mock data files found in data/mock/. Add appstore_*.json files.")
        return

    total = 0
    for path in files:
        records = json.loads(path.read_text())
        print(f"{path.name}: {len(records)} records")
        total += len(records)

    print(f"\nTotal: {total} mock records across {len(files)} files")


if __name__ == "__main__":
    main()
