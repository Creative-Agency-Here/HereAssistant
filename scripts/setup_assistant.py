"""Настройка RU-сервера (185.246.220.120) под фронт ассистента assistant.hereagency.ru.

Зеркало Sites/HereAgency/scripts/setup_admin.py, адаптировано:
- статика SPA (try_files → index.html), БЕЗ /api-прокси (фронт ходит на api-assistant.hereagency.ru напрямую, CORS в API на DE-1);
- кэш для /_nuxt/ (Nuxt) и /assets/ (Vite — пригодится после редизайна);
- SSL через certbot --nginx.

Запуск с этой Windows-машины (ssh/scp по ключу id_ed25519):  python scripts/setup_assistant.py
"""

import os
import subprocess
import sys

HOST = "185.246.220.120"
USER = "root"
DOMAIN = "assistant.hereagency.ru"
ROOT_PATH = f"/var/www/{DOMAIN}"
EMAIL = "creativeagencyhere@gmail.com"

NGINX_CONFIG = f"""
server {{
    listen 80;
    server_name {DOMAIN};
    root {ROOT_PATH};
    index index.html;

    client_max_body_size 50M;

    # SPA Routing: всё на index.html
    location / {{
        try_files $uri $uri/ /index.html;
    }}

    # Иммутабельные ассеты сборки
    location /_nuxt/ {{
        expires 1y;
        add_header Cache-Control "public, immutable";
    }}
    location /assets/ {{
        expires 1y;
        add_header Cache-Control "public, immutable";
    }}

    access_log /var/log/nginx/{DOMAIN}.access.log;
    error_log  /var/log/nginx/{DOMAIN}.error.log;
}}
"""


def remote_exec(command):
    print(f"🔧 SSH: {command}")
    try:
        subprocess.run(["ssh", f"{USER}@{HOST}", command], check=True)
    except subprocess.CalledProcessError:
        print("❌ Ошибка выполнения команды на сервере.")
        sys.exit(1)


def main():
    print(f"🚀 Настройка RU под {DOMAIN}...")

    print(f"\n📂 1. Директория {ROOT_PATH}")
    remote_exec(f"mkdir -p {ROOT_PATH} && chown -R www-data:www-data {ROOT_PATH}")

    print("\n⚙️ 2. Конфиг Nginx")
    with open("nginx_temp.conf", "w", encoding="utf-8") as f:
        f.write(NGINX_CONFIG)
    try:
        subprocess.run(
            ["scp", "nginx_temp.conf",
             f"{USER}@{HOST}:/etc/nginx/sites-available/{DOMAIN}"],
            check=True,
        )
        print("   ✅ Конфиг загружен.")
    except Exception as e:
        print(f"❌ Ошибка scp: {e}")
        sys.exit(1)
    finally:
        if os.path.exists("nginx_temp.conf"):
            os.remove("nginx_temp.conf")

    print("\n🔗 3. Активация (symlink)")
    remote_exec(f"ln -sf /etc/nginx/sites-available/{DOMAIN} /etc/nginx/sites-enabled/")

    print("\n🔄 4. nginx -t + reload")
    remote_exec("nginx -t && systemctl reload nginx")

    print("\n🔒 5. SSL (certbot --nginx)")
    print("   ⚠️ DNS A-запись assistant.hereagency.ru должна указывать на этот сервер!")
    remote_exec(
        f"certbot --nginx -d {DOMAIN} --non-interactive --agree-tos -m {EMAIL} --redirect"
    )

    print("\n✨ Готово. Серт установлен. Теперь заливай фронт в", ROOT_PATH)


if __name__ == "__main__":
    main()
