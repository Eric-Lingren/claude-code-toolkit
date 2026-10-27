"""Focus boost for /pick shared-pool buckets. Pure, in-memory; nothing is stored.

boost(cands, focus) stable-moves candidates whose project matches `focus`
(case-insensitive exact match) ahead of the rest. It never removes anything.
No focus (None, "" or "No focus") returns the list unchanged.
"""

NO_FOCUS = "No focus"


def active(focus):
    return bool(focus) and focus.strip().lower() != NO_FOCUS.lower()


def boost(cands, focus):
    if not active(focus):
        return list(cands)
    f = focus.strip().lower()
    hit = lambda c: (c.get("project") or "").strip().lower() == f
    return [c for c in cands if hit(c)] + [c for c in cands if not hit(c)]
