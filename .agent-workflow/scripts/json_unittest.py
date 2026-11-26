#!/usr/bin/env python3
"""Run unittest discovery in the current directory and write per-test JSON.

Executed as a subprocess by base_head.py with cwd = an ephemeral checkout.
Usage: json_unittest.py <tests_dir> <out.json>
"""
import json
import sys
import unittest


class Rec(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.rows = []

    def _row(self, test, outcome, err=None):
        exc, msg = None, None
        if err is not None:
            exc = err[0].__name__
            msg = str(err[1]).splitlines()[0] if str(err[1]) else ""
        self.rows.append({"id": test.id(), "outcome": outcome, "exc_type": exc, "message": msg})

    def addSuccess(self, test):
        self._row(test, "pass")

    def addFailure(self, test, err):
        self._row(test, "fail", err)

    def addError(self, test, err):
        self._row(test, "error", err)

    def addSkip(self, test, reason):
        self.rows.append({"id": test.id(), "outcome": "skip", "exc_type": None, "message": reason})


def main():
    tests_dir, out = sys.argv[1], sys.argv[2]
    sys.path.insert(0, ".")
    suite = unittest.defaultTestLoader.discover(tests_dir, top_level_dir=".")
    res = Rec()
    suite.run(res)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"tests": res.rows}, f, indent=2)


if __name__ == "__main__":
    main()
