"""Frontend preference for /pick. Pure, in-memory.

stack(c) is the scorer's `stack` field when present, else a title heuristic
("FE"/"frontend" -> fe, "BE"/"backend" -> be), else None. penalty(c) is added
to the effort score so FE work ranks ahead of backend/infra work of similar size.
"""
import re

PENALTY = {"fe": 0, "full": 1, "be": 3, "infra": 3}
_FE = re.compile(r"\b(FE|frontend|front-end)\b", re.I)
_BE = re.compile(r"\b(BE|backend|back-end)\b", re.I)


def stack(c):
    if c.get("_stack") in PENALTY:
        return c["_stack"]
    t = c.get("title", "")
    fe, be = bool(_FE.search(t)), bool(_BE.search(t))
    return "full" if fe and be else "fe" if fe else "be" if be else None


def penalty(c):
    return PENALTY.get(stack(c), 0)
