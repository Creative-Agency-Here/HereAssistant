#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

repo_root=$(cd "$(dirname "$0")/.." && pwd)
install -d -o root -g root -m 0755 /usr/local/libexec /etc/hereassistant/runners
install -o root -g root -m 0755 \
  "$repo_root/runner/entrypoint.py" \
  /usr/local/libexec/hereassistant-runner
install -o root -g root -m 0755 \
  "$repo_root/runner/git_credential_proxy.py" \
  /usr/local/libexec/hereassistant-git-credential

echo "installed /usr/local/libexec/hereassistant-runner"
echo "installed /usr/local/libexec/hereassistant-git-credential"
echo "next: create root-owned runner JSON and a minimal sudoers rule; see docs/os-runners.md"
