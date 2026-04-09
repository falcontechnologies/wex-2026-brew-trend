#!/usr/bin/env python3
"""
COLLECTION RUNNER
=================
Entry point for both manual runs and the Windows Task Scheduler.

Usage:
    python run_collection.py            # normal run
    python run_collection.py --plot     # collect + regenerate chart
"""

import subprocess
import sys
import os
from datetime import datetime

from logger import get_logger

log = get_logger("homebrew")

HERE = os.path.dirname(os.path.abspath(__file__))


def run_script(script_name: str) -> bool:
    """Run a Python script in the same directory. Returns True on success."""
    path = os.path.join(HERE, script_name)
    log.info("Launching: %s", path)
    try:
        result = subprocess.run(
            [sys.executable, path],
            cwd=HERE,
            capture_output=False,   # let output stream to console
            check=True,
        )
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        log.error("'%s' exited with code %d.", script_name, e.returncode)
        return False
    except FileNotFoundError:
        log.error("Script not found: %s", path)
        return False


def main():
    plot_after = "--plot" in sys.argv

    log.info("=" * 60)
    log.info("COLLECTION RUN - %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    success = run_script("homebrew_tracker_sqlite.py")

    if success:
        log.info("Data collection: SUCCESS")
        if plot_after:
            log.info("Regenerating chart…")
            run_script("plot_analytics.py")
    else:
        log.error("Data collection: FAILED — check logs/ for details")
        sys.exit(1)


if __name__ == "__main__":
    main()
