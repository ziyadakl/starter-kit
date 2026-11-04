#!/usr/bin/env bash
# Package-manager helpers of the typescript layer, shared by scripts/check.d/20-typescript.sh and the CI install step.
#   bash scripts/ts-pm.sh detect    prints pnpm, npm, yarn or bun, from the lockfile in the current folder
#   bash scripts/ts-pm.sh install   installs the dependencies from the lockfile and refuses to change it
#   source scripts/ts-pm.sh         defines ts_detect_pm, ts_install_cmd, ts_install and ts_oneoff
# With two lockfiles the first in this order wins: pnpm-lock.yaml, package-lock.json, yarn.lock, bun.lock.

ts_detect_pm() {
  if [ -f pnpm-lock.yaml ]; then
    echo pnpm
  elif [ -f package-lock.json ]; then
    echo npm
  elif [ -f yarn.lock ]; then
    echo yarn
  elif [ -f bun.lock ] || [ -f bun.lockb ]; then
    echo bun
  else
    echo "no lockfile (pnpm-lock.yaml, package-lock.json, yarn.lock or bun.lock) in $PWD: commit one, the check and CI install from it" >&2
    return 1
  fi
}

# Yarn 2 and newer writes a __metadata block at the top of yarn.lock; Yarn 1 does not.
ts_yarn_is_berry() {
  grep -q '^__metadata:' yarn.lock 2>/dev/null
}

# Sets TS_INSTALL_CMD to the command that installs from the lockfile without changing it.
ts_install_cmd() {
  case "$1" in
    pnpm) TS_INSTALL_CMD=(pnpm install --frozen-lockfile) ;;
    npm) TS_INSTALL_CMD=(npm ci) ;;
    yarn)
      if ts_yarn_is_berry; then
        TS_INSTALL_CMD=(yarn install --immutable)
      else
        TS_INSTALL_CMD=(yarn install --frozen-lockfile)
      fi
      ;;
    bun) TS_INSTALL_CMD=(bun install --frozen-lockfile) ;;
    *) echo "unknown package manager: $1" >&2; return 1 ;;
  esac
}

ts_install() {
  ts_install_cmd "$1" || return 1
  "${TS_INSTALL_CMD[@]}"
}

# ts_oneoff <pm> "<pkg@version> [<pkg@version> ...]" <binary> [args...]
# Runs a binary from pinned packages without touching package.json, through the package manager's own one-off runner.
# Yarn 1 has none and bun's runner takes one package, so those use npx (Node is always there for this layer).
ts_oneoff() {
  local pm=$1 pkgs=$2 bin=$3
  shift 3
  local -a flags=() pnpm_flags=()
  local p
  for p in $pkgs; do
    flags+=(-p "$p")
    pnpm_flags+=("--package=$p")
  done
  case "$pm" in
    pnpm) pnpm "${pnpm_flags[@]}" dlx "$bin" "$@" ;;
    yarn)
      if ts_yarn_is_berry; then
        yarn dlx "${flags[@]}" "$bin" "$@"
      else
        npx -y "${flags[@]}" "$bin" "$@"
      fi
      ;;
    bun)
      if [ "${#flags[@]}" -eq 2 ]; then
        bunx "${flags[@]}" "$bin" "$@"
      else
        npx -y "${flags[@]}" "$bin" "$@"
      fi
      ;;
    *) npx -y "${flags[@]}" "$bin" "$@" ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  case "${1:-}" in
    detect) ts_detect_pm ;;
    install) ts_install "$(ts_detect_pm)" ;;
    *) echo "usage: bash scripts/ts-pm.sh detect|install" >&2; exit 2 ;;
  esac
fi
