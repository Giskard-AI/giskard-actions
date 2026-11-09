#!/usr/bin/env bash
# Runs the Gate scan on a local clone, exactly as the org ruleset runs it.
#
# Usage: gate-scan.sh OWNER/REPO CLONE_DIR
# Environment:
#   GATE_REF       giskard-actions ref whose scan to run, to test an unreleased
#                  scan. Default: the SHA the org ruleset pins on the default
#                  branch, else the latest giskard-actions release.
#   ZIZMOR_RUNNER  docker (default when Docker runs) or uvx.
# Exit status: the scan's: 0 means the Gate passes.
set -euo pipefail

repo="${1:?usage: gate-scan.sh OWNER/REPO CLONE_DIR}"
clone=$(cd "${2:?usage: gate-scan.sh OWNER/REPO CLONE_DIR}" && pwd)

branch=$(gh api "repos/${repo}" --jq .default_branch)
gate_sha=$(gh api "repos/${repo}/rules/branches/${branch}?per_page=100" --jq '[.[]
  | select(.type == "workflows") | .parameters.workflows[]
  | select(.path == ".github/workflows/zizmor-scan.yml") | .sha][0] // empty' 2>/dev/null || true)
gate_ref="${GATE_REF:-${gate_sha:-$(gh api repos/Giskard-AI/giskard-actions/releases/latest --jq .tag_name)}}"
echo "Gate ruleset: ${gate_sha:-not active yet}; scanning with giskard-actions@${gate_ref}" >&2

gate_dir=$(mktemp -d)
trap 'rm -rf "${gate_dir}"' EXIT
git -C "${gate_dir}" init -q
git -C "${gate_dir}" fetch -q --depth 1 https://github.com/Giskard-AI/giskard-actions.git "${gate_ref}"
git -C "${gate_dir}" checkout -q FETCH_HEAD
if [[ ! -f "${gate_dir}/scripts/zizmor/scan.sh" ]]; then
  echo "giskard-actions@${gate_ref} has no scripts/zizmor/scan.sh: set GATE_REF to a ref that has it." >&2
  exit 2
fi

if [[ -z "${ZIZMOR_RUNNER:-}" ]]; then
  ZIZMOR_RUNNER=uvx
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ZIZMOR_RUNNER=docker
  fi
fi
export ZIZMOR_RUNNER
bash "${gate_dir}/scripts/zizmor/scan.sh" "${clone}"
