#!/usr/bin/env python3
"""Append the cmux_probe event. Usage: probe-event.py REACHABLE(true|false) RC CONTEXT DETAIL"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import events  # noqa: E402


def main(argv):
    reachable, rc, context, detail = argv
    events.append(
        "cmux_probe", "_system", 0, request_id="cmux-probe",
        data={"reachable": reachable == "true", "rc": int(rc),
              "context": context, "detail": detail[:300]},
    )


if __name__ == "__main__":
    main(sys.argv[1:])
