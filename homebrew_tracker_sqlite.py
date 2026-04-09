#!/usr/bin/env python3
"""
Homebrew Package Tracker with SQLite Database
==============================================
Fetches the Homebrew 30-day install analytics, validates the payload,
and persists it to a local SQLite database.

Database Structure:
- snapshots table: one row per collection run
- packages  table: top-N packages for each snapshot (FK → snapshots.id)

Run directly:
    python homebrew_tracker_sqlite.py

Or via the scheduler wrapper:
    python run_collection.py
"""

import requests
import sqlite3
from datetime import datetime, date
import os
import sys

from logger import get_logger

# ============================================================================
# CONFIGURATION
# ============================================================================

API_URL       = "https://formulae.brew.sh/api/analytics/install/30d.json"
DATABASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "homebrew_analytics.db")
TOP_N         = 5
EXPECTED_WINDOW_DAYS = 30   # API is a 30-day rolling window
WINDOW_TOLERANCE     = 3    # allow ±3 days drift before flagging

log = get_logger("homebrew")

# ============================================================================
# VALIDATION HELPERS
# ============================================================================

def _parse_date(value: str, field_name: str) -> date | None:
    """Parse YYYY-MM-DD string; log a warning and return None on failure."""
    if not value:
        log.warning("Validation: '%s' is empty or missing.", field_name)
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        log.warning(
            "Validation: '%s' value '%s' is not a valid YYYY-MM-DD date.",
            field_name, value,
        )
        return None


def validate_api_data(data: dict) -> bool:
    """
    Run structural and date-math checks on the raw API payload.

    Returns True if data is safe to persist, False if critically invalid.
    Logs warnings for non-critical anomalies so they appear in the log file.
    """
    ok = True

    # ── Required top-level keys ──────────────────────────────────────────────
    for key in ("items", "total_items", "start_date", "end_date"):
        if key not in data:
            log.error("Validation: required key '%s' missing from API response.", key)
            ok = False

    if not ok:
        return False   # no point continuing

    # ── Date format & ordering ───────────────────────────────────────────────
    start = _parse_date(data.get("start_date", ""), "start_date")
    end   = _parse_date(data.get("end_date",   ""), "end_date")

    if start and end:
        if end < start:
            log.error(
                "Validation: end_date (%s) is before start_date (%s) — data rejected.",
                end, start,
            )
            return False

        window = (end - start).days
        expected_lo = EXPECTED_WINDOW_DAYS - WINDOW_TOLERANCE
        expected_hi = EXPECTED_WINDOW_DAYS + WINDOW_TOLERANCE
        if not (expected_lo <= window <= expected_hi):
            log.warning(
                "Validation: date window is %d days (expected ~%d). "
                "API may have changed — proceeding with caution.",
                window, EXPECTED_WINDOW_DAYS,
            )
    elif not start or not end:
        log.warning("Validation: could not fully validate date range due to parse errors.")

    # ── Item-level checks ────────────────────────────────────────────────────
    items = data.get("items", [])
    if not items:
        log.error("Validation: 'items' list is empty — nothing to save.")
        return False

    for i, item in enumerate(items[:TOP_N]):
        for field in ("formula", "count", "percent", "number"):
            if field not in item:
                log.warning(
                    "Validation: item[%d] missing field '%s'.", i, field
                )

        raw_count = item.get("count", "0")
        try:
            count = int(str(raw_count).replace(",", ""))
            if count <= 0:
                log.warning(
                    "Validation: item[%d] (%s) has non-positive count %d.",
                    i, item.get("formula", "?"), count,
                )
        except ValueError:
            log.warning(
                "Validation: item[%d] count '%s' cannot be parsed as integer.",
                i, raw_count,
            )

    log.info("Validation passed for %d items (start=%s, end=%s).",
             len(items), data.get("start_date"), data.get("end_date"))
    return True

# ============================================================================
# DATABASE SETUP
# ============================================================================

def setup_database():
    """Create tables and index if they do not already exist."""
    log.info("Setting up database: %s", DATABASE_FILE)

    conn = sqlite3.connect(DATABASE_FILE)
    cur  = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL,
            date            TEXT    NOT NULL,
            total_packages  INTEGER,
            api_start_date  TEXT,
            api_end_date    TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS packages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id   INTEGER NOT NULL,
            package_name  TEXT    NOT NULL,
            install_count INTEGER NOT NULL,
            percentage    TEXT,
            rank          INTEGER,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        )
    ''')

    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_snapshot_id
        ON packages(snapshot_id)
    ''')

    conn.commit()
    conn.close()
    log.info("Database ready.")

# ============================================================================
# API
# ============================================================================

