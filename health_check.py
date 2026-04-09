#!/usr/bin/env python3
"""
HEALTH CHECK
============
Quick-glance dashboard for demos and debugging. Shows:
  • Scheduler task status (Windows Task Scheduler)
  • Database: snapshot count, date range, latest packages
  • Log summary: last run, last error
  • Overall pass/fail verdict

Usage:
    python health_check.py
    python health_check.py --json    # machine-readable output
"""

import sqlite3
import os
import io
import json
import subprocess
import sys

# Ensure UTF-8 output on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from datetime import datetime, timedelta
from pathlib import Path

HERE         = Path(__file__).parent
DB_FILE      = HERE / "homebrew_analytics.db"
LOGS_DIR     = HERE / "logs"
MAIN_LOG     = LOGS_DIR / "collection.log"
TASK_NAME    = "HomebrewAnalyticsDailyRun"


# ── ANSI colours (disabled automatically on Windows without colour support) ──
def _supports_color():
    return sys.stdout.isatty() and os.name != "nt" or os.environ.get("TERM")

GREEN  = "\033[92m" if _supports_color() else ""
YELLOW = "\033[93m" if _supports_color() else ""
RED    = "\033[91m" if _supports_color() else ""
RESET  = "\033[0m"  if _supports_color() else ""
BOLD   = "\033[1m"  if _supports_color() else ""


def ok(msg):   return f"{GREEN}[OK]{RESET}  {msg}"
def warn(msg): return f"{YELLOW}[!!]{RESET}  {msg}"
def fail(msg): return f"{RED}[XX]{RESET}  {msg}"


# ── Scheduler check ───────────────────────────────────────────────────────────

def check_scheduler() -> dict:
    result = {"registered": False, "last_run": None, "next_run": None,
              "last_result": None, "status": None}
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST", "/v"],
            capture_output=True, text=True
        )
        if proc.returncode != 0:
            return result

        result["registered"] = True
        for line in proc.stdout.splitlines():
            if line.startswith("Last Run Time"):
                result["last_run"] = line.split(":", 1)[1].strip()
            elif line.startswith("Next Run Time"):
                result["next_run"] = line.split(":", 1)[1].strip()
            elif line.startswith("Last Result"):
                result["last_result"] = line.split(":", 1)[1].strip()
            elif line.startswith("Status"):
                result["status"] = line.split(":", 1)[1].strip()
    except FileNotFoundError:
        pass   # schtasks not available (non-Windows)
    return result


# ── Database check ────────────────────────────────────────────────────────────

def check_database() -> dict:
    result = {"exists": False, "snapshots": 0, "first_date": None,
              "last_date": None, "latest_packages": [], "stale": False}

    if not DB_FILE.exists():
        return result

    result["exists"] = True
    try:
        conn = sqlite3.connect(DB_FILE)
        cur  = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM snapshots")
        result["snapshots"] = cur.fetchone()[0]

        cur.execute("SELECT MIN(date), MAX(date) FROM snapshots")
        first, last = cur.fetchone()
        result["first_date"] = first
        result["last_date"]  = last

        if last:
            last_dt = datetime.strptime(last, "%Y-%m-%d").date()
            result["stale"] = (datetime.now().date() - last_dt) > timedelta(days=2)

        cur.execute('''
            SELECT p.rank, p.package_name, p.install_count
            FROM   packages p
            JOIN   snapshots s ON p.snapshot_id = s.id
            WHERE  s.id = (SELECT MAX(id) FROM snapshots)
            ORDER  BY p.rank
        ''')
        result["latest_packages"] = cur.fetchall()
        conn.close()
    except sqlite3.Error as e:
        result["error"] = str(e)

    return result


# ── Log check ─────────────────────────────────────────────────────────────────

def check_logs() -> dict:
    result = {"log_exists": False, "last_run_line": None,
              "recent_errors": [], "run_files": 0}

    if not LOGS_DIR.exists():
        return result

    run_logs = sorted(LOGS_DIR.glob("run_*.log"))
    result["run_files"] = len(run_logs)

    if not MAIN_LOG.exists():
        return result

    result["log_exists"] = True
    errors = []
    last_run = None

    with open(MAIN_LOG, encoding="utf-8") as f:
        lines = f.readlines()

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if last_run is None:
            last_run = stripped
        if "ERROR" in stripped or "CRITICAL" in stripped:
            errors.append(stripped)
        if len(errors) >= 5:
            break

    result["last_run_line"] = last_run
    result["recent_errors"] = list(reversed(errors))
    return result


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(sched: dict, db: dict, logs: dict):
    issues = 0

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Homebrew Analytics — Health Check{RESET}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{BOLD}{'='*60}{RESET}\n")

    # ── Scheduler ────────────────────────────────────────────────────────────
    print(f"{BOLD}[1] Windows Task Scheduler{RESET}")
    if sched["registered"]:
        print(ok(f"Task '{TASK_NAME}' is registered"))
        if sched["status"]:
            print(f"    Status    : {sched['status']}")
        if sched["last_run"]:
            print(f"    Last run  : {sched['last_run']}")
        if sched["next_run"]:
            print(f"    Next run  : {sched['next_run']}")
        if sched["last_result"] and sched["last_result"] not in ("0", "267011"):
            print(warn(f"Last exit code: {sched['last_result']} (non-zero may indicate failure)"))
            issues += 1
    else:
        print(fail(f"Task '{TASK_NAME}' NOT found in Task Scheduler"))
        print(f"    → Run:  python setup_scheduler.py")
        issues += 1

    # ── Database ──────────────────────────────────────────────────────────────
    print(f"\n{BOLD}[2] Database{RESET}")
    if not db["exists"]:
        print(fail("homebrew_analytics.db not found"))
        print("    → Run:  python run_collection.py")
        issues += 1
    else:
        print(ok(f"Database found: {DB_FILE}"))
        print(ok(f"Snapshots: {db['snapshots']} "
                 f"({db['first_date']} → {db['last_date']})"))
        if db["stale"]:
            print(warn("Last snapshot is >2 days old — scheduler may not be running"))
            issues += 1
        if db.get("error"):
            print(fail(f"DB error: {db['error']}"))
            issues += 1
        if db["latest_packages"]:
            print(f"    Latest top packages:")
            for rank, name, count in db["latest_packages"]:
                print(f"      #{rank}  {name:<22} {count:>9,}")

    # ── Logs ──────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}[3] Logs{RESET}")
    if not logs["log_exists"]:
        print(warn("No log file found yet (will be created on first run)"))
    else:
        print(ok(f"Log directory: {LOGS_DIR}"))
        print(ok(f"Run log files: {logs['run_files']}"))
        if logs["last_run_line"]:
            print(f"    Last entry: {logs['last_run_line']}")
        if logs["recent_errors"]:
            print(warn(f"{len(logs['recent_errors'])} recent error(s) in collection.log:"))
            for e in logs["recent_errors"]:
                print(f"      {e}")
            issues += 1
        else:
            print(ok("No errors in recent log entries"))

    # ── Verdict ───────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'='*60}{RESET}")
    if issues == 0:
        print(f"{GREEN}{BOLD}  ALL CHECKS PASSED — pipeline is healthy{RESET}")
    else:
        print(f"{RED}{BOLD}  {issues} ISSUE(S) FOUND — see above for details{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")


def main():
    as_json = "--json" in sys.argv

    sched = check_scheduler()
    db    = check_database()
    logs  = check_logs()

    if as_json:
        print(json.dumps({"scheduler": sched, "database": db, "logs": logs},
                         indent=2, default=str))
        return

    print_report(sched, db, logs)


if __name__ == "__main__":
    main()
