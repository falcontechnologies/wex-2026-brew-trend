#!/usr/bin/env python3
"""
SCHEDULER SETUP
===============
Registers a daily Windows Task Scheduler job that runs run_collection.py
automatically at 08:00 every morning.

Usage:
    python setup_scheduler.py           # install task (default 08:00)
    python setup_scheduler.py --time 09:30
    python setup_scheduler.py --remove  # delete the task
    python setup_scheduler.py --status  # show task info

Cross-platform notes
--------------------
Windows  → Windows Task Scheduler (this script)
macOS    → launchd  (see README.md — "macOS / Linux Setup")
Linux    → cron     (see README.md — "macOS / Linux Setup")
"""

import subprocess
import sys
import os
import argparse

TASK_NAME  = "HomebrewAnalyticsDailyRun"
HERE       = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = sys.executable
SCRIPT     = os.path.join(HERE, "run_collection.py")


# ── helpers ──────────────────────────────────────────────────────────────────

def run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def task_exists() -> bool:
    code, out, _ = run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"])
    return code == 0


# ── actions ──────────────────────────────────────────────────────────────────

def install(run_time: str = "08:00"):
    """Create (or replace) the scheduled task."""
    if task_exists():
        print(f"Task '{TASK_NAME}' already exists — replacing it.")
        remove(silent=True)

    cmd = [
        "schtasks", "/create",
        "/tn",  TASK_NAME,
        "/tr",  f'"{PYTHON_EXE}" "{SCRIPT}"',
        "/sc",  "daily",
        "/st",  run_time,
        "/rl",  "HIGHEST",          # run with highest available privileges
        "/f",                        # force overwrite if it exists
    ]
    code, out, err = run(cmd)

    if code == 0:
        # Allow the task to run on battery (off by default in schtasks)
        ps = "\n".join([
            f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}'",
            "$t.Settings.DisallowStartIfOnBatteries = $false",
            "$t.Settings.StopIfGoingOnBatteries = $false",
            f"Set-ScheduledTask -TaskName '{TASK_NAME}' -Settings $t.Settings",
        ])
        run(["powershell", "-Command", ps])

        print(f"Task '{TASK_NAME}' created successfully.")
        print(f"  Schedule : daily at {run_time}")
        print(f"  Battery  : runs on battery AND AC power")
        print(f"  Python   : {PYTHON_EXE}")
        print(f"  Script   : {SCRIPT}")
        print()
        print("Run `python setup_scheduler.py --status` to verify.")
    else:
        print(f"ERROR: Could not create task (exit {code}).")
        print(f"  stdout : {out}")
        print(f"  stderr : {err}")
        print()
        print("TIP: Try running this script as Administrator.")
        sys.exit(1)


def remove(silent: bool = False):
    """Delete the scheduled task."""
    if not task_exists():
        if not silent:
            print(f"Task '{TASK_NAME}' does not exist — nothing to remove.")
        return

    code, out, err = run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
    )
    if code == 0:
        if not silent:
            print(f"Task '{TASK_NAME}' removed.")
    else:
        print(f"ERROR: Could not remove task: {err}")
        sys.exit(1)


def status():
    """Display current task information."""
    if not task_exists():
        print(f"Task '{TASK_NAME}' is NOT registered.")
        print("Run `python setup_scheduler.py` to install it.")
        return

    code, out, err = run(
        ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST", "/v"]
    )
    if code == 0:
        # Print only the most useful lines
        relevant = [
            "TaskName", "Status", "Next Run Time", "Last Run Time",
            "Last Result", "Run As User", "Schedule Type", "Start Time",
        ]
        for line in out.splitlines():
            if any(line.startswith(k) for k in relevant):
                print(line)
    else:
        print(f"Could not query task: {err}")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Manage the Homebrew Analytics daily scheduler (Windows Task Scheduler)."
    )
    parser.add_argument(
        "--time", default="08:00",
        help="Daily run time in HH:MM 24-hour format (default: 08:00)"
    )
    parser.add_argument(
        "--remove", action="store_true",
        help="Remove the scheduled task"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current task status"
    )
    args = parser.parse_args()

    if args.remove:
        remove()
    elif args.status:
        status()
    else:
        install(args.time)


if __name__ == "__main__":
    main()
