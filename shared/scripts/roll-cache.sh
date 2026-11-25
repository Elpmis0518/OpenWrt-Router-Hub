#!/usr/bin/env bash
# Roll GitHub Actions build cache generations for a single isolated device prefix.
set -euo pipefail

CACHE_PREFIX="${1:?Usage: roll-cache.sh <cache_prefix> <current_cache_key>}"
CURRENT_KEY="${2:?Usage: roll-cache.sh <cache_prefix> <current_cache_key>}"
REPO="${GH_REPO:-${GITHUB_REPOSITORY:-}}"
REF="${GITHUB_REF:-refs/heads/main}"

if [ -z "${REPO}" ]; then
  echo "ERROR: GH_REPO or GITHUB_REPOSITORY must be set." >&2
  exit 1
fi

CURRENT_FOUND=0
while IFS= read -r KEY; do
  if [ "${KEY}" = "${CURRENT_KEY}" ]; then
    CURRENT_FOUND=1
    break
  fi
done < <(gh cache list --repo "${REPO}" --key "${CURRENT_KEY}" --ref "${REF}" --limit 100 --json key --jq '.[].key')

if [ "${CURRENT_FOUND}" -ne 1 ]; then
  echo "Skip cache rollover: current cache '${CURRENT_KEY}' is not available yet."
  exit 0
fi

gh cache list --repo "${REPO}" --key "${CACHE_PREFIX}-" --ref "${REF}" --limit 100 --json id,key --jq '.[] | [.id, .key] | @tsv' |
  while IFS=$'\t' read -r CACHE_ID CACHE_KEY; do
    [ -n "${CACHE_ID}" ] || continue
    [ "${CACHE_KEY}" = "${CURRENT_KEY}" ] && continue
    echo "Deleting superseded cache ${CACHE_ID} (${CACHE_KEY})..."
    gh cache delete "${CACHE_ID}" --repo "${REPO}" ||
      echo "Warning: failed to delete cache ${CACHE_ID} (${CACHE_KEY})"
  done
