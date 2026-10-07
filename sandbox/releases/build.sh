#!/usr/bin/env bash
# Builds the faulty "release" images that bad-deploy scenarios roll out.
# A release = the committed baseline source of a service + one patch from this
# directory, so the bug exists only in that image and in that diff.
#
#   build.sh          build every release image
#   build.sh --list   print the image names (used by `make load-sandbox`)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

#        service  version  patch
RELEASES=(
  "orders   1.4.0    orders-1.4.0.patch"
)

if [[ "${1:-}" == "--list" ]]; then
  for r in "${RELEASES[@]}"; do
    read -r svc ver _ <<<"$r"
    echo "relay/sandbox-$svc:$ver"
  done
  exit 0
fi

for r in "${RELEASES[@]}"; do
  read -r svc ver patch <<<"$r"
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  # Copy the baseline source outside the repo so `git apply` uses plain paths.
  mkdir -p "$work/sandbox/services"
  cp -r "$ROOT/sandbox/services/$svc" "$work/sandbox/services/$svc"
  rm -rf "$work/sandbox/services/$svc/target"
  (cd "$work" && git apply "$ROOT/sandbox/releases/$patch")
  echo "building relay/sandbox-$svc:$ver from $patch"
  docker build -q -t "relay/sandbox-$svc:$ver" "$work/sandbox/services/$svc"
  rm -rf "$work"
  trap - EXIT
done
