"""Деплой фронта WebApp на сервер HereAssistant (Ubuntu + nginx).

Сборка Nuxt → доставка статики в webroot. Основной путь — `rsync`; если его нет
в системе, используется фолбэк через `tar` + `scp`.

Адрес сервера в коде НЕ зашит: репозиторий публичный, и каждый self-hosted
инсталл разворачивает свой узел. Параметры берутся из окружения (удобно
держать их в локальном `.env`, см. `.env.example`):

    HEREASSISTANT_DEPLOY_HOST   — хост или SSH-алиас
    HEREASSISTANT_DEPLOY_USER   — пользователь SSH (не нужен, если задан
                                  в `~/.ssh/config` для алиаса)
    HEREASSISTANT_DEPLOY_PATH   — webroot на сервере, например
                                  `/var/www/assistant.example.com`
    HEREASSISTANT_DEPLOY_OWNER  — владелец файлов после доставки
                                  (по умолчанию `www-data:www-data`)
    NUXT_PUBLIC_API_BASE        — базовый URL API, с которым собирается фронт

Запуск:

    python scripts/deploy.py            # собрать и выложить
    python scripts/deploy.py --no-build # выложить уже собранное (.output/public)
    python scripts/deploy.py --dry-run  # показать команды, ничего не выполняя
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "webapp" / "front"
DIST = FRONT / ".output" / "public"
TAR = ROOT / ".runtime" / "assistant-dist.tar.gz"

HOST = os.environ.get("HEREASSISTANT_DEPLOY_HOST", "").strip()
USER = os.environ.get("HEREASSISTANT_DEPLOY_USER", "").strip()
REMOTE = os.environ.get("HEREASSISTANT_DEPLOY_PATH", "").strip()
OWNER = os.environ.get("HEREASSISTANT_DEPLOY_OWNER", "www-data:www-data").strip()
API_BASE = os.environ.get("NUXT_PUBLIC_API_BASE", "").strip()


def target() -> str:
    """``user@host`` либо просто хост/алиас, если пользователь задан в ssh-config."""
    return f"{USER}@{HOST}" if USER else HOST


def require_config() -> None:
    """Без явной конфигурации не деплоим: угадывать чужой сервер нельзя."""
    missing = [
        name
        for name, value in (
            ("HEREASSISTANT_DEPLOY_HOST", HOST),
            ("HEREASSISTANT_DEPLOY_PATH", REMOTE),
        )
        if not value
    ]
    if missing:
        print("❌ Не задано:", ", ".join(missing))
        print("   Укажи значения в окружении или локальном .env — см. шапку файла.")
        sys.exit(2)


def run(cmd, dry: bool = False, **kw) -> None:
    print("   🔧", " ".join(str(c) for c in cmd))
    if dry:
        return
    subprocess.run(cmd, check=True, **kw)


def build(dry: bool = False) -> None:
    if not API_BASE:
        print("❌ Не задан NUXT_PUBLIC_API_BASE — фронт собрался бы без адреса API.")
        sys.exit(2)
    print("\n📦 Сборка фронта (apiBase =", API_BASE + ")")
    env = {**os.environ, "NUXT_PUBLIC_API_BASE": API_BASE}
    run(["node", "node_modules/nuxt/bin/nuxt.mjs", "generate"], dry=dry, cwd=FRONT, env=env)


def deploy_rsync(dry: bool = False) -> None:
    """Основной путь: инкрементальная синхронизация с удалением лишнего."""
    print("\n📡 Синхронизация (rsync) →", f"{target()}:{REMOTE}")
    run(["rsync", "-az", "--delete", f"{DIST}/", f"{target()}:{REMOTE}/"], dry=dry)
    run(["ssh", target(), f"chown -R {OWNER} {REMOTE}"], dry=dry)


def deploy_tar(dry: bool = False) -> None:
    """Фолбэк для систем без rsync: архив и распаковка на сервере."""
    print("\n🗜 Упаковка", DIST)
    TAR.parent.mkdir(parents=True, exist_ok=True)
    run(["tar", "-czf", str(TAR), "-C", str(DIST), "."], dry=dry)
    print("\n📡 Заливка (scp) →", target())
    run(["scp", str(TAR), f"{target()}:/tmp/assistant-dist.tar.gz"], dry=dry)
    remote = (
        f"find {REMOTE} -mindepth 1 -delete && "
        f"tar -xzf /tmp/assistant-dist.tar.gz -C {REMOTE} && "
        f"chown -R {OWNER} {REMOTE} && "
        f"rm -f /tmp/assistant-dist.tar.gz"
    )
    run(["ssh", target(), remote], dry=dry)
    if TAR.exists() and not dry:
        TAR.unlink()


def deploy(dry: bool = False) -> None:
    if not DIST.exists():
        print("❌ Нет сборки:", DIST, "— запусти без --no-build")
        sys.exit(1)
    if shutil.which("rsync"):
        deploy_rsync(dry=dry)
    else:
        deploy_tar(dry=dry)
    print("\n✨ Готово. Webroot обновлён:", f"{target()}:{REMOTE}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true", help="не собирать, выложить .output/public")
    ap.add_argument("--dry-run", action="store_true", help="показать команды, ничего не выполняя")
    args = ap.parse_args()
    require_config()
    if not args.no_build:
        build(dry=args.dry_run)
    deploy(dry=args.dry_run)


if __name__ == "__main__":
    main()
