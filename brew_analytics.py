#!/usr/bin/env python3
"""
brew_analytics.py — Fetch Homebrew install analytics and store them in SQLite.

Usage:
    python brew_analytics.py fetch              # Fetch 30d data (default)
    python brew_analytics.py fetch --days 90    # Fetch 90d data (30 | 90 | 365)
    python brew_analytics.py fetch --db my.db   # Use a custom DB file
    python brew_analytics.py fetch --force      # Replace today's snapshot if it exists
    python brew_analytics.py query              # Show the 10 most recent rows
    python brew_analytics.py query --top 25     # Show top 25 from latest snapshot
    python brew_analytics.py query --list       # List all stored snapshots
    python brew_analytics.py --help
"""

import argparse
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://formulae.brew.sh/api/analytics/install/{days}d.json"
VALID_DAYS = (30, 90, 365)
DEFAULT_DB = "brew_analytics.db"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist yet."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at  TEXT    NOT NULL,   -- UTC ISO-8601 timestamp of this fetch
            category    TEXT,
            period_days INTEGER,
            start_date  TEXT,
            end_date    TEXT,
            total_items INTEGER,
            total_count INTEGER
        );

        CREATE TABLE IF NOT EXISTS installs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
            rank        INTEGER,
            formula     TEXT    NOT NULL,
            count       INTEGER,
            percent     REAL
        );

        CREATE INDEX IF NOT EXISTS idx_installs_snapshot ON installs(snapshot_id);
        CREATE INDEX IF NOT EXISTS idx_installs_formula  ON installs(formula);
    """)
    conn.commit()

# ---------------------------------------------------------------------------
# Fetch command
# ---------------------------------------------------------------------------

def cmd_fetch(args: argparse.Namespace) -> None:
    days = args.days
    db_path = args.db
    
    url = BASE_URL.format(days=days)
    print(f"→ Fetching {url} …")

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"✗ Failed to fetch data: {exc}", file=sys.stderr)
        sys.exit(1)

    fetched_at = datetime.now(timezone.utc).isoformat()
    fetch_date = fetched_at[:10] # strip to YYYY-MM-DD
    items = payload.get("items", [])

    conn = get_connection(db_path)
    init_db(conn)

    # Add a duplicate request guard to limit fetching to once per day
    existing = conn.execute(
        "SELECT id, fetched_at FROM snapshots WHERE end_date = ? AND period_days = ?",
        (fetch_date, days),
      ).fetchone()
    
    if existing:
        print(f"A snapshot for {fetch_date} ({days}d) already exists "
             f"(snapshot #{existing['id']}, fetched {existing['fetched_at']}).\n"
             f" Skipping to avoid duplicate data.\n"
            )
        conn.close()
        sys.exit(0)
    
    cur = conn.execute(
        """
        INSERT INTO snapshots
            (fetched_at, category, period_days, start_date, end_date, total_items, total_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fetched_at,
            payload.get("category"),
            days,
            payload.get("start_date"),
            payload.get("end_date"),
            payload.get("total_items"),
            payload.get("total_count"),
        ),
    )
    snapshot_id = cur.lastrowid

    rows = []
    for item in items:
        # API returns count as a string with commas, e.g. "428,259"
        raw_count = item.get("count", "0")
        count = int(str(raw_count).replace(",", "")) if raw_count else 0
        rows.append((
            snapshot_id,
            item.get("number"),
            item.get("formula"),
            count,
            float(item.get("percent", 0)),
        ))

    conn.executemany(
        """
        INSERT INTO installs (snapshot_id, rank, formula, count, percent)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()

    print(f"✓ Snapshot #{snapshot_id} saved — {len(rows):,} formulae "
          f"({payload.get('start_date')} → {payload.get('end_date')})")
    print(f"  Database: {Path(db_path).resolve()}")

# ---------------------------------------------------------------------------
# Query command
# ---------------------------------------------------------------------------

def cmd_query(args: argparse.Namespace) -> None:
    db_path = args.db

    if not Path(db_path).exists():
        print(f"✗ Database not found: {db_path}\n  Run 'fetch' first.", file=sys.stderr)
        sys.exit(1)

    conn = get_connection(db_path)
    init_db(conn)

    # --list: show all snapshots
    if args.list:
        rows = conn.execute(
            "SELECT id, fetched_at, period_days, start_date, end_date, total_items, total_count "
            "FROM snapshots ORDER BY id"
        ).fetchall()
        if not rows:
            print("No snapshots found.")
            return
        print(f"\n{'ID':>4}  {'Fetched (UTC)':<27}  {'Days':>4}  "
              f"{'Period':<23}  {'Formulae':>8}  {'Total installs':>14}")
        print("-" * 88)
        for r in rows:
            period = f"{r['start_date']} → {r['end_date']}"
            print(f"{r['id']:>4}  {r['fetched_at']:<27}  {r['period_days']:>4}  "
                  f"{period:<23}  {r['total_items']:>8,}  {r['total_count']:>14,}")
        print()
        conn.close()
        return

    # Default: top N from the latest snapshot
    snap = conn.execute(
        "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not snap:
        print("No snapshots found. Run 'fetch' first.")
        conn.close()
        return

    top = args.top
    rows = conn.execute(
        """
        SELECT rank, formula, count, percent
        FROM installs
        WHERE snapshot_id = ?
        ORDER BY rank
        LIMIT ?
        """,
        (snap["id"], top),
    ).fetchall()

    print(f"\nSnapshot #{snap['id']}  |  "
          f"{snap['start_date']} → {snap['end_date']}  |  "
          f"fetched {snap['fetched_at']}")
    print(f"\n{'Rank':>4}  {'Formula':<35}  {'Installs':>10}  {'%':>6}")
    print("-" * 62)
    for r in rows:
        print(f"{r['rank']:>4}  {r['formula']:<35}  {r['count']:>10,}  {r['percent']:>5.2f}%")
    print()
    conn.close()

# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brew_analytics",
        description="Fetch Homebrew install analytics and store them in SQLite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", default=DEFAULT_DB, metavar="FILE",
                        help=f"SQLite database file (default: {DEFAULT_DB})")

    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    p_fetch = sub.add_parser("fetch", help="Download analytics and store a new snapshot")
    p_fetch.add_argument("--days", type=int, default=30, choices=VALID_DAYS,
                         help="Analytics window in days (default: 30)")
    p_fetch.set_defaults(func=cmd_fetch)

    # query
    p_query = sub.add_parser("query", help="Display stored data")
    p_query.add_argument("--top", type=int, default=10, metavar="N",
                         help="Show top N formulae from the latest snapshot (default: 10)")
    p_query.add_argument("--list", action="store_true",
                         help="List all stored snapshots instead of showing formulae")
    p_query.set_defaults(func=cmd_query)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
