#!/usr/bin/env python3
"""Open requests handed from a clicked review banner to the Review Watcher pane.

A click on a review banner runs outside cmux, where the cmux socket refuses
connections, so it cannot open the review workspace itself. It drops a request
file in STATE_DIR/open-requests instead. The queue view (inside cmux) takes it
within a second and runs review-open. Requests older than MAX_AGE_SECONDS are
dropped, so a click made while the pane was closed does not open a review later.

  add(pr)     queue a request; pr is a PR number, or None for the oldest ready review
  take()      selections of fresh requests, oldest first ("" = oldest ready review);
              every request file is removed, fresh or stale
"""
import os
import time
import uuid

import config

MAX_AGE_SECONDS = 600


def requests_dir():
    return os.path.join(config.STATE_DIR, "open-requests")


def add(pr=None, now=None):
    d = requests_dir()
    os.makedirs(d, exist_ok=True)
    now = time.time() if now is None else now
    name = f"{now:.3f}-{uuid.uuid4().hex[:8]}"
    tmp = os.path.join(d, "." + name)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("" if pr is None else str(int(pr)))
    os.replace(tmp, os.path.join(d, name))  # atomic: take() never sees half a file
    return name


def take(now=None):
    now = time.time() if now is None else now
    d = requests_dir()
    try:
        names = sorted(n for n in os.listdir(d) if not n.startswith("."))
    except FileNotFoundError:
        return []
    out = []
    for name in names:
        path = os.path.join(d, name)
        try:
            with open(path, encoding="utf-8") as fh:
                selection = fh.read().strip()
            os.remove(path)
        except OSError:
            continue
        try:
            ts = float(name.split("-")[0])
        except ValueError:
            continue
        if now - ts <= MAX_AGE_SECONDS:
            out.append(selection)
    return out
