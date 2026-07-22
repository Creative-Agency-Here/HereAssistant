#!/bin/bash
# Публикация HereAssistant VS Code расширения в Open VSX.
# Токен хранится в macOS Keychain (шифрован, не в репо).
#
# Использование:
#   bash scripts/publish-openvsx.sh              # опубликовать текущий VSIX
#   bash scripts/publish-openvsx.sh --set-token  # сохранить/обновить токен
#   bash scripts/publish-openvsx.sh --show-token # показать токен (для GitHub Secrets)

set -euo pipefail

KEYCHAIN_SERVICE="hereassistant-openvsx"
KEYCHAIN_ACCOUNT="publish-token"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VSIX_DIR="$ROOT/dist"

# --- Чтение токена из Keychain ---
get_token() {
  security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" -w 2>/dev/null || true
}

# --- Сохранение токена в Keychain ---
set_token() {
  local token="$1"
  # Удаляем старый если есть
  security delete-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" 2>/dev/null || true
  # Сохраняем новый
  security add-generic-password -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" -w "$token"
  echo "✓ Токен сохранён в macOS Keychain (service=$KEYCHAIN_SERVICE)"
}

# --- Публикация ---
publish() {
  local token
  token=$(get_token)
  if [ -z "$token" ]; then
    echo "✗ Токен не найден в Keychain. Запусти: $0 --set-token"
    exit 1
  fi

  # Находим последний VSIX
  local vsix
  vsix=$(ls -t "$VSIX_DIR"/hereassistant-vscode-*.vsix 2>/dev/null | head -1)
  if [ -z "$vsix" ]; then
    echo "✗ VSIX не найден в $VSIX_DIR. Собери: python3 scripts/package_vscode_extension.py"
    exit 1
  fi

  echo "📦 Публикую: $(basename "$vsix")"

  # Публикуем через npx (без глобальной установки)
  npx --yes ovsx publish "$vsix" -p "$token"
  echo ""
  echo "✅ Опубликовано на https://open-vsx.org/extension/creative-agency-here/hereassistant-vscode"
}

# --- Показать токен (для копирования в GitHub Secrets) ---
show_token() {
  local token
  token=$(get_token)
  if [ -z "$token" ]; then
    echo "✗ Токен не найден. Запусти: $0 --set-token"
    exit 1
  fi
  echo "$token"
}

# --- Main ---
case "${1:-}" in
  --set-token)
    echo -n "Вставь Open VSX токен (не отобразится): "
    read -rs token
    echo ""
    if [ -z "$token" ]; then
      echo "✗ Пустой токен"
      exit 1
    fi
    set_token "$token"
    echo ""
    echo "Для GitHub Secrets (CI/CD):"
    echo "  1. GitHub → Settings → Secrets and variables → Actions"
    echo "  2. New repository secret → Name: OPEN_VSX_TOKEN"
    echo "  3. Value: $(get_token | head -c 8)..."
    echo "  Или запусти: $0 --show-token | pbcopy  (скопирует в clipboard)"
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