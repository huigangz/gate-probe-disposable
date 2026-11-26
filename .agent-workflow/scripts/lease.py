#!/usr/bin/env python3
"""Task-scoped single-writer lease using Git ref compare-and-swap.

Ref:   refs/workflow/<task_id>/lease  -> blob holding the lease record (JSON)
State: <git-common-dir>/workflow-state/<task_id>/   (out of the candidate tree)

A local ref is coordination evidence only. It does not stop a shell from editing
files and does not prove the holder is alive. Stale leases are never auto-stolen:
`recover` needs an explicit --authorized-by.

Exit: 0 = ok, 3 = lease conflict (CAS lost / held by another owner), 2 = usage error.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

ZERO = "0" * 40


def git(*args, stdin=None, check=True):
    p = subprocess.run(["git", *args], input=stdin, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.strip())
    return p


def ref_for(task):
    return f"refs/workflow/{task}/lease"


def read(task):
    p = git("rev-parse", "--verify", "--quiet", ref_for(task), check=False)
    if p.returncode != 0:
        return ZERO, {"state": "free", "epoch": 0, "owner": None}
    oid = p.stdout.strip()
    return oid, json.loads(git("cat-file", "blob", oid).stdout)


def state_dir(task):
    common = git("rev-parse", "--git-common-dir").stdout.strip()
    d = os.path.join(os.path.abspath(common), "workflow-state", task)
    os.makedirs(d, exist_ok=True)
    return d


def journal(task, entry):
    path = os.path.join(state_dir(task), "lease-journal.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())


def cas(task, old_oid, record):
    new_oid = git("hash-object", "-w", "--stdin", stdin=json.dumps(record, sort_keys=True) + "\n").stdout.strip()
    p = git("update-ref", ref_for(task), new_oid, old_oid, check=False)
    return p.returncode == 0, new_oid, p.stderr.strip()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def out(obj, code=0):
    print(json.dumps(obj, indent=2))
    sys.exit(code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["acquire", "release", "status", "recover"])
    ap.add_argument("--task", required=True)
    ap.add_argument("--owner", help="role + agent id, e.g. impl:<agentId>")
    ap.add_argument("--authorized-by", help="required for recover")
    args = ap.parse_args()
    task = args.task
    old_oid, cur = read(task)

    if args.cmd == "status":
        out({"ref": ref_for(task), "oid": old_oid, "record": cur})

    if args.cmd in ("acquire", "release") and not args.owner:
        ap.error("--owner required")

    head = git("rev-parse", "HEAD").stdout.strip()
    if args.cmd == "acquire":
        if cur["state"] == "held":
            out({"result": "CONFLICT", "reason": "HELD", "holder": cur["owner"], "epoch": cur["epoch"]}, 3)
        rec = {"task_id": task, "state": "held", "owner": args.owner, "epoch": cur["epoch"] + 1,
               "claimed_head": head, "timestamp": now(), "expected_prior": old_oid}
    elif args.cmd == "release":
        if cur["state"] != "held" or cur["owner"] != args.owner:
            out({"result": "CONFLICT", "reason": "NOT_HOLDER", "holder": cur.get("owner"), "epoch": cur["epoch"]}, 3)
        rec = {"task_id": task, "state": "free", "owner": None, "epoch": cur["epoch"],
               "released_by": args.owner, "released_head": head, "timestamp": now(), "expected_prior": old_oid}
    else:  # recover
        if not args.authorized_by:
            out({"result": "REFUSED", "reason": "RECOVERY_REQUIRES_--authorized-by"}, 3)
        rec = {"task_id": task, "state": "free", "owner": None, "epoch": cur["epoch"],
               "recovered_from": cur.get("owner"), "authorized_by": args.authorized_by,
               "timestamp": now(), "expected_prior": old_oid}

    ok, new_oid, err = cas(task, old_oid, rec)
    entry = {"cmd": args.cmd, "by": args.owner or args.authorized_by, "ok": ok, "old": old_oid,
             "new": new_oid, "epoch": rec["epoch"], "pid": os.getpid(), "ts": now()}
    journal(task, entry)
    if not ok:
        out({"result": "CONFLICT", "reason": "CAS_LOST", "git": err, **entry}, 3)
    out({"result": "OK", "ref": ref_for(task), "oid": new_oid, "record": rec})


if __name__ == "__main__":
    main()
