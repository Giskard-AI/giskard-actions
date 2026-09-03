#!/usr/bin/env bash
set -euo pipefail
# Resolve the unpacked action repo root from this file:
# .github/actions/_lib/run-assess.sh -> ../../../
ACTION_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export ACTION_ROOT
exec python3 "${ACTION_ROOT}/scripts/ci/codex_pr_assessment.py" "$@"
