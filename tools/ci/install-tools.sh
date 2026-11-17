#!/usr/bin/env bash
# Install pinned CI tools, verified by SHA-256 (no curl | sh). Linux x86_64 runners.
#   tools/ci/install-tools.sh conftest gitleaks
set -euo pipefail
BIN=${BIN:-$HOME/.local/bin}
mkdir -p "$BIN"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fetch() { # url sha256 file
  curl -fsSL --retry 3 -o "$tmp/$3" "$1"
  echo "$2  $tmp/$3" | sha256sum -c --quiet -
}

for tool in "$@"; do
  case "$tool" in
    conftest)
      fetch https://github.com/open-policy-agent/conftest/releases/download/v0.71.1/conftest_0.71.1_Linux_x86_64.tar.gz \
        c3f6b2a753bd56e377a1e51a95ae3e057f926f2e9139f7d5b5dfbbaf810f7985 conftest.tgz
      tar -xzf "$tmp/conftest.tgz" -C "$BIN" conftest ;;
    gitleaks)
      fetch https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
        551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb gitleaks.tgz
      tar -xzf "$tmp/gitleaks.tgz" -C "$BIN" gitleaks ;;
    *) echo "unknown tool $tool" >&2; exit 2 ;;
  esac
done
echo "$BIN" >> "${GITHUB_PATH:-/dev/null}"
