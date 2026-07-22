#!/bin/bash
# Публикация HereAssistant VS Code расширения в VS Code Marketplace.
# Токен хранится в macOS Keychain (шифрован, не в репо).
#
# Использование:
#   bash scripts/publish-vsce.sh              # опубликовать текущий VSIX
#   bash scripts/publish-vsce.sh --set-token  # сохранить/обновить токен
#   bash scripts/publish-vsce.sh --show-token # показать токен (для GitHub Secrets)

set -euo pipefail

KEYCHAIN_SERVICE="hereassistant-vsce"
KEYCHAIN_ACCOUNT="publish-token"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VSIX_DIR="$ROOT/dist"

get_token() {
  security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" -w 2>/dev/null || true
}

set_token() {
  local token="$1"
  security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" 2>/dev/null || true
  security add-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" -w "$token"
  echo "✓ Токен сохранён в macOS Keychain (service=$KEYCHAIN_SERVICE)"
}

publish() {
  local token
  token=$(get_token)
  if [ -z "$token" ]; then
    echo "✗ Токен не найден в Keychain. Запусти: $0 --set-token"
    exit 1
  fi

  local vsix
  vsix=$(ls -t "$VSIX_DIR"/hereassistant-vscode-*.vsix 2>/dev/null | head -1)
  if [ -z "$vsix" ]; then
    echo "✗ VSIX не найден в $VSIX_DIR. Собери: python3 scripts/package_vscode_extension.py"
    exit 1
  fi

  echo "📦 Публикую: $(basename "$vsix")"

  npx --yes @vscode/vsce publish --packagePath "$vsix" -p "$token"
  echo ""
  echo "✅ Опубликовано на https://marketplace.visualstudio.com/items?itemName=creative-agency-here.hereassistant-vscode"
}

show_token() {
  local token
  token=$(get_token)
  if [ -z "$token" ]; then
    echo "✗ Токен не найден. Запусти: $0 --set-token"
    exit 1
  fi
  echo "$token"
}

case "${1:-}" in
  --set-token)
    echo -n "Вставь VS Code Marketplace PAT (не отобразится): "
    read -rs token
    echo ""
    if [ -z "$token" ]; then
      echo "✗ Пустой токен"
      exit 1
    fi
    set_token "$token"
    echo ""
    echo "Для GitHub Secrets (CI/CD):"
    echo "  1. GitHub → Settings → Secrets → Actions → New secret"
    echo "  2. Name: VSCE_TOKEN"
    echo "  3. Value: $(get_token | head -c 8)..."
    echo "  Или: $0 --show-token | pbcopy"
    ;;
  --show-token)
    show_token
    ;;
  --help|-h)
    echo "Использование:"
    echo "  $0              — опубликовать текущий VSIX"
    echo "  $0 --set-token  — сохранить токен в Keychain"
    echo "  $0 --show-token — показать токен (для GitHub Secrets)"
    ;;
  *)
    publish
    ;;
esac