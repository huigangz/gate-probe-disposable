#!/usr/bin/env bash
# Trusted helper (runs from protected main). Fetches candidate objects WITHOUT checking them out
# and exports BASE_SHA. Inputs via env: CANDIDATE_SHA, PR_NUMBER (optional), POLICY_SHA, TOKEN.
set -euo pipefail
if [ "${GITHUB_REF:-}" != "refs/heads/main" ]; then
  echo "::error::gate must run from refs/heads/main, got ${GITHUB_REF:-<unset>}"; exit 1
fi
[[ "$CANDIDATE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::candidate_sha must be 40 hex"; exit 1; }
if [ -n "${PR_NUMBER:-}" ]; then
  [[ "$PR_NUMBER" =~ ^[0-9]+$ ]] || { echo "::error::bad pr_number"; exit 1; }
  refspec="+refs/pull/${PR_NUMBER}/head:refs/remotes/candidate/pr"
else
  refspec="$CANDIDATE_SHA"
fi
auth=$(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic ${auth}" fetch --no-tags origin "$refspec"
git cat-file -e "${CANDIDATE_SHA}^{commit}"
BASE_SHA=$(git merge-base "$POLICY_SHA" "$CANDIDATE_SHA")
echo "BASE_SHA=${BASE_SHA}" >> "$GITHUB_ENV"
echo "event=${GITHUB_EVENT_NAME} ref=${GITHUB_REF} policy_sha=${POLICY_SHA} workflow_sha=${GITHUB_WORKFLOW_SHA:-} candidate_sha=${CANDIDATE_SHA} base_sha=${BASE_SHA}"
