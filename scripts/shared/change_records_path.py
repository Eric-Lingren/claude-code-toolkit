#!/usr/bin/env python3
"""change_records_path.py - single source of truth for the change-records folder.

Returns <repo root>/attribution/change-records, where repo root is the parent
of the real (symlink-resolved) claude-code-shared directory.

Only computes the path: never creates the folder, never reads a record.

Library:  from change_records_path import change_records_path
CLI:      python3 change_records_path.py   (prints the path, nothing else)
"""

from pathlib import Path


def change_records_path() -> Path:
    # this file: <toolkit>/scripts/shared/change_records_path.py
    toolkit = Path(__file__).resolve().parent.parent.parent
    return toolkit.parent / "attribution" / "change-records"


if __name__ == "__main__":
    print(change_records_path())
