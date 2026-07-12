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
install -o root -g root -m 0755 \
  "$repo_root/runner/git_vault_service.py" \
  /usr/local/libexec/hereassistant-git-vault
install -d -o root -g root -m 0755 /etc/systemd/system
install -o root -g root -m 0644 \
  "$repo_root/systemd/hereassistant-git-vault@.service" \
  /etc/systemd/system/hereassistant-git-vault@.service

echo "installed /usr/local/libexec/hereassistant-runner"
echo "installed /usr/local/libexec/hereassistant-git-credential"
echo "installed /usr/local/libexec/hereassistant-git-vault and systemd unit"
echo "next: create root-owned runner JSON and a minimal sudoers rule; see docs/os-runners.md"
