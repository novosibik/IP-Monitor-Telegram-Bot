# IP Monitor Telegram Bot

Монитор доступности IP/хостов с уведомлениями в Telegram, отчётами (CSV/PNG/PDF), ролями и правами доступа, интерактивными мастерами, персональными расписаниями и часовыми поясами пользователей, а также Prometheus-метриками.

> Работает на Linux (Ubuntu 24.04+). Пинг каждые `N` секунд, анти-флаппер, алерты «падение/восстановление», отчёты по расписанию (daily/weekly/monthly) *per-user* с учётом локального часового пояса.

---

## ✨ Возможности

* 🔄 **Проверка доступности**: ICMP `ping` с интервалом (по умолчанию 30 сек).
* 🧯 **Анти-флаппер**: предупреждение после `WARN_TH` подряд FAIL, «падение» после `CRIT_TH`, «восстановление» после `SUCC_TH`.
* 🔔 **Telegram-уведомления**: падение, восстановление (с временем начала/конца и общей длительностью), предупреждения.
* 👥 **Роли и права**:

  * `admin`/`user`;
  * доступ пользователей к отдельным ресурсам (по IP/Name);
  * выдача/отзыв прав по **номерам ресурсов**.
* 🗣 **Интерактивные мастера**: команды без аргументов → диалог (шаги, `/back`, `/cancel`, авто-таймаут).
* 📊 **Отчёты**: CSV/PNG/PDF, средний SLA, подсветка нарушений, индивидуальный **часовой пояс** пользователя, опционально учёт **рабочих часов**.
* 🕒 **Персональные расписания отчётов**: daily/weekly/monthly, включение/выключение для каждого пользователя отдельными командами.
* 🧭 **Часовые пояса per-user**: отчёты и cron в локальном TZ.
* 🔁 **/rebuildcron**: пересборка cron прямо из чата (admin).
* 📈 **Prometheus-метрики**: `ip_up`, streak-и успехов/неудач.
* 🗃 **Файлы конфигурации**: `config.json`, `targets.csv`.

---

## 📦 Структура репозитория

```
ip-monitor/
├─ monitor.py               # демон мониторинга и Telegram-бот
├─ report.py                # генератор отчётов (CSV/PNG/PDF) с TZ per-user
├─ install_monitor.sh       # установка зависимостей, systemd, cron
├─ ip-monitor.service       # пример systemd unit (обычно не нужен)
├─ requirements.txt         # Python-зависимости
├─ config.json              # конфиг (TOKEN, получатели, пороги, и т.д.)
├─ targets.csv              # список ресурсов: IP;Name;SLA_Target
└─ reports/                 # папка для отчётов
```

---

## 🚀 Быстрый старт

```bash
git clone https://github.com/<your-org>/ip-monitor.git
cd ip-monitor

# 1) Укажите Telegram BOT TOKEN (от @BotFather) в config.json
# 2) Заполните recipients/targets при необходимости

chmod +x install_monitor.sh
./install_monitor.sh
```

Проверка:

```bash
systemctl status ip-monitor
crontab -l         # увидите блок между "# BEGIN ip-monitor reports" ... "# END ..."
```

В чате с ботом:
`/whoami` → получите свой `chat_id`.
Админ может управлять пользователями, ресурсами и отчётами (см. ниже).

---

## ⚙️ Конфигурация

### `config.json` (пример)

```json
{
  "telegram_token": "PUT_YOUR_BOT_TOKEN_HERE",

  "recipients": [
    {
      "chat_id": "123456789",
      "role": "admin",
      "resources": "*",
      "timezone": "Asia/Almaty",
      "reports": {
        "daily":   {"enabled": true,  "hour": 9,  "minute": 0},
        "weekly":  {"enabled": true,  "dow": 1,   "hour": 9,  "minute": 5},
        "monthly": {"enabled": false, "day": 1,   "hour": 9,  "minute": 10}
      }
    },
    {
      "chat_id": "222222222",
      "role": "user",
      "resources": ["8.8.8.8", "Cloudflare DNS"],
      "timezone": "Europe/Berlin",
      "reports": {
        "daily":   {"enabled": true,  "hour": 10, "minute": 0},
        "weekly":  {"enabled": false, "dow": 1,   "hour": 9,  "minute": 0},
        "monthly": {"enabled": true,  "day": 1,   "hour": 11, "minute": 15}
      }
    }
  ],

  "check_interval": 30,
  "log_csv": "ip_monitor_log.csv",
  "reports_dir": "reports",

  "warn_threshold": 1,
  "fail_threshold": 3,
  "success_threshold": 2,

  "sla_target_percent": 99.9,
  "timezone": "UTC",

  "working_hours": { "enabled": false, "start": "09:00", "end": "21:00" },

  "prom_metrics_path": "ip_monitor.prom",
  "session_timeout_minutes": 15
}
```

### `targets.csv` (пример)

```csv
IP;Name;SLA_Target
8.8.8.8;Google DNS;99.9
1.1.1.1;Cloudflare DNS;99.9
9.9.9.9;Quad9;99.7
```

**Пояснения ключей:**

* `recipients[].resources`: `*` — доступ ко всем ресурсам; либо массив IP/Name (строки).
* `reports.daily/weekly/monthly`: расписания отчётов per-user.

  * `weekly.dow`: 1..7 (Пн..Вс).
  * `monthly.day`: 1..31.
