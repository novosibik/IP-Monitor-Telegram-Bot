#!/usr/bin/env bash
set -euo pipefail

# Installation script for IP Monitor Telegram Bot
# Tested on Ubuntu 24.04+ and Ubuntu 25.10

if [[ ${EUID:-0} -ne 0 ]]; then
  echo "⚠️  Запустите скрипт через sudo или от root (нужно для установки пакетов и systemd)."
  exit 1
fi

APP_USER=${SUDO_USER:-$(whoami)}
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  echo "❌ Пользователь $APP_USER не существует. Передайте нужного пользователя через sudo."
  exit 1
fi

APP_HOME=$(getent passwd "$APP_USER" | cut -d: -f6)
APP_DIR="$APP_HOME/ip-monitor"
REPO_DIR=$(pwd)

mkdir -p "$APP_DIR"

# Устанавливаем системные зависимости (обновлено для Ubuntu 25.10)
DEBIAN_FRONTEND=noninteractive apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip \
  iputils-ping cron tzdata rsync \
  fonts-dejavu-core libcairo2-dev libfreetype6-dev pkg-config

# Копируем файлы в домашнюю директорию пользователя, не затирая конфиги/логи при переустановке
RSYNC_EXCLUDES=(
  --exclude '.git'
  --exclude 'reports/'
  --exclude 'ip_monitor_log.csv'
  --exclude 'ip_monitor.prom'
)
rsync -a "${RSYNC_EXCLUDES[@]}" "$REPO_DIR"/ "$APP_DIR"/

# Создаём дефолтные конфиги только при первом запуске
for cfg in config.json targets.csv; do
  if [[ ! -f "$APP_DIR/$cfg" ]]; then
    cp "$REPO_DIR/$cfg" "$APP_DIR/$cfg"
  fi
done

mkdir -p "$APP_DIR/reports"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# Настраиваем виртуальное окружение
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && python3 -m venv .venv"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/pip install --upgrade pip"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/pip install -r requirements.txt"

# Создаём unit с учётом виртуального окружения
cat <<SERVICE >/etc/systemd/system/ip-monitor@.service
[Unit]
Description=IP Monitor (Telegram)
After=network-online.target
Wants=network-online.target

[Service]
User=%i
WorkingDirectory=%h/ip-monitor
Environment=PYTHONUNBUFFERED=1
Environment="PATH=%h/ip-monitor/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
ExecStart=%h/ip-monitor/.venv/bin/python %h/ip-monitor/monitor.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now "ip-monitor@${APP_USER}.service"

cat <<MSG
✅ Установка завершена.
Код:        $APP_DIR
Служба:     ip-monitor@${APP_USER}.service (systemd)
Проверьте:  systemctl status ip-monitor@${APP_USER}
MSG
