 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/install_monitor.sh b/install_monitor.sh
old mode 100644
new mode 100755
index 8cb69a9d9986f90fa63ae22a6337d9f3b0570623..b81fbf9cc6b033292d6da8d04a8271a7d39bae10
--- a/install_monitor.sh
+++ b/install_monitor.sh
@@ -1 +1,68 @@
-<... слишком длинный, опущен ...>
\ No newline at end of file
+#!/usr/bin/env bash
+set -euo pipefail
+
+# Installation script for IP Monitor Telegram Bot
+# Tested on Ubuntu 24.04+ and Ubuntu 25.10
+
+if [[ ${EUID:-0} -ne 0 ]]; then
+  echo "⚠️  Запустите скрипт через sudo или от root (нужно для установки пакетов и systemd)."
+  exit 1
+fi
+
+APP_USER=${SUDO_USER:-$(whoami)}
+if ! id -u "$APP_USER" >/dev/null 2>&1; then
+  echo "❌ Пользователь $APP_USER не существует. Передайте нужного пользователя через sudo."
+  exit 1
+fi
+
+APP_HOME=$(getent passwd "$APP_USER" | cut -d: -f6)
+APP_DIR="$APP_HOME/ip-monitor"
+REPO_DIR=$(pwd)
+
+mkdir -p "$APP_DIR"
+
+# Устанавливаем системные зависимости (обновлено для Ubuntu 25.10)
+DEBIAN_FRONTEND=noninteractive apt-get update
+DEBIAN_FRONTEND=noninteractive apt-get install -y \
+  python3 python3-venv python3-pip \
+  iputils-ping cron tzdata rsync \
+  fonts-dejavu-core libcairo2-dev libfreetype6-dev pkg-config
+
+# Копируем файлы в домашнюю директорию пользователя
+rsync -a --delete --exclude '.git' "$REPO_DIR"/ "$APP_DIR"/
+chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
+
+# Настраиваем виртуальное окружение
+sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && python3 -m venv .venv"
+sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/pip install --upgrade pip"
+sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/pip install -r requirements.txt"
+
+# Создаём unit с учётом виртуального окружения
+cat <<SERVICE >/etc/systemd/system/ip-monitor@.service
+[Unit]
+Description=IP Monitor (Telegram)
+After=network-online.target
+Wants=network-online.target
+
+[Service]
+User=%i
+WorkingDirectory=%h/ip-monitor
+Environment=PYTHONUNBUFFERED=1
+Environment="PATH=%h/ip-monitor/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
+ExecStart=%h/ip-monitor/.venv/bin/python %h/ip-monitor/monitor.py
+Restart=always
+RestartSec=5
+
+[Install]
+WantedBy=multi-user.target
+SERVICE
+
+systemctl daemon-reload
+systemctl enable --now "ip-monitor@${APP_USER}.service"
+
+cat <<MSG
+✅ Установка завершена.
+Код:        $APP_DIR
+Служба:     ip-monitor@${APP_USER}.service (systemd)
+Проверьте:  systemctl status ip-monitor@${APP_USER}
+MSG
 
EOF
)