* `timezone`: глобальный TZ по умолчанию (IANA). `recipients[].timezone` перекрывает его.
* `working_hours.enabled`: если `true` — SLA/простой считаются только в указанные часы (`start`..`end`).

---

## 🧑‍💻 Команды бота

### Для всех

* `/help` — справка
* `/whoami` — ваш `chat_id`
* `/resources` — ресурсы с номерами
* `/myaccess` — ваши ресурсы
* `/status` — статус ваших ресурсов (UP/DOWN, простой за сегодня)
* `/cancel` — отменить текущий мастер
* `/back` — шаг назад в мастере

### Админ

**Пользователи**

* `/adduser [chat_id role [*|ip1,ip2|name1,name2]]` — мастер без аргументов
* `/setrole [chat_id role]`
* `/deluser [chat_id]`
* `/listusers`
* `/listaccess <chat_id>`

**Ресурсы**

* `/addhost [ip name [sla]]` — мастер без аргументов
* `/removehost [ip]` — мастер без аргументов
* `/grantidx [chat_id idx_list]` — выдать доступ по номерам (напр. `1,3-5`) — мастер без аргументов
* `/revokeidx [chat_id idx_list]` — отозвать доступ — мастер без аргументов

**Отчёты и TZ**

* `/report [daily|weekly|monthly]` — сгенерировать вручную
* `/setreport [chat_id period time on|off]` — мастер без аргументов

  * daily: `HH:MM`
  * weekly: `DOW,HH:MM`
  * monthly: `DAY,HH:MM`
* `/getreport <chat_id>` — показать расписание отчётов
* `/settz [chat_id tz]` — задать TZ (мастер без аргументов)
* `/gettz <chat_id>` — показать TZ
* `/rebuildcron` — **пересобрать cron** по текущему `config.json` (учитываются per-user TZ и расписания)

**Примеры:**

```
/adduser
/setrole 222222222 user
/grantidx 222222222 1,3-4
/settz 222222222 Europe/Berlin
/setreport 222222222 daily 09:30 on
/rebuildcron
```

---

## 🧱 Архитектура

* `monitor.py`

  * Цикл пингов (`ping -c 1 -W 2`), пороги анти-флаппера, логирование (`ip_monitor_log.csv`).
  * Telegram polling: команды/мастера, хранение контекста диалогов в `.tg_sessions.json` (с `/back`, таймаутом).
  * Broadcast уведомлений только тем, у кого есть доступ к ресурсу.
  * Prometheus снапшот в `ip_monitor.prom`.
  * Команда `/rebuildcron` — генерация cron-блоков per-TZ/per-user.

* `report.py`

  * Построение временного окна периода в TZ пользователя.
  * Агрегация недоступности по инцидентам, опциональный учёт рабочих часов.
  * Экспорт CSV/PNG/плоский PDF (ReportLab), отправка в Telegram.

---

## 🔐 Безопасность

* Бот получает команды через Telegram Bot API (polling). Храните `telegram_token` в приватном `config.json`.
* Роль `admin` выдавайте только доверенным chat\_id.
* Файл лога и отчёты содержат временные метки событий — ограничьте доступ к каталогу.

---

## 🧩 Интеграция с Prometheus/Grafana

Файл `ip_monitor.prom` перезаписывается на каждом цикле:

```
ip_up{ip="8.8.8.8",name="Google DNS"} 1
ip_fail_streak{ip="8.8.8.8",name="Google DNS"} 0
ip_succ_streak{ip="8.8.8.8",name="Google DNS"} 5
```

Поднимите node\_exporter textfile collector и укажите путь к этому файлу — дальше настраивайте панели в Grafana.

---

## 🛠 Траблшутинг

* Нет уведомлений в Telegram:

  * проверьте `telegram_token` и интернет у сервера;
  * `journalctl -u ip-monitor -f` — логи сервиса.
* Отчёты «не приходят»:

  * `crontab -l` — есть ли блок между `# BEGIN`/`# END`;
  * повторите `/rebuildcron`;
  * проверьте TZ пользователя и время расписания.
* Пинги всегда «DOWN»:

  * проверьте firewall/ICMP у цели;
  * увеличьте `-W` (таймаут) в `ping_ok` при необходимости.

---

## 🤝 Вклад

PR приветствуются!
Идеи для доработок:

* Webhook-режим для Telegram,
* Расширенные алерты (дедупликация, эскалации),
* Экспорт в Slack/Email,
* Авто-дискавери хостов из инвентаря,
* Гибкие Grafana-дашборды.

---

## 📜 Лицензия

MIT (пример):

```
MIT License

Copyright (c) <year> <you>

Permission is hereby granted, free of charge, to any person obtaining a copy
...
```

---

## 🙋 FAQ

**Q:** Как узнать мой `chat_id`?
**A:** Напишите боту `/whoami`.

**Q:** Как дать пользователю доступ к нескольким ресурсам быстро?
**A:** `/grantidx <chat_id> 1,3-5` — по номерам из `/resources`.

**Q:** Поменяли TZ — почему отчёты ещё по старому?
**A:** Выполните `/rebuildcron` (или запустите `./install_monitor.sh`, он пересоберёт cron).

---

Если нужно — добавлю в README скриншоты отчётов/пример панели Grafana.
