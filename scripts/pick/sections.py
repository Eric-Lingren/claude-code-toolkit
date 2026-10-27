"""Other-repos section helpers for rank-render.py.

Candidates whose `repo` differs from the current repo are not filtered out;
they render below the main list under 'other repos', tagged with their repo.
"""
OTHER_LIMIT = 3


def partition(ranked, repo):
    main = [c for c in ranked if c.get("repo") in (None, "", repo)]
    other = [c for c in ranked if c.get("repo") not in (None, "", repo)]
    return main, other


def render_other(other, limit=OTHER_LIMIT):
    if not other:
        return []
    lines = ["", "other repos", ""]
    for n, c in enumerate(other[:limit], 1):
        pts = f"  ~{c['points']}pt" if c.get("points") is not None else "  ⚠ unpointed"
        lines.append(f"{n}. [{c['repo']}] {c['id']}  {c['title']}{pts}")
        if c.get("_why"):
            lines.append(f"   why: {c['_why']}")
        lines.append(f"   start: wt {c['branch']}  ->  /grill-me {c['id']}")
    return lines