def fetch_api_data() -> dict | None:
    """Fetch and validate the Homebrew analytics payload."""
    log.info("Fetching: %s", API_URL)
    try:
        response = requests.get(API_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        log.info("API returned %d packages.", len(data.get("items", [])))
        return data
    except requests.exceptions.Timeout:
        log.error("API request timed out after 15 s.")
    except requests.exceptions.HTTPError as e:
        log.error("HTTP error: %s", e)
    except requests.exceptions.RequestException as e:
        log.error("Network error: %s", e)
    except ValueError:
        log.error("API response is not valid JSON.")
    return None

# ============================================================================
# DATABASE WRITE
# ============================================================================

def save_snapshot(api_data: dict) -> int | None:
    """
    Persist one snapshot + top-N packages.

    Uses a single transaction so partial writes are rolled back on error.
    Returns the new snapshot_id, or None on failure.
    """
    log.info("Saving snapshot to database...")
    conn = sqlite3.connect(DATABASE_FILE)
    cur  = conn.cursor()

    try:
        cur.execute('''
            INSERT INTO snapshots
                (timestamp, date, total_packages, api_start_date, api_end_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(),
            datetime.now().strftime("%Y-%m-%d"),
            api_data.get("total_items", 0),
            api_data.get("start_date"),
            api_data.get("end_date"),
        ))

        snapshot_id = cur.lastrowid
        log.info("Snapshot #%d created.", snapshot_id)

        saved = 0
        for item in api_data.get("items", [])[:TOP_N]:
            count = int(str(item["count"]).replace(",", ""))
            cur.execute('''
                INSERT INTO packages
                    (snapshot_id, package_name, install_count, percentage, rank)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                snapshot_id,
                item["formula"],
                count,
                item["percent"],
                item["number"],
            ))
            log.info("  #%s %-22s %9s installs",
                     item["number"], item["formula"], f"{count:,}")
            saved += 1

        conn.commit()
        log.info("Snapshot saved. %d packages written.", saved)
        return snapshot_id

    except Exception as exc:
        conn.rollback()
        log.exception("Failed to save snapshot — rolled back. Error: %s", exc)
        return None

    finally:
        conn.close()

# ============================================================================
# SUMMARY
# ============================================================================

def show_database_summary():
    """Print a concise summary of DB contents to console / log."""
    conn = sqlite3.connect(DATABASE_FILE)
    cur  = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM snapshots")
    total_snapshots = cur.fetchone()[0]

    cur.execute("SELECT MIN(date), MAX(date) FROM snapshots")
    first_date, last_date = cur.fetchone()

    log.info("-" * 50)
    log.info("DB SUMMARY - %d snapshot(s)  (%s to %s)",
             total_snapshots, first_date, last_date)

    cur.execute('''
        SELECT p.rank, p.package_name, p.install_count, p.percentage
        FROM   packages p
        JOIN   snapshots s ON p.snapshot_id = s.id
        WHERE  s.id = (SELECT MAX(id) FROM snapshots)
        ORDER  BY p.rank
    ''')
    log.info("Latest top %d:", TOP_N)
    for rank, name, count, pct in cur.fetchall():
        log.info("  #%-2s %-22s %9s installs  (%s%%)",
                 rank, name, f"{count:,}", pct)

    if total_snapshots >= 2:
        cur.execute('''
            WITH first AS (
                SELECT package_name, install_count
                FROM   packages
                WHERE  snapshot_id = (SELECT MIN(id) FROM snapshots)
            ),
            last AS (
                SELECT package_name, install_count
                FROM   packages
                WHERE  snapshot_id = (SELECT MAX(id) FROM snapshots)
            )
            SELECT l.package_name,
                   f.install_count,
                   l.install_count,
                   l.install_count - COALESCE(f.install_count, 0)
            FROM   last l
            LEFT JOIN first f ON l.package_name = f.package_name
            ORDER  BY l.install_count DESC
        ''')
        log.info("Trend (first vs latest):")
        for name, first, last, delta in cur.fetchall():
            arrow = "^" if delta > 0 else ("v" if delta < 0 else "=")
            sign  = "+" if delta > 0 else ""
            log.info("  %-22s %s %s%s", name, arrow, sign, f"{delta:,}")

    conn.close()

# ============================================================================
# MAIN
# ============================================================================

def main():
    log.info("Homebrew Analytics Tracker — starting")

    setup_database()

    api_data = fetch_api_data()
    if not api_data:
        log.error("Could not fetch API data. Aborting.")
        sys.exit(1)

    if not validate_api_data(api_data):
        log.error("API data failed validation. Aborting.")
        sys.exit(1)

    snapshot_id = save_snapshot(api_data)
    if not snapshot_id:
        log.error("Could not persist snapshot. Aborting.")
        sys.exit(1)

    show_database_summary()
    log.info("Run complete. Snapshot ID: %d", snapshot_id)


if __name__ == "__main__":
    main()
