#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: sudo $0 [--require-credential] <git-unix-user>" >&2
  exit 2
}

require_credential=0
if [[ ${1:-} == "--require-credential" ]]; then
  require_credential=1
  shift
fi
[[ $# -eq 1 ]] || usage
unix_user=$1
[[ $unix_user =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || usage
[[ ${EUID} -eq 0 ]] || { echo "canary: run as root" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || { echo "canary: Linux is required" >&2; exit 1; }

secure_root_file() {
  local path=$1
  [[ -f $path && ! -L $path ]] || { echo "canary: missing $path" >&2; exit 1; }
  [[ $(stat -c '%u' "$path") == 0 ]] || { echo "canary: $path is not root-owned" >&2; exit 1; }
  local mode
  mode=$(stat -c '%a' "$path")
  (( (8#$mode & 8#022) == 0 )) || { echo "canary: $path is writable by group/others" >&2; exit 1; }
}

for executable in \
  /usr/local/libexec/hereassistant-runner \
  /usr/local/libexec/hereassistant-git-credential \
  /usr/local/libexec/hereassistant-git-vault \
  /usr/local/libexec/hereassistant-git-vault-admin; do
  secure_root_file "$executable"
  [[ -x $executable ]] || { echo "canary: $executable is not executable" >&2; exit 1; }
done

config_file="/etc/hereassistant/runners/${unix_user}.json"
unit_file=/etc/systemd/system/hereassistant-git-vault@.service
secure_root_file "$config_file"
secure_root_file "$unit_file"
getent passwd "$unix_user" >/dev/null || { echo "canary: Unix user is missing" >&2; exit 1; }

systemd_version=$(systemd-creds --version | awk 'NR==1 {print $2}')
[[ $systemd_version =~ ^[0-9]+$ && $systemd_version -ge 250 ]] || {
  echo "canary: systemd-creds 250+ is required" >&2
  exit 1
}
systemd-analyze verify "$unit_file" >/dev/null

PYTHONPATH=/usr/local/libexec python3 - "$unix_user" <<'PY'
import sqlite3
import sys

from runner.entrypoint import load_config

config = load_config(sys.argv[1])
if not config.git_broker or config.accounts:
    raise SystemExit("canary: config is not a dedicated Git broker")
if config.git_database is None or config.git_credential_helper is None:
    raise SystemExit("canary: database/credential helper is missing")
if config.git_vault_socket is None:
    raise SystemExit("canary: vault socket is missing")
with sqlite3.connect(f"file:{config.git_database}?mode=ro", uri=True) as database:
    integrity = database.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise SystemExit("canary: SQLite integrity check failed")
    tables = {
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
required = {"git_connections", "git_repository_grants", "git_auth_sessions"}
if not required <= tables:
    raise SystemExit("canary: Git schema is incomplete")
print(f"canary: config user_id={config.user_id}, hosts={len(config.git_allowed_hosts)}")
PY

credential_file="/etc/hereassistant/git-credentials/${unix_user}.json.cred"
if (( require_credential )); then
  secure_root_file "$credential_file"
  [[ $(stat -c '%a' "$credential_file") == 600 ]] || {
    echo "canary: encrypted credential must have mode 0600" >&2
    exit 1
  }
  systemd-creds decrypt --name=git-credentials.json "$credential_file" - \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); assert isinstance(value,dict)' \
    >/dev/null
fi

service="hereassistant-git-vault@${unix_user}.service"
state=$(systemctl is-active "$service" 2>/dev/null || true)
echo "canary: service=$state credential_check=$require_credential"
echo "canary: PASS (read-only; no service was started or restarted)"
