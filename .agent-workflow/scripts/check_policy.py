#!/usr/bin/env python3
"""Static path/ownership policy check. Never executes candidate code.

Modes
  gate : authority check of every path changed in BASE..HEAD (CI and local preflight)
  own  : a returning agent checks ONLY its own owned paths since its last checkpoint

Exit codes: 0 = PASS (possibly with review flags), 1 = REJECT, 2 = usage/infra error.
"""
import argparse
import json
import subprocess
import sys


def git(repo, *args, check=True):
    p = subprocess.run(
        ["git", "-C", repo, "-c", "core.quotepath=off", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and p.returncode != 0:
        print(f"git {' '.join(args)} failed: {p.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return p.stdout


def under(path, prefixes):
    return any(path == p or path.startswith(p) for p in prefixes)


def role_of(path, policy):
    for role, prefixes in policy["ownership"].items():
        if under(path, prefixes):
            return role
    return None


def name_status(repo, a, b):
    """Return [(status, old_path, new_path)] for a..b with rename detection."""
    out = git(repo, "diff", "--no-ext-diff", "--no-textconv", "-M", "--name-status", "-z", a, b)
    toks = [t for t in out.split("\0") if t != ""]
    rows, i = [], 0
    while i < len(toks):
        st = toks[i]
        if st[0] in ("R", "C"):
            rows.append((st[0], toks[i + 1], toks[i + 2]))
            i += 3
        else:
            rows.append((st[0], toks[i + 1], toks[i + 1]))
            i += 2
    return rows


def numstat(repo, a, b, path):
    out = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--numstat", a, b, "--", path).strip()
    if not out:
        return 0, 0
    added, deleted = out.split("\t")[:2]
    if added == "-":  # binary
        return 1, 1
    return int(added), int(deleted)


def blob(repo, rev, path):
    return git(repo, "rev-parse", f"{rev}:{path}", check=False).strip()


def gate(args, policy):
    repo, base, head = args.repo, args.base, args.head
    approvals = []
    if args.approvals:
        with open(args.approvals, encoding="utf-8") as f:
            approvals = json.load(f)
    approved = {(a["path"], a["head_blob"]) for a in approvals}
    violations, review = [], []

    # 1. whole-range checks: protected, unowned, locked tests
    for st, old, new in name_status(repo, base, head):
        for p in {old, new}:
            if under(p, policy["protected"]):
                violations.append({"rule": "PROTECTED_PATH", "path": p, "status": st})
            elif under(p, policy["shared_requires_preauth"]):
                if (p, blob(repo, head, p)) not in approved:
                    violations.append({"rule": "SHARED_PATH_NOT_PREAUTHORIZED", "path": p, "status": st})
            elif role_of(p, policy) is None:
                violations.append({"rule": "UNOWNED_PATH", "path": p, "status": st})
        # locked tests = test files that already exist at BASE
        if under(old, policy["locked_test_roots"]) and st != "A":
            if st in ("D", "R"):
                violations.append({"rule": "LOCKED_TEST_DELETED_OR_RENAMED", "path": old, "status": st})
                continue
            added, deleted = numstat(repo, base, head, old)
            if deleted > 0:
                if (old, blob(repo, head, old)) in approved:
                    review.append({"flag": "LOCKED_TEST_MODIFICATION_APPROVED", "path": old,
                                   "added": added, "deleted": deleted})
                else:
                    violations.append({"rule": "LOCKED_TEST_MODIFIED_WITHOUT_APPROVAL", "path": old,
                                       "added": added, "deleted": deleted,
                                       "head_blob": blob(repo, head, old)})
            elif added > 0:
                review.append({"flag": "LOCKED_TEST_ADDITIONS_NEED_REVIEW", "path": old, "added": added})
        elif under(new, policy["locked_test_roots"]) and st == "A":
            review.append({"flag": "NEW_TEST_FILE_NEEDS_REVIEW", "path": new})

    # 2. per-commit role trailer vs ownership (catches drift; not proof of actor identity)
    trailer = policy["role_trailer"]
    for sha in git(repo, "rev-list", "--reverse", "--no-merges", f"{base}..{head}").split():
        role = git(repo, "log", "-1", f"--format=%(trailers:key={trailer},valueonly)", sha).strip()
        if role not in policy["ownership"]:
            violations.append({"rule": "MISSING_OR_UNKNOWN_ROLE_TRAILER", "commit": sha, "role": role})
            continue
        for st, old, new in name_status(repo, f"{sha}^", sha):
            for p in {old, new}:
                owner = role_of(p, policy)
                if owner is not None and owner != role:
                    violations.append({"rule": "CROSS_OWNERSHIP_EDIT", "commit": sha, "role": role,
                                       "path": p, "owner": owner})

    return {"mode": "gate", "base": git(repo, "rev-parse", base).strip(),
            "head": git(repo, "rev-parse", head).strip(),
            "verdict": "REJECT" if violations else "PASS",
            "violations": violations, "review_required": review}


def own(args, policy):
    repo, role, cp = args.repo, args.role, args.checkpoint
    specs = policy["ownership"][role]
    findings = []
    committed = git(repo, "diff", "--no-ext-diff", "--no-textconv", "-M", "--name-status", cp, "HEAD", "--", *specs).strip()
    if committed:
        findings.append({"rule": "OWNED_PATH_CHANGED_SINCE_CHECKPOINT", "detail": committed.splitlines()})
    # dirty state (staged, unstaged, untracked, renames) restricted to owned pathspecs
    dirty = git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--", *specs).rstrip()
    if dirty:
        findings.append({"rule": "OWNED_PATH_DIRTY", "detail": dirty.splitlines()})
    return {"mode": "own", "role": role, "checkpoint": git(repo, "rev-parse", cp).strip(),
            "head": git(repo, "rev-parse", "HEAD").strip(), "owned_pathspecs": specs,
            "verdict": "REJECT" if findings else "PASS", "violations": findings, "review_required": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["gate", "own"], required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--policy", required=True, help="policy.json from the TRUSTED source, not the candidate tree")
    ap.add_argument("--approvals", help="approvals.json from the TRUSTED source")
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--role")
    ap.add_argument("--checkpoint")
    ap.add_argument("--out")
    args = ap.parse_args()
    with open(args.policy, encoding="utf-8") as f:
        policy = json.load(f)
    if args.mode == "gate":
        if not args.base:
            ap.error("--base required")
        result = gate(args, policy)
    else:
        if not (args.role and args.checkpoint):
            ap.error("--role and --checkpoint required")
        result = own(args, policy)
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.exit(0 if result["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
