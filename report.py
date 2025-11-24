#!/usr/bin/env python3
"""Generate availability reports and send them to Telegram recipients."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd
from matplotlib import pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
from zoneinfo import ZoneInfo

import requests

CONFIG_PATH = Path("config.json")
TARGETS_PATH = Path("targets.csv")
DEFAULT_LOG_PATH = Path("ip_monitor_log.csv")
DEFAULT_REPORTS_DIR = Path("reports")


@dataclass
class Target:
    host: str
    name: str
    sla_target: float


@dataclass
class Recipient:
    chat_id: str
    role: str
    resources: str | List[str]
    timezone: str
    reports: Dict[str, dict]

    def can_access(self, target: Target) -> bool:
        if self.resources == "*":
            return True
        return target.host in self.resources or target.name in self.resources


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.api_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, chat_id: str, text: str) -> None:
        requests.post(f"{self.api_url}/sendMessage", data={"chat_id": chat_id, "text": text}, timeout=15)

    def send_document(self, chat_id: str, file_path: Path, caption: str = "") -> None:
        files = {"document": file_path.open("rb")}
        try:
            requests.post(
                f"{self.api_url}/sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files=files,
                timeout=60,
            )
        finally:
            files["document"].close()


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_targets(path: Path = TARGETS_PATH) -> List[Target]:
    targets: List[Target] = []
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for row in reader:
            targets.append(Target(host=row["IP"].strip(), name=row["Name"].strip(), sla_target=float(row["SLA_Target"])))
    return targets


def load_recipients(config: dict) -> List[Recipient]:
    recipients = []
    for rec in config.get("recipients", []):
        recipients.append(
            Recipient(
                chat_id=str(rec.get("chat_id")),
                role=rec.get("role", "user"),
                resources=rec.get("resources", "*"),
                timezone=rec.get("timezone", "UTC"),
                reports=rec.get("reports", {}),
            )
        )
    return recipients


def period_range(period: str, tz: ZoneInfo) -> Tuple[dt.datetime, dt.datetime]:
    now = dt.datetime.now(tz)
    if period == "weekly":
        start = now - dt.timedelta(days=7)
    elif period == "monthly":
        start = now - dt.timedelta(days=30)
    else:
        start = now - dt.timedelta(days=1)
    return start, now


def filter_logs(log_path: Path, start: dt.datetime, end: dt.datetime, tz: ZoneInfo) -> pd.DataFrame:
    if not log_path.exists():
        return pd.DataFrame(columns=["timestamp", "host", "name", "status", "rtt"])
    df = pd.read_csv(log_path, names=["timestamp", "host", "name", "status", "rtt"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(tz)
    mask = (df["timestamp"] >= start) & (df["timestamp"] <= end)
    return df.loc[mask]


def build_summary(df: pd.DataFrame, targets: Iterable[Target]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            {
                "host": [t.host for t in targets],
                "name": [t.name for t in targets],
                "availability": [0.0 for _ in targets],
                "samples": [0 for _ in targets],
            }
        )
    grouped = df.groupby(["host", "name"])
    rows = []
    for (host, name), group in grouped:
        samples = len(group)
        ok = (group["status"] == "ok").sum()
        availability = (ok / samples) * 100 if samples else 0.0
        rows.append({"host": host, "name": name, "availability": availability, "samples": samples})
    return pd.DataFrame(rows)


def save_csv(summary: pd.DataFrame, path: Path) -> None:
    summary.to_csv(path, index=False)


def save_chart(summary: pd.DataFrame, path: Path) -> None:
    plt.figure(figsize=(8, 4))
    plt.bar(summary["name"], summary["availability"], color=["#2c7be5" if v >= 99 else "#e55353" for v in summary["availability"]])
    plt.ylabel("Availability %")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def save_pdf(summary: pd.DataFrame, path: Path, period: str, tz: ZoneInfo) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph(f"IP Monitor Report ({period})", styles["Title"]), Spacer(1, 12)]
    elements.append(Paragraph(f"Timezone: {tz.key}", styles["Normal"]))
    elements.append(Spacer(1, 12))
    table_data = [["Name", "Host", "Availability %", "Samples"]]
    for _, row in summary.iterrows():
        table_data.append([row["name"], row["host"], f"{row['availability']:.2f}", int(row["samples"])])
    table = Table(table_data)
    elements.append(table)
    doc.build(elements)


def send_report(recipient: Recipient, period: str, summary: pd.DataFrame, report_dir: Path, bot: TelegramClient) -> None:
    ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    base = report_dir / f"report_{recipient.chat_id}_{period}_{ts}"
    csv_path = base.with_suffix(".csv")
    png_path = base.with_suffix(".png")
    pdf_path = base.with_suffix(".pdf")

    save_csv(summary, csv_path)
    save_chart(summary, png_path)
    save_pdf(summary, pdf_path, period, ZoneInfo(recipient.timezone))

    caption = f"Отчёт {period}. Записей: {len(summary)}"
    bot.send_message(recipient.chat_id, caption)
    for path in (csv_path, png_path, pdf_path):
        bot.send_document(recipient.chat_id, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate availability reports")
    parser.add_argument("--period", choices=["daily", "weekly", "monthly"], required=True)
    parser.add_argument("--chat-id", dest="chat_id", help="Send report only to this chat id")
    args = parser.parse_args()

    config = load_config()
    targets = load_targets()
    recipients = load_recipients(config)
    log_path = Path(config.get("log_csv", DEFAULT_LOG_PATH))
    reports_dir = Path(config.get("reports_dir", DEFAULT_REPORTS_DIR))
    reports_dir.mkdir(parents=True, exist_ok=True)

    bot = TelegramClient(config["telegram_token"])

    for recipient in recipients:
        settings = recipient.reports.get(args.period, {"enabled": False})
        if not settings.get("enabled", False):
            continue
        if args.chat_id and recipient.chat_id != args.chat_id:
            continue
        tz = ZoneInfo(recipient.timezone)
        start, end = period_range(args.period, tz)
        df = filter_logs(log_path, start, end, tz)
        accessible_targets = [t for t in targets if recipient.can_access(t)]
        if accessible_targets:
            df = df[df["host"].isin([t.host for t in accessible_targets])]
        summary = build_summary(df, accessible_targets)
        send_report(recipient, args.period, summary, reports_dir, bot)


if __name__ == "__main__":
    main()
