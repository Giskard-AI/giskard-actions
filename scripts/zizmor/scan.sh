#!/usr/bin/env bash
# The zizmor scan of the organization Gate: the only place that sets the zizmor
# version and flags. The Gate workflow (zizmor-scan.yml), the gha-hardening
# skill and the organization dashboard all run this script, so they agree.
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
# Actions file is a symlink, has a path annotations cannot carry, or differs
# from its committed bytes, 2 when zizmor
# did not run to the end (Docker, network, a file it cannot parse), 10 to 14
# for findings, by highest severity. Messages go to stderr, so stdout carries
# only zizmor's report.
set -euo pipefail

ZIZMOR_IMAGE="ghcr.io/zizmorcore/zizmor:1.32.0@sha256:3014cc8f68f663935f69b3c3e0e41f5ce102b268d97db13db0f62208747f860d"
DOCS="https://giskard-ai.github.io/giskard-actions/ci-workflows.html#zizmor-failed-on-my-pr"

src=$(cd "${1:?usage: scan.sh SRC [BASE]}" && pwd) || exit 2
base=$(cd "${2:-$1}" && pwd) || exit 2
format="${ZIZMOR_FORMAT:-plain}"

# Annotation property value: the runner splits properties on , and ::.
prop() { local v="${1//%/%25}"; v="${v//$'\r'/%0D}"; v="${v//$'\n'/%0A}"; v="${v//:/%3A}"; echo "${v//,/%2C}"; }

# The runner follows symlinks when it loads a local action, but zizmor audits
# the link's target under its own name, or skips it when the target is outside
# the checkout.
rejected=0
while IFS= read -r -d '' link; do
  link="${link#./}"
  echo "::error file=$(prop "${link}"),title=zizmor::$(prop "${link}") is a symlink. Commit the file itself: zizmor cannot audit what a symlinked action, workflow or .github file points to." >&2
  rejected=1
done < <(cd "${src}" && find . -path ./.git -prune -o -type l \
  \( -name '*.yml' -o -name '*.yaml' -o -name .github -o -path '*/.github/*' \) -print0)

# zizmor writes file paths into its annotations unescaped, so a path could
# forge annotations or workflow commands in the log.
while IFS= read -r -d '' path; do
  path="${path#./}"
  echo "::error title=zizmor::$(prop "${path}") contains a control character, a comma, a colon or %. Rename it: zizmor would print it unescaped in its annotations." >&2
  rejected=1
done < <(cd "${src}" && LC_ALL=C find . -path ./.git -prune -o \
  \( -name '*.yml' -o -name '*.yaml' \) -path '*[[:cntrl:],:%]*' -print0)

# GitHub runs the committed bytes; a .gitattributes filter, eol or
# working-tree-encoding would make zizmor read other bytes. Local runs scan
# uncommitted edits on purpose.
if [[ "${GITHUB_ACTIONS:-}" == true && -e "${src}/.git" ]]; then
  while IFS= read -r -d '' entry; do
    info="${entry%%$'\t'*}" path="${entry#*$'\t'}"
    read -r mode blob _ <<<"${info}"
    [[ "${mode}" == 120000 || ! -f "${src}/${path}" ]] && continue
    if [[ "$(git -C "${src}" hash-object --no-filters -- "${path}")" != "${blob}" ]]; then
      echo "::error file=$(prop "${path}"),title=zizmor::$(prop "${path}") differs from its committed bytes (a .gitattributes filter, eol or working-tree-encoding). Remove that attribute for this file: the Gate scans what GitHub runs." >&2
      rejected=1
    fi
  done < <(git -C "${src}" ls-files -s -z -- ':(glob)**/*.yml' ':(glob)**/*.yaml')
fi
if (( rejected )); then
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

flags=(--persona=auditor --format="${format}" --collect=all --strict-collection)
network=(--network none)
if [[ "${ZIZMOR_ONLINE:-0}" == 1 ]]; then
  network=(--env GH_TOKEN)
else
  flags+=(--offline)
fi

# zizmor's errors quote YAML values from the scanned files; in Actions they
# are printed with workflow commands off, so they cannot fake annotations,
# unless stderr is a terminal (an Actions step never has one).
errors="${config_dir}/stderr"
run_zizmor() {
  if [[ -t 2 ]]; then
    "$@"
  else
    "$@" 2>"${errors}"
  fi
}

cd "${src}"
status=0
case "${ZIZMOR_RUNNER:-docker}" in
  docker)
    run_zizmor docker run --rm "${network[@]}" --read-only --tmpfs /root/.cache \
      --volume "${src}:/src:ro" \
      --volume "${config_dir}:/config:ro" \
      --workdir /src \
      "${ZIZMOR_IMAGE}" \
      "${flags[@]}" --config=/config/zizmor.yml . || status=$?
    ;;
  uvx)
    version="${ZIZMOR_IMAGE#*:}"
    run_zizmor uvx "zizmor@${version%@*}" "${flags[@]}" --config="${config_dir}/zizmor.yml" . || status=$?
    ;;
  *)
    echo "ZIZMOR_RUNNER must be docker or uvx" >&2
    exit 2
    ;;
esac
if [[ -s "${errors}" ]]; then
  token="zizmor-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  { echo "::stop-commands::${token}"; cat "${errors}"; printf '\n::%s::\n' "${token}"; } >&2
fi

case "${status}" in
  0) ;;
  3)
    echo "::notice title=zizmor::No workflow, action, Dependabot or pre-commit files to audit." >&2
    ;;
  1[0-4])
    if [[ "${ZIZMOR_ONLINE:-0}" != 1 ]]; then
      echo "::error title=zizmor failed::Fix the findings, or add '# zizmor: ignore[<rule>] -- <reason>' on the flagged line for your reviewers to check. See ${DOCS}" >&2
    fi
    exit "${status}"
    ;;
  *)
    echo "::error title=zizmor did not run::zizmor stopped with status ${status} before reporting findings; its message is above (Docker, network, or a file it cannot parse). See ${DOCS}" >&2
    exit 2
    ;;
esac
