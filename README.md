# Wex 2026 — Homebrew Analytics

Automated pipeline that collects daily Homebrew install-count snapshots from the [Homebrew Analytics API](https://formulae.brew.sh/api/), stores them in a local SQLite database, and renders a two-panel dashboard chart.

---

## Project Structure

```
Homebrew_API/
├── homebrew_tracker_sqlite.py      # Core tracker — fetch, validate, persist
├── homebrew_tracker_sqlite_demo.py # Offline demo (uses sample data)
├── run_collection.py               # Scheduler entry point (also works manually)
├── plot_analytics.py               # Chart generator (line + bar)
├── setup_scheduler.py              # One-command scheduler registration
├── health_check.py                 # Pipeline health dashboard
├── logger.py                       # Shared logging module
├── homebrew_analytics.db           # SQLite database (auto-created)
├── homebrew_analytics.png          # Latest chart (auto-generated)
└── logs/
    ├── collection.log              # Persistent log across all runs
    └── run_YYYY-MM-DD_HH-MM-SS.log # Per-run detail log
```

---

## Quick Start

### 1 — Install dependencies
```bash
pip install requests matplotlib
```

### 2 — Run once manually
```bash
python run_collection.py
```

### 3 — Generate chart
```bash
python plot_analytics.py
```

### 4 — Set up daily automation
```bash
python setup_scheduler.py           # registers daily 08:00 run
python setup_scheduler.py --time 09:00   # custom time
```

### 5 — Verify everything is working
```bash
python health_check.py
```

---

## Scheduler

### Windows (Task Scheduler) — Recommended on this machine

`setup_scheduler.py` registers a **Windows Task Scheduler** job that runs `run_collection.py` daily at 08:00 using your current Python executable.

```
python setup_scheduler.py               # install at 08:00
python setup_scheduler.py --time 09:30  # install at 09:30
python setup_scheduler.py --status      # show task details
python setup_scheduler.py --remove      # unregister task
```

**Why Task Scheduler?**
- Built into every version of Windows — no extra software needed
- Runs even if you are not logged in (with correct user settings)
- Survives reboots automatically
- Easy to inspect via the Task Scheduler GUI (`taskschd.msc`)

---

### macOS (launchd)

Create `~/Library/LaunchAgents/com.homebrew.analytics.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>          <string>com.homebrew.analytics</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/full/path/to/run_collection.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key> <string>/tmp/homebrew_analytics.log</string>
  <key>StandardErrorPath</key><string>/tmp/homebrew_analytics_err.log</string>
</dict>
</plist>
```

Then load it:
```bash
launchctl load ~/Library/LaunchAgents/com.homebrew.analytics.plist
```

---

### Linux (cron)

```bash
crontab -e
# Add this line (runs at 08:00 daily):
0 8 * * * /usr/bin/python3 /full/path/to/run_collection.py >> /full/path/to/logs/cron.log 2>&1
```

---

## Data Validation

Each run validates the API payload before writing anything to the database:

| Check | What it catches |
|---|---|
| Required keys present | Broken API schema |
| `end_date >= start_date` | Date field inversion |
| Window ≈ 30 days (±3) | API window change |
| `install_count > 0` | Zero/negative counts |
| All item fields present | Incomplete records |

Warnings are logged but do not abort the run; errors abort before any DB writes.

---

## Logging

Every run writes to two places:

| File | Purpose |
|---|---|
| `logs/collection.log` | Persistent history — one file, all runs appended |
| `logs/run_YYYY-MM-DD_HH-MM-SS.log` | Full debug log for a single run |

Log format:
```
2026-03-25 08:00:01 | INFO     | Homebrew Analytics Tracker — starting
2026-03-25 08:00:01 | INFO     | Setting up database: ...
2026-03-25 08:00:02 | INFO     | Snapshot #12 created.
```

---

## Health Check

Run before a demo or whenever something seems off:

```bash
python health_check.py
```

Output:
```
============================================================
  Homebrew Analytics — Health Check
  2026-03-25 09:00:00
============================================================

[1] Windows Task Scheduler
✔  Task 'HomebrewAnalyticsDailyRun' is registered
    Status    : Ready
    Last run  : 2026-03-25 08:00:00
    Next run  : 2026-03-26 08:00:00

[2] Database
✔  Database found
✔  Snapshots: 5 (2026-03-20 → 2026-03-25)
    Latest top packages:
      #1  openssl@3              488,020
      ...

[3] Logs
✔  Log directory: logs/
✔  Run log files: 5
✔  No errors in recent log entries

============================================================
  ALL CHECKS PASSED — pipeline is healthy
============================================================
```

For machine-readable output (CI / scripts):
```bash
python health_check.py --json
```

---

## Chart

```bash
python plot_analytics.py
```

Produces `homebrew_analytics.png`:
- **Left** — line chart: install count trend per package across all collected dates
- **Right** — horizontal bar chart: latest snapshot rankings with exact counts

Requires at least 1 snapshot for the bar chart; 2+ snapshots for meaningful trend lines.

---

## Troubleshooting

**`setup_scheduler.py` says "Access denied"**
Run the terminal as Administrator (right-click → "Run as administrator").

**Scheduler task exists but never ran**
Open Task Scheduler GUI (`taskschd.msc`), find `HomebrewAnalyticsDailyRun`, right-click → Run to test it manually. Check "Last Run Result" — code `0` = success.

**`homebrew_analytics.db` missing after scheduler run**
The task may be running from the wrong working directory. Re-register with the full path:
```bash
python setup_scheduler.py --remove
python setup_scheduler.py
```

**Chart shows no trend line (flat)**
You need snapshots from at least 2 different days. The line chart plots one point per day — intra-day duplicates are deduplicated automatically.

**`requests` or `matplotlib` not found**
```bash
pip install requests matplotlib
```

If the scheduler runs a different Python than your shell:
```bash
python setup_scheduler.py --remove
# Then re-register — setup_scheduler.py always uses the Python it was launched with
python setup_scheduler.py
```

---

## Database Schema

```sql
snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT,       -- ISO-8601 datetime of collection
    date            TEXT,       -- YYYY-MM-DD
    total_packages  INTEGER,    -- total items reported by API
    api_start_date  TEXT,       -- rolling window start
    api_end_date    TEXT        -- rolling window end
)

packages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id   INTEGER,      -- FK → snapshots.id
    package_name  TEXT,
    install_count INTEGER,
    percentage    TEXT,
    rank          INTEGER
)
```

Query example — trend for a specific package:
```sql
SELECT s.date, p.install_count
FROM   packages p
JOIN   snapshots s ON p.snapshot_id = s.id
WHERE  p.package_name = 'openssl@3'
ORDER  BY s.date;
```
