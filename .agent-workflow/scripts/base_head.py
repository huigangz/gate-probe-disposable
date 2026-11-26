#!/usr/bin/env python3
"""BASE production code + HEAD test definitions, replayed in ephemeral clean exports.

No worktree, index or ref of the source repository is touched: trees are exported
with `git archive` into a temporary directory that is deleted afterwards.

Classification per planned test
  existing / characterization : BASE must be GREEN (all runs), HEAD GREEN
  confirming / deferred       : BASE must be VALID_RED (all runs, approved fingerprint), HEAD GREEN

BASE outcomes: GREEN | VALID_RED | INVALID_RED | FLAKY | MISSING | TIMEOUT | RUNNER_CRASH
Exit: 0 = all planned tests meet expectation, 1 = at least one does not, 2 = usage/infra.
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "json_unittest.py")


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if p.returncode != 0:
        print(f"git {' '.join(args)} failed: {p.stderr.decode(errors='replace')}", file=sys.stderr)
        sys.exit(2)
    return p.stdout


def export(repo, rev, dest, paths=()):
    data = git(repo, "archive", "--format=tar", rev, *paths)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")


def run_once(workdir, tests_dir, timeout):
    out = os.path.join(workdir, ".gate-result.json")
    if os.path.exists(out):
        os.remove(out)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("GITHUB_TOKEN", "GH_", "ACTIONS_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        p = subprocess.run([sys.executable, RUNNER, tests_dir, out], cwd=workdir, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"runner": "TIMEOUT", "tests": {}}
    if not os.path.exists(out):
        return {"runner": "RUNNER_CRASH", "tests": {}, "stderr": p.stderr[-2000:]}
    with open(out, encoding="utf-8") as f:
        rows = json.load(f)["tests"]
    return {"runner": "OK", "tests": {r["id"]: r for r in rows}}


def classify_base(runs, case):
    if any(r["runner"] != "OK" for r in runs):
        return next(r["runner"] for r in runs if r["runner"] != "OK"), None
    rows = [r["tests"].get(case["id"]) for r in runs]
    if any(x is None for x in rows):
        # an import failure hides the real test id behind unittest's _FailedTest loader entry
        loader_errs = [t for r in runs for t in r["tests"].values() if "_FailedTest" in t["id"]]
        if loader_errs:
            return "INVALID_RED", {"reason": "LOAD_OR_IMPORT_ERROR", "detail": loader_errs[0]}
        return "MISSING", None
    sig = {(x["outcome"], x["exc_type"], x["message"]) for x in rows}
    if len(sig) > 1:
        return "FLAKY", {"signatures": sorted(map(str, sig))}
    x = rows[0]
    if x["outcome"] == "pass":
        return "GREEN", None
    fp = case.get("fingerprint") or {}
    if (x["outcome"] == "fail" and x["exc_type"] == fp.get("exc_type", "AssertionError")
            and re.search(fp.get("message_regex", ""), x["message"] or "")):
        return "VALID_RED", {"observed": x}
    return "INVALID_RED", {"reason": "FINGERPRINT_MISMATCH_OR_NON_ASSERTION", "observed": x}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--plan", required=True, help="test-plan JSON (trusted / G0-approved)")
    ap.add_argument("--tests-dir", default="tests")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--out")
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    base = git(args.repo, "rev-parse", args.base).decode().strip()
    head = git(args.repo, "rev-parse", args.head).decode().strip()
    status_before = git(args.repo, "status", "--porcelain=v1").decode()

    tmp = tempfile.mkdtemp(prefix="basehead-")
    try:
        bdir, hdir = os.path.join(tmp, "base-code_head-tests"), os.path.join(tmp, "head")
        os.makedirs(bdir), os.makedirs(hdir)
        export(args.repo, base, bdir)                       # BASE production code
        shutil.rmtree(os.path.join(bdir, args.tests_dir), ignore_errors=True)
        export(args.repo, head, bdir, [args.tests_dir])     # identical HEAD test definitions
        export(args.repo, head, hdir)                       # full HEAD
        base_runs = [run_once(bdir, args.tests_dir, args.timeout) for _ in range(args.runs)]
        head_run = run_once(hdir, args.tests_dir, args.timeout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    results, ok = [], True
    for case in plan["tests"]:
        b, detail = classify_base(base_runs, case)
        h = head_run["tests"].get(case["id"])
        h_out = head_run["runner"] if head_run["runner"] != "OK" else (h["outcome"].upper() if h else "MISSING")
        expect_base = "GREEN" if case["class"] in ("existing", "characterization") else "VALID_RED"
        met = (b == expect_base) and (h_out == "PASS")
        ok = ok and met
        results.append({"id": case["id"], "class": case["class"], "expected_base": expect_base,
                        "base": b, "head": h_out, "meets_expectation": met, "detail": detail})

    report = {"base_sha": base, "head_sha": head, "runs": args.runs,
              "source_repo_status_unchanged": status_before == git(args.repo, "status", "--porcelain=v1").decode(),
              "verdict": "PASS" if ok else "FAIL", "results": results}
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
