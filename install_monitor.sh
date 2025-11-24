 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/install_monitor.sh b/install_monitor.sh
old mode 100644
new mode 100755
index 8cb69a9d9986f90fa63ae22a6337d9f3b0570623..c24726033fd6df3726f2001cf1d28826a5575c51
--- a/install_monitor.sh
+++ b/install_monitor.sh
@@ -1 +1,40 @@
-<... слишком длинный, опущен ...>
\ No newline at end of file
+#!/usr/bin/env bash
+set -euo pipefail
+
+PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
+VENV_DIR="$PROJECT_DIR/.venv"
+REQUIREMENTS="$PROJECT_DIR/requirements.txt"
+SERVICE_NAME="ip-monitor"
+
+if ! command -v python3 >/dev/null; then
+  echo "python3 is required" >&2
+  exit 1
+fi
+
+python3 -m venv "$VENV_DIR"
+source "$VENV_DIR/bin/activate"
+pip install --upgrade pip
+pip install -r "$REQUIREMENTS"
+
+echo "Installing systemd service..."
+SERVICE_FILE="$PROJECT_DIR/ip-monitor.service"
+if [ -f "$SERVICE_FILE" ]; then
+  sudo cp "$SERVICE_FILE" /etc/systemd/system/$SERVICE_NAME.service
+  sudo systemctl daemon-reload
+  sudo systemctl enable $SERVICE_NAME.service
+  sudo systemctl restart $SERVICE_NAME.service
+else
+  echo "Service file not found; skipping systemd installation"
+fi
+
+if command -v crontab >/dev/null; then
+  echo "Building report cron rules..."
+  python3 - <<'PY'
+from monitor import Monitor
+m = Monitor()
+m.rebuild_cron()
+print("Cron rebuilt")
+PY
+fi
+
+echo "Done. Logs: $PROJECT_DIR/ip_monitor_log.csv"
 
EOF
)
