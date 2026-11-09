#!/usr/bin/env bash
# Runs the Gate scan on a local clone, exactly as the org ruleset runs it.
#
# Usage: gate-scan.sh OWNER/REPO CLONE_DIR
# Environment:
#   GATE_REF       giskard-actions ref whose scan to run, to test an unreleased
#                  scan. Default: the SHA the org ruleset pins on the default
#                  branch, else the latest giskard-actions release.
#   ZIZMOR_RUNNER  docker (default when Docker runs) or uvx.
# Exit status: the scan's: 0 means the Gate passes. 2 with a reason on stderr
# when the scan could not start (GitHub unreachable, a ruleset that pins a ref
# and a SHA that disagree).
set -euo pipefail
# `git rebase --exec` in a linked worktree exports GIT_DIR, which would point the
# git calls below at the clone instead of the Gate's checkout.
# shellcheck disable=SC2046 # one variable name per word
unset $(git rev-parse --local-env-vars)

fail() {
  echo "gate-scan.sh: $*" >&2
  exit 2
}

repo="${1:?usage: gate-scan.sh OWNER/REPO CLONE_DIR}"
clone=$(cd "${2:?usage: gate-scan.sh OWNER/REPO CLONE_DIR}" && pwd)
gate_repo=Giskard-AI/giskard-actions
gate_url="https://github.com/${gate_repo}.git"

branch=$(gh api "repos/${repo}" --jq .default_branch) || fail "cannot read ${repo} from the GitHub API."
gate_id=$(gh api "repos/${gate_repo}" --jq .id) || fail "cannot read ${gate_repo} from the GitHub API."
# The Gate: an organization ruleset requiring zizmor-scan.yml of giskard-actions, as "<sha> <ref>".
pin=$(gh api "repos/${repo}/rules/branches/${branch}?per_page=100" --jq "[.[]
  | select(.type == \"workflows\" and .ruleset_source_type == \"Organization\") | .parameters.workflows[]
  | select(.path == \".github/workflows/zizmor-scan.yml\" and .repository_id == ${gate_id})
  | \"\(.sha) \(.ref // \"\")\"][0] // empty") || fail "cannot read the rules on ${repo}@${branch}."
read -r gate_sha gate_pin_ref <<<"${pin}" || true
if [[ -n "${gate_sha:-}" && -n "${gate_pin_ref:-}" ]]; then
  remote=$(git ls-remote "${gate_url}" "${gate_pin_ref}" "${gate_pin_ref}^{}") || fail "cannot reach ${gate_url}."
  cut -f1 <<<"${remote}" | grep -qx "${gate_sha}" \
    || fail "the Gate ruleset pins ${gate_sha} as ${gate_pin_ref}, which points elsewhere: an org admin fixes the ruleset."
fi
if [[ -z "${GATE_REF:-}" && -z "${gate_sha:-}" ]]; then
  GATE_REF=$(gh api "repos/${gate_repo}/releases/latest" --jq .tag_name) || fail "cannot read the latest ${gate_repo} release."
fi
gate_ref="${GATE_REF:-${gate_sha}}"
echo "Gate ruleset: ${gate_sha:-not active yet}; scanning with giskard-actions@${gate_ref}" >&2

gate_dir=$(mktemp -d)
trap 'rm -rf "${gate_dir}"' EXIT
git -C "${gate_dir}" init -q
git -C "${gate_dir}" fetch -q --depth 1 "${gate_url}" "${gate_ref}" || fail "cannot fetch ${gate_repo}@${gate_ref}."
git -C "${gate_dir}" checkout -q FETCH_HEAD
scanned=$(git -C "${gate_dir}" rev-parse HEAD)
if [[ -n "${gate_sha:-}" && "${scanned}" != "${gate_sha}" ]]; then
  echo "warning: GATE_REF=${GATE_REF} is ${scanned}, not the Gate ruleset's ${gate_sha}: unset GATE_REF to scan as the Gate does." >&2
fi
if [[ ! -f "${gate_dir}/scripts/zizmor/scan.sh" ]]; then
  fail "giskard-actions@${gate_ref} has no scripts/zizmor/scan.sh: set GATE_REF to a ref that has it."
fi

if [[ -z "${ZIZMOR_RUNNER:-}" ]]; then
  ZIZMOR_RUNNER=uvx
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ZIZMOR_RUNNER=docker
  fi
fi
export ZIZMOR_RUNNER
bash "${gate_dir}/scripts/zizmor/scan.sh" "${clone}"
