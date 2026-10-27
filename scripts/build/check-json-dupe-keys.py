#!/usr/bin/env python3
"""Exit 1 if any given JSON file has duplicate keys in the same object."""
import json
import sys


def main(paths):
    bad = False
    for path in paths:
        dupes = []

        def hook(pairs, path=path):
            seen = set()
            for k, _ in pairs:
                if k in seen:
                    dupes.append(k)
                seen.add(k)
            return dict(pairs)

        with open(path) as f:
            json.load(f, object_pairs_hook=hook)
        for k in dupes:
            print(f"{path}: duplicate key {k!r}")
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
