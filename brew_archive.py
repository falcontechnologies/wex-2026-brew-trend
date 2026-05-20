#!/usr/bin/env python3
"""
Moves rows older than 30 days from the active database to an archive database,
then runs VACUUM to reclaim disk space.

Run manually:
    python3.11 brew_archive.py

Or automatically after each fetch by adding it to the launchd plist.

Usage:
    python3.11 brew_archive.py
    python3.11 brew_archive.py --db brew_analytics.db --archive brew_analytics_archive.db
    python3.11 brew_archive.py --days 60  # keep 60 days instead of 30
"""

import argparse
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_DB      = "brew_analytics.db"
DEFAULT_ARCHIVE = "brew_analytics_archive.db"
DEFAULT_DAYS    = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def db_size_mb(db_path: str) -> float:
    if not os.path.exists(db_path):
        return 0.0
    return os.path.getsize(db_path) / (1024 * 1024)


def create_archive_tables(archive_conn: sqlite3.Connection) -> None:
    """Create the same table structure in the archive database if not present."""
    archive_conn.executescript("""
        CREATE TABLE IF NOT EXISTS analytics_runs (
            id           INTEGER PRIMARY KEY,
            fetched_at   TEXT    NOT NULL,
            category     TEXT    NOT NULL,
            start_date   TEXT    NOT NULL,
            end_date     TEXT    NOT NULL,
            total_items  INTEGER NOT NULL,
            total_count  INTEGER NOT NULL,
            source_url   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS formula_installs (
            id            INTEGER PRIMARY KEY,
            run_id        INTEGER NOT NULL,
            rank          INTEGER NOT NULL,
            formula       TEXT    NOT NULL,
            install_count INTEGER NOT NULL,
            percent       REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS formula_deltas (
            id                 INTEGER PRIMARY KEY,
            run_id             INTEGER NOT NULL,
            formula            TEXT    NOT NULL,
            date               TEXT    NOT NULL,
            install_count      INTEGER NOT NULL,
            prev_install_count INTEGER,
            delta_count        INTEGER NOT NULL,
            delta_percent      REAL,
            is_new             INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_archive_installs_run
            ON formula_installs(run_id);
        CREATE INDEX IF NOT EXISTS idx_archive_deltas_date
            ON formula_deltas(date);
    """)
    archive_conn.commit()


# ---------------------------------------------------------------------------
# Archive process
# ---------------------------------------------------------------------------

def archive_old_data(
    active_conn: sqlite3.Connection,
    archive_conn: sqlite3.Connection,
    cutoff_date: str,
) -> dict:
    """
    1. Find all runs older than cutoff_date
    2. Copy their data to the archive database
    3. Delete them from the active database
    Returns counts of what was moved.
    """

    # Find old run IDs
    old_runs = active_conn.execute(
        "SELECT * FROM analytics_runs WHERE end_date < ?",
        (cutoff_date,),
    ).fetchall()

    if not old_runs:
        return {"runs": 0, "installs": 0, "deltas": 0}

    old_run_ids = [r["id"] for r in old_runs]
    placeholders = ",".join("?" * len(old_run_ids))

    # ── Copy to archive ──────────────────────────────────────────────────────

    # analytics_runs
    archive_conn.executemany(
        """
        INSERT OR IGNORE INTO analytics_runs
            (id, fetched_at, category, start_date, end_date,
             total_items, total_count, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [tuple(r) for r in old_runs],
    )

    # formula_installs
    installs = active_conn.execute(
        f"SELECT * FROM formula_installs WHERE run_id IN ({placeholders})",
        old_run_ids,
    ).fetchall()
    archive_conn.executemany(
        """
        INSERT OR IGNORE INTO formula_installs
            (id, run_id, rank, formula, install_count, percent)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [tuple(r) for r in installs],
    )

    # formula_deltas
    deltas = active_conn.execute(
        f"SELECT * FROM formula_deltas WHERE run_id IN ({placeholders})",
        old_run_ids,
    ).fetchall()
    archive_conn.executemany(
        """
        INSERT OR IGNORE INTO formula_deltas
            (id, run_id, formula, date, install_count, prev_install_count,
             delta_count, delta_percent, is_new)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [tuple(r) for r in deltas],
    )

    archive_conn.commit()

    # ── Delete from active ───────────────────────────────────────────────────

    active_conn.execute(
        f"DELETE FROM formula_deltas WHERE run_id IN ({placeholders})",
        old_run_ids,
    )
    active_conn.execute(
        f"DELETE FROM formula_installs WHERE run_id IN ({placeholders})",
        old_run_ids,
    )
    active_conn.execute(
        f"DELETE FROM analytics_runs WHERE id IN ({placeholders})",
        old_run_ids,
    )
    active_conn.commit()

    return {
        "runs":     len(old_runs),
        "installs": len(installs),
        "deltas":   len(deltas),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Archive old brew analytics data.")
    parser.add_argument("--db",      default=DEFAULT_DB,      help="Active database path")
    parser.add_argument("--archive", default=DEFAULT_ARCHIVE, help="Archive database path")
    parser.add_argument("--days",    default=DEFAULT_DAYS,    type=int,
                        help=f"Keep this many days in the active DB (default: {DEFAULT_DAYS})")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"  ✗ Database not found: {args.db}")
        return

    # Cutoff: any run with end_date before this gets archived
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    print(f"\n🗄  Brew Analytics Archiver")
    print(f"   Active DB  : {args.db} ({db_size_mb(args.db):.1f} MB)")
    print(f"   Archive DB : {args.archive} ({db_size_mb(args.archive):.1f} MB)")
    print(f"   Cutoff     : {cutoff} (keeping last {args.days} days)\n")

    active_conn  = get_connection(args.db)
    archive_conn = get_connection(args.archive)
    create_archive_tables(archive_conn)

    moved = archive_old_data(active_conn, archive_conn, cutoff)

    if moved["runs"] == 0:
        print("  ✓ Nothing to archive — all runs are within the retention window.")
    else:
        print(f"  ✓ Archived {moved['runs']} runs, "
              f"{moved['installs']:,} install rows, "
              f"{moved['deltas']:,} delta rows.")

        # Reclaim disk space in the active database
        print("  → Running VACUUM on active database...")
        active_conn.execute("VACUUM")
        active_conn.commit()
        print(f"  ✓ Active DB now {db_size_mb(args.db):.1f} MB")
        print(f"  ✓ Archive DB now {db_size_mb(args.archive):.1f} MB")

    active_conn.close()
    archive_conn.close()
    print()


if __name__ == "__main__":
    main()
