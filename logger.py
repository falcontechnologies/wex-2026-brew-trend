#!/usr/bin/env python3
"""
Centralized logger for Homebrew Analytics pipeline.
Writes to both console and logs/ directory.
"""

import logging
import os
from datetime import datetime

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
MAIN_LOG = os.path.join(LOGS_DIR, "collection.log")


def get_logger(name: str = "homebrew") -> logging.Logger:
    """
    Return a logger that writes to:
      - console (INFO+)
      - logs/collection.log (INFO+, persistent across all runs)
      - logs/run_YYYY-MM-DD_HH-MM-SS.log (this run only)
    """
    os.makedirs(LOGS_DIR, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:          # already configured — return as-is
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ──────────────────────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # ── Persistent log (appended every run) ──────────────────────────────────
    fh_main = logging.FileHandler(MAIN_LOG, encoding="utf-8")
    fh_main.setLevel(logging.INFO)
    fh_main.setFormatter(fmt)

    # ── Per-run log ───────────────────────────────────────────────────────────
    # If a parent process already opened a log file, reuse it (no duplicate).
    run_log = os.environ.get("HOMEBREW_RUN_LOG")
    is_child = run_log is not None
    if not run_log:
        run_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        run_log = os.path.join(LOGS_DIR, f"run_{run_ts}.log")
        os.environ["HOMEBREW_RUN_LOG"] = run_log   # inherited by subprocesses

    fh_run = logging.FileHandler(run_log, encoding="utf-8")
    fh_run.setLevel(logging.DEBUG)
    fh_run.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh_main)
    logger.addHandler(fh_run)

    if not is_child:
        logger.info("=" * 60)
        logger.info("RUN STARTED - log file: %s", run_log)
        logger.info("=" * 60)

    return logger
