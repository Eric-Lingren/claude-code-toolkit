#!/usr/bin/env python3
"""Map free text after /pick to a bucket name. Read-only.

Usage: map-bucket.py "<free text>" [--repo <Org/Repo>] <bucket> [<bucket> ...]
       aliases come from PICK_ALIASES_JSON ({bucket: [alias,...]}) or, when
       --repo is given, the optional 'aliases' list on each pick_buckets entry
       in repo-policy.json (PICK_POLICY overrides the path).
Prints the bucket name and exits 0 on a match; prints nothing and exits 1 when
the text matches no bucket (caller falls back to the bucket menu).
Match order: exact bucket name, exact alias, then an alias or bucket name
contained in the text (longest wins). Case, hyphens and spaces are ignored.
"""
import json, os, re, sys


def norm(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def map_bucket(text, buckets, aliases=None):
    t = norm(text)
    if not t:
        return None
    names = {norm(b): b for b in buckets}
    if t in names:
        return names[t]
    terms = {}
    for b in buckets:
        for a in (aliases or {}).get(b, []):
            terms[norm(a)] = b
    if t in terms:
        return terms[t]
    terms.update(names)
    hits = [(len(k), b) for k, b in terms.items() if k and f" {k} " in f" {t} "]
    return max(hits)[1] if hits else None


def main(argv):
    repo = None
    if "--repo" in argv:
        i = argv.index("--repo")
        repo = argv[i + 1]
        del argv[i:i + 2]
    text, buckets = argv[0], argv[1:]
    aliases = json.loads(os.environ.get("PICK_ALIASES_JSON", "{}"))
    if repo:
        policy = os.environ.get("PICK_POLICY") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "../../resources/repo-policy.json")
        pb = (json.load(open(policy)).get(repo) or {}).get("pick_buckets") or {}
        aliases = {b: v.get("aliases", []) for b, v in pb.items()}
    hit = map_bucket(text, buckets, aliases)
    if hit:
        print(hit)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
