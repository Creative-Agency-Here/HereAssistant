"""Деплой фронта ассистента на RU (assistant.hereagency.ru).

Аналог Sites/HereAgency/scripts/deploy.py, но под эту Windows-машину (rsync нет):
сборка Nuxt → tar → scp → распаковка на RU. SSH по ключу id_ed25519.

  python scripts/deploy.py            # собрать и выложить
  python scripts/deploy.py --no-build # выложить уже собранное (.output/public)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "webapp" / "front"
DIST = FRONT / ".output" / "public"
TAR = ROOT / ".runtime" / "assistant-dist.tar.gz"

HOST = "185.246.220.120"
USER = "root"
REMOTE = "/var/www/assistant.hereagency.ru"
API_BASE = "https://api-assistant.hereagency.ru"   # API ассистента на DE-1


def run(cmd, **kw):
    print("   🔧", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def build():
    print("\n📦 Сборка фронта (apiBase =", API_BASE + ")")
    env = {**os.environ, "NUXT_PUBLIC_API_BASE": API_BASE}
    run(["node", "node_modules/nuxt/bin/nuxt.mjs", "generate"], cwd=FRONT, env=env)


def deploy():
    if not DIST.exists():
        print("❌ Нет сборки:", DIST, "— запусти без --no-build")
        sys.exit(1)
    print("\n🗜 Упаковка", DIST)
    run(["tar", "-czf", str(TAR), "-C", str(DIST), "."])
    print("\n📡 Заливка на RU")
    run(["scp", str(TAR), f"{USER}@{HOST}:/tmp/assistant-dist.tar.gz"])
    # очистить webroot, распаковать, права; rm временного архива
    remote = (
        f"find {REMOTE} -mindepth 1 -delete && "
        f"tar -xzf /tmp/assistant-dist.tar.gz -C {REMOTE} && "
        f"chown -R www-data:www-data {REMOTE} && "
        f"rm -f /tmp/assistant-dist.tar.gz"
    )
    run(["ssh", f"{USER}@{HOST}", remote])
    if TAR.exists():
        TAR.unlink()
    print("\n✨ Готово:", f"https://{ 'assistant.hereagency.ru' }")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true", help="не собирать, выложить .output/public")
    args = ap.parse_args()
    if not args.no_build:
        build()
    deploy()


if __name__ == "__main__":
    main()
