 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/monitor.py b/monitor.py
index 8cb69a9d9986f90fa63ae22a6337d9f3b0570623..54fbb4f70a4d159851d0f34b694e73e289e671cb 100644
--- a/monitor.py
+++ b/monitor.py
@@ -1 +1,381 @@
-<... слишком длинный, опущен ...>
\ No newline at end of file
+#!/usr/bin/env python3
+"""IP availability monitor with Telegram notifications.
+
+This script periodically pings configured targets, tracks their state using
+anti-flap thresholds, emits Prometheus-style metrics, and sends alerts to
+Telegram recipients defined in ``config.json``. Users can query the bot with
+basic commands (``/whoami``, ``/status``) and admins may rebuild cron rules for
+scheduled reports using ``/rebuildcron``.
+"""
+from __future__ import annotations
+
+import argparse
+import csv
+import datetime as dt
+import json
+import logging
+import os
+import signal
+import subprocess
+import threading
+import time
+from dataclasses import dataclass, field
+from pathlib import Path
+from typing import Dict, Iterable, List, Optional, Tuple
+
+import requests
+
+CONFIG_PATH = Path("config.json")
+TARGETS_PATH = Path("targets.csv")
+DEFAULT_LOG_PATH = Path("ip_monitor_log.csv")
+DEFAULT_REPORTS_DIR = Path("reports")
+DEFAULT_PROM_PATH = Path("ip_monitor.prom")
+
+
+@dataclass
+class Target:
+    """Host to monitor."""
+
+    host: str
+    name: str
+    sla_target: float
+
+
+@dataclass
+class Recipient:
+    """Telegram recipient with access rules and report schedule."""
+
+    chat_id: str
+    role: str
+    resources: str | List[str]
+    timezone: str
+    reports: Dict[str, dict] = field(default_factory=dict)
+
+    def can_access(self, target: Target) -> bool:
+        if self.resources == "*":
+            return True
+        return target.host in self.resources or target.name in self.resources
+
+    def is_admin(self) -> bool:
+        return self.role.lower() == "admin"
+
+
+@dataclass
+class TargetState:
+    status: str = "unknown"  # unknown|ok|warn|down
+    fail_streak: int = 0
+    success_streak: int = 0
+    outage_started: Optional[dt.datetime] = None
+
+
+class TelegramClient:
+    def __init__(self, token: str) -> None:
+        self.token = token
+        self.api_url = f"https://api.telegram.org/bot{token}"
+
+    def send_message(self, chat_id: str, text: str) -> None:
+        payload = {"chat_id": chat_id, "text": text}
+        try:
+            resp = requests.post(f"{self.api_url}/sendMessage", data=payload, timeout=10)
+            resp.raise_for_status()
+        except Exception as exc:  # pragma: no cover - network best-effort
+            logging.error("Failed to send Telegram message: %s", exc)
+
+    def send_document(self, chat_id: str, file_path: Path, caption: str = "") -> None:
+        if not file_path.exists():
+            logging.warning("Attachment %s does not exist", file_path)
+            return
+        files = {"document": file_path.open("rb")}
+        data = {"chat_id": chat_id, "caption": caption}
+        try:
+            resp = requests.post(f"{self.api_url}/sendDocument", data=data, files=files, timeout=30)
+            resp.raise_for_status()
+        except Exception as exc:  # pragma: no cover - network best-effort
+            logging.error("Failed to send Telegram document: %s", exc)
+        finally:
+            files["document"].close()
+
+    def get_updates(self, offset: Optional[int] = None, timeout: int = 20) -> dict:
+        params = {"timeout": timeout}
+        if offset is not None:
+            params["offset"] = offset
+        resp = requests.get(f"{self.api_url}/getUpdates", params=params, timeout=timeout + 5)
+        resp.raise_for_status()
+        return resp.json()
+
+
+class Monitor:
+    def __init__(self, config_path: Path = CONFIG_PATH, targets_path: Path = TARGETS_PATH) -> None:
+        self.config = self._load_config(config_path)
+        self.targets = self._load_targets(targets_path)
+        self.states: Dict[str, TargetState] = {t.host: TargetState() for t in self.targets}
+        self.stop_event = threading.Event()
+        self.telegram = TelegramClient(self.config["telegram_token"])
+        self.log_path = Path(self.config.get("log_csv", DEFAULT_LOG_PATH))
+        self.reports_dir = Path(self.config.get("reports_dir", DEFAULT_REPORTS_DIR))
+        self.prom_path = Path(self.config.get("prom_metrics_path", DEFAULT_PROM_PATH))
+        self.warn_th = int(self.config.get("warn_threshold", 1))
+        self.fail_th = int(self.config.get("fail_threshold", 3))
+        self.success_th = int(self.config.get("success_threshold", 2))
+        self.check_interval = int(self.config.get("check_interval", 30))
+        self.recipients = self._load_recipients()
+        self._setup_logging()
+        self._ensure_dirs()
+
+    def _setup_logging(self) -> None:
+        logging.basicConfig(
+            level=logging.INFO,
+            format="%(asctime)s [%(levelname)s] %(message)s",
+            handlers=[logging.StreamHandler(), logging.FileHandler(self.log_path, encoding="utf-8")],
+        )
+
+    def _ensure_dirs(self) -> None:
+        self.reports_dir.mkdir(parents=True, exist_ok=True)
+        self.log_path.parent.mkdir(parents=True, exist_ok=True)
+        self.prom_path.parent.mkdir(parents=True, exist_ok=True)
+
+    def _load_config(self, path: Path) -> dict:
+        with path.open("r", encoding="utf-8") as fh:
+            return json.load(fh)
+
+    def _load_targets(self, path: Path) -> List[Target]:
+        targets: List[Target] = []
+        with path.open("r", encoding="utf-8") as fh:
+            reader = csv.DictReader(fh, delimiter=";")
+            for row in reader:
+                targets.append(Target(host=row["IP"].strip(), name=row["Name"].strip(), sla_target=float(row["SLA_Target"])))
+        return targets
+
+    def _load_recipients(self) -> List[Recipient]:
+        recipients = []
+        for rec in self.config.get("recipients", []):
+            resources = rec.get("resources", "*")
+            recipients.append(
+                Recipient(
+                    chat_id=str(rec.get("chat_id")),
+                    role=rec.get("role", "user"),
+                    resources=resources,
+                    timezone=rec.get("timezone", "UTC"),
+                    reports=rec.get("reports", {}),
+                )
+            )
+        return recipients
+
+    # ------------------- ping & state management -------------------
+    def ping(self, target: Target) -> Tuple[bool, Optional[float]]:
+        """Ping target host and return (success, rtt_ms)."""
+        try:
+            proc = subprocess.run(
+                ["ping", "-c", "1", "-W", "3", target.host],
+                capture_output=True,
+                text=True,
+                check=False,
+            )
+        except FileNotFoundError:
+            logging.error("ping command not found")
+            return False, None
+
+        success = proc.returncode == 0
+        rtt = None
+        if success:
+            for line in proc.stdout.splitlines():
+                if "time=" in line:
+                    try:
+                        rtt = float(line.split("time=")[1].split()[0])
+                    except ValueError:
+                        rtt = None
+                    break
+        return success, rtt
+
+    def _log_result(self, target: Target, status: str, rtt: Optional[float]) -> None:
+        with self.log_path.open("a", encoding="utf-8", newline="") as fh:
+            writer = csv.writer(fh)
+            writer.writerow([
+                dt.datetime.utcnow().isoformat(),
+                target.host,
+                target.name,
+                status,
+                f"{rtt:.2f}" if rtt is not None else "",
+            ])
+
+    def _write_metrics(self) -> None:
+        lines = []
+        for target in self.targets:
+            state = self.states[target.host]
+            up = 1 if state.status == "ok" else 0
+            lines.append(f'ip_up{{target="{target.host}",name="{target.name}"}} {up}')
+            lines.append(f'ip_success_streak{{target="{target.host}"}} {state.success_streak}')
+            lines.append(f'ip_fail_streak{{target="{target.host}"}} {state.fail_streak}')
+        self.prom_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
+
+    def _send_alert(self, target: Target, text: str) -> None:
+        for recipient in self.recipients:
+            if recipient.can_access(target):
+                self.telegram.send_message(recipient.chat_id, text)
+
+    def _format_duration(self, started: Optional[dt.datetime]) -> str:
+        if not started:
+            return "unknown"
+        delta = dt.datetime.utcnow() - started
+        return str(delta).split(".")[0]
+
+    def _update_state(self, target: Target, success: bool, rtt: Optional[float]) -> None:
+        state = self.states[target.host]
+        if success:
+            state.success_streak += 1
+            state.fail_streak = 0
+            if state.status in {"warn", "down"} and state.success_streak >= self.success_th:
+                duration = self._format_duration(state.outage_started)
+                text = f"✅ {target.name} ({target.host}) восстановлен. Длительность: {duration}."
+                self._send_alert(target, text)
+                state.status = "ok"
+                state.outage_started = None
+            elif state.status == "unknown" and state.success_streak >= self.success_th:
+                state.status = "ok"
+        else:
+            state.fail_streak += 1
+            state.success_streak = 0
+            if state.fail_streak >= self.fail_th and state.status != "down":
+                state.status = "down"
+                state.outage_started = dt.datetime.utcnow()
+                text = f"❌ {target.name} ({target.host}) недоступен"
+                self._send_alert(target, text)
+            elif state.fail_streak >= self.warn_th and state.status == "ok":
+                state.status = "warn"
+                text = f"⚠️ {target.name} ({target.host}) нестабилен"
+                self._send_alert(target, text)
+
+        self._log_result(target, state.status if success else "fail", rtt)
+        self._write_metrics()
+
+    # ------------------- Telegram handlers -------------------
+    def _handle_command(self, chat_id: str, text: str) -> None:
+        lower = text.strip().lower()
+        recipient = next((r for r in self.recipients if r.chat_id == chat_id), None)
+        if lower.startswith("/whoami"):
+            if recipient:
+                resources = "*" if recipient.resources == "*" else ", ".join(recipient.resources)
+                self.telegram.send_message(
+                    chat_id,
+                    f"role: {recipient.role}\nresources: {resources}\ntimezone: {recipient.timezone}",
+                )
+            else:
+                self.telegram.send_message(chat_id, "Вы не настроены в config.json")
+        elif lower.startswith("/status"):
+            lines = []
+            for t in self.targets:
+                if recipient and not recipient.can_access(t):
+                    continue
+                st = self.states[t.host]
+                lines.append(f"{t.name} ({t.host}): {st.status} (fail {st.fail_streak}, ok {st.success_streak})")
+            self.telegram.send_message(chat_id, "\n".join(lines) if lines else "Нет доступных ресурсов")
+        elif lower.startswith("/rebuildcron") and recipient and recipient.is_admin():
+            try:
+                self.rebuild_cron()
+                self.telegram.send_message(chat_id, "Cron обновлён")
+            except Exception as exc:
+                logging.exception("Failed to rebuild cron")
+                self.telegram.send_message(chat_id, f"Ошибка cron: {exc}")
+        else:
+            self.telegram.send_message(chat_id, "Неизвестная команда")
+
+    def _poll_updates(self) -> None:
+        offset = None
+        while not self.stop_event.is_set():
+            try:
+                payload = self.telegram.get_updates(offset=offset, timeout=10)
+            except Exception as exc:  # pragma: no cover - network best-effort
+                logging.warning("Telegram polling failed: %s", exc)
+                time.sleep(5)
+                continue
+            for update in payload.get("result", []):
+                offset = update["update_id"] + 1
+                message = update.get("message") or update.get("edited_message")
+                if not message:
+                    continue
+                chat_id = str(message["chat"]["id"])
+                text = message.get("text", "")
+                if text.startswith("/"):
+                    self._handle_command(chat_id, text)
+            time.sleep(1)
+
+    # ------------------- cron -------------------
+    def rebuild_cron(self) -> None:
+        """Generate cron entries for reports according to config."""
+        cron_lines = ["# BEGIN ip-monitor reports"]
+        script = Path(__file__).resolve().parent / "report.py"
+        python = "python3"
+        for recipient in self.recipients:
+            for period, opts in recipient.reports.items():
+                if not opts.get("enabled", False):
+                    continue
+                minute = int(opts.get("minute", 0))
+                hour = int(opts.get("hour", 9))
+                if period == "weekly":
+                    dow = int(opts.get("dow", 1))
+                    cron_time = f"{minute} {hour} * * {dow}"
+                elif period == "monthly":
+                    day = int(opts.get("day", 1))
+                    cron_time = f"{minute} {hour} {day} * *"
+                else:  # daily
+                    cron_time = f"{minute} {hour} * * *"
+                cron_lines.append(
+                    f"{cron_time} {python} {script} --period {period} --chat-id {recipient.chat_id}"
+                )
+        cron_lines.append("# END ip-monitor reports")
+        cron_content = "\n".join(cron_lines) + "\n"
+        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
+        current = proc.stdout if proc.returncode == 0 else ""
+        new = []
+        inside = False
+        for line in current.splitlines():
+            if line.strip() == "# BEGIN ip-monitor reports":
+                inside = True
+                continue
+            if line.strip() == "# END ip-monitor reports":
+                inside = False
+                continue
+            if not inside:
+                new.append(line)
+        new.append(cron_content)
+        subprocess.run(["crontab", "-"], input="\n".join(new), text=True, check=False)
+
+    # ------------------- run loop -------------------
+    def run(self) -> None:
+        logging.info("Starting monitor for %d targets", len(self.targets))
+        signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
+        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())
+
+        poller = threading.Thread(target=self._poll_updates, daemon=True)
+        poller.start()
+
+        while not self.stop_event.is_set():
+            loop_start = time.time()
+            for target in self.targets:
+                success, rtt = self.ping(target)
+                self._update_state(target, success, rtt)
+            elapsed = time.time() - loop_start
+            sleep_for = max(0, self.check_interval - elapsed)
+            time.sleep(sleep_for)
+        logging.info("Monitor stopped")
+
+
+def parse_args() -> argparse.Namespace:
+    parser = argparse.ArgumentParser(description="IP monitor with Telegram notifications")
+    parser.add_argument("--once", action="store_true", help="Run single check instead of loop")
+    return parser.parse_args()
+
+
+def main() -> None:
+    args = parse_args()
+    monitor = Monitor()
+    if args.once:
+        for target in monitor.targets:
+            success, rtt = monitor.ping(target)
+            monitor._update_state(target, success, rtt)
+    else:
+        monitor.run()
+
+
+if __name__ == "__main__":
+    main()
 
EOF
)
