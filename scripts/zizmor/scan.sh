#!/usr/bin/env bash
# The zizmor scan of the organization Gate: the only place that sets the zizmor
# version and flags. The Gate workflow (zizmor-scan.yml), the gha-hardening
# skill and the repo-baseline dashboard all run this script, so they agree.
#
# Usage: scan.sh SRC [BASE]
#   SRC   directory to scan.
#   BASE  directory whose zizmor config applies (default: SRC). The Gate passes
#         the pull request's base commit, so a pull request cannot loosen its
#         own config.
# Environment:
#   ZIZMOR_FORMAT  output format (default: plain; the Gate uses github).
#   ZIZMOR_ONLINE  1 to run online audits as well (reads GH_TOKEN). The Gate
#                  runs offline, so an advisory or an API outage cannot block
#                  merges.
#   ZIZMOR_RUNNER  docker (default) or uvx, on a machine without Docker.
# Exit status: 0 when clean or when there is nothing to audit, 1 when an
# Actions file is a symlink, zizmor's own status otherwise. Messages go to
# stderr, so stdout carries only zizmor's report.
set -euo pipefail

ZIZMOR_IMAGE="ghcr.io/zizmorcore/zizmor:1.32.0@sha256:3014cc8f68f663935f69b3c3e0e41f5ce102b268d97db13db0f62208747f860d"
DOCS="https://giskard-ai.github.io/giskard-actions/ci-workflows.html#zizmor-failed-on-my-pr"

src=$(cd "${1:?usage: scan.sh SRC [BASE]}" && pwd)
base=$(cd "${2:-$1}" && pwd)
format="${ZIZMOR_FORMAT:-plain}"

# The runner follows symlinks when it loads a local action, but zizmor audits
# the link's target under its own name, or skips it when the target is outside
# the checkout.
mapfile -t links < <(cd "${src}" && find . -path ./.git -prune -o -type l \
  \( -name action.yml -o -name action.yaml -o -name .github -o -path '*/.github/*' \) -print)
for link in "${links[@]}"; do
  echo "::error file=${link#./},title=zizmor::${link#./} is a symlink. Commit the file itself: zizmor cannot audit what a symlinked action, workflow or .github file points to." >&2
done
if (( ${#links[@]} )); then
  exit 1
fi

config_dir=$(mktemp -d)
trap 'rm -rf "${config_dir}"' EXIT
echo "rules: {}" > "${config_dir}/zizmor.yml"
# zizmor's own discovery order; the first file found wins.
for candidate in .github/zizmor.yml .github/zizmor.yaml zizmor.yml zizmor.yaml; do
  if [[ -f "${base}/${candidate}" ]]; then
    cp "${base}/${candidate}" "${config_dir}/zizmor.yml"
    echo "Using ${candidate} from ${base}" >&2
    break
  fi
done

flags=(--persona=pedantic --format="${format}" --collect=all --strict-collection)
network=(--network none)
if [[ "${ZIZMOR_ONLINE:-0}" == 1 ]]; then
  network=(--env GH_TOKEN)
else
  flags+=(--offline)
fi

status=0
case "${ZIZMOR_RUNNER:-docker}" in
  docker)
    docker run --rm "${network[@]}" --read-only --tmpfs /root/.cache \
      --volume "${src}:/src:ro" \
      --volume "${config_dir}:/config:ro" \
      --workdir /src \
      "${ZIZMOR_IMAGE}" \
      "${flags[@]}" --config=/config/zizmor.yml . || status=$?
    ;;
  uvx)
    version="${ZIZMOR_IMAGE#*:}"
    (cd "${src}" && uvx "zizmor@${version%@*}" "${flags[@]}" --config="${config_dir}/zizmor.yml" .) || status=$?
    ;;
  *)
    echo "ZIZMOR_RUNNER must be docker or uvx" >&2
    exit 2
    ;;
esac

if [[ "${status}" -eq 3 ]]; then
  echo "::notice title=zizmor::No workflow, action, Dependabot or pre-commit files to audit." >&2
  exit 0
fi
if [[ "${status}" -ne 0 && "${ZIZMOR_ONLINE:-0}" != 1 ]]; then
  echo "::error title=zizmor failed::Fix the findings, or add '# zizmor: ignore[<rule>] -- <reason>' on the flagged line for your reviewers to check. See ${DOCS}" >&2
fi
exit "${status}"
