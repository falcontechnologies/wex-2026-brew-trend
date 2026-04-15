#!/usr/bin/env python3
"""
Fetch Homebrew 30-day install analytics and store them in a SQLite database.

Usage:
    python brew_analytics_to_sqlite.py
    python brew_analytics_to_sqlite.py --db my_database.db
    python brew_analytics_to_sqlite.py --url https://formulae.brew.sh/api/analytics/install/90d.json
"""

import argparse
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone

DEFAULT_URL = "https://formulae.brew.sh/api/analytics/install/30d.json"
DEFAULT_DB  = "brew_analytics.db"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS analytics_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at   TEXT    NOT NULL,          -- ISO-8601 UTC timestamp
            category     TEXT    NOT NULL,
            start_date   TEXT    NOT NULL,
            end_date     TEXT    NOT NULL,
            total_items  INTEGER NOT NULL,
            total_count  INTEGER NOT NULL,
            source_url   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS formula_installs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       INTEGER NOT NULL REFERENCES analytics_runs(id),
            rank         INTEGER NOT NULL,
            formula      TEXT    NOT NULL,
            install_count INTEGER NOT NULL,
            percent      REAL    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_formula_installs_run
            ON formula_installs(run_id);
        CREATE INDEX IF NOT EXISTS idx_formula_installs_formula
            ON formula_installs(formula);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def fetch_data(url: str) -> dict:
    print(f"  Fetching {url} ...")
    with urllib.request.urlopen(url, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def parse_count(value: str) -> int:
    """Convert '488,091' → 488091."""
    return int(value.replace(",", ""))


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------

def insert_run(conn: sqlite3.Connection, data: dict, url: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO analytics_runs
            (fetched_at, category, start_date, end_date,
             total_items, total_count, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            data["category"],
            data["start_date"],
            data["end_date"],
            data["total_items"],
            data["total_count"],
            url,
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_items(conn: sqlite3.Connection, run_id: int, items: list) -> int:
    rows = [
        (
            run_id,
            item["number"],
            item["formula"],
            parse_count(item["count"]),
            float(item["percent"]),
        )
        for item in items
    ]
    conn.executemany(
        """
        INSERT INTO formula_installs
            (run_id, rank, formula, install_count, percent)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_summary(conn: sqlite3.Connection, run_id: int) -> None:
    run = conn.execute(
        "SELECT * FROM analytics_runs WHERE id = ?", (run_id,)
    ).fetchone()
    top = conn.execute(
        """
        SELECT rank, formula, install_count, percent
        FROM   formula_installs
        WHERE  run_id = ?
        ORDER  BY rank
        LIMIT  10
        """,
        (run_id,),
    ).fetchall()

    print("\n" + "=" * 55)
    print("  Homebrew Analytics – Top 10 Formulae (30 days)")
    print("=" * 55)
    print(f"  Period : {run['start_date']}  →  {run['end_date']}")
    print(f"  Total  : {run['total_count']:,} installs across "
          f"{run['total_items']:,} formulae")
    print("-" * 55)
    print(f"  {'#':>3}  {'Formula':<30} {'Installs':>10}  {'%':>5}")
    print("-" * 55)
    for row in top:
        print(f"  {row['rank']:>3}  {row['formula']:<30} "
              f"{row['install_count']:>10,}  {row['percent']:>5.2f}")
    print("=" * 55)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Homebrew analytics and store in SQLite."
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"Analytics JSON endpoint (default: {DEFAULT_URL})"
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB,
        help=f"SQLite database file (default: {DEFAULT_DB})"
    )
    args = parser.parse_args()

    print("\n🍺  Homebrew Analytics → SQLite")
    print(f"   Database : {args.db}")

    conn = get_connection(args.db)
    create_tables(conn)

    data   = fetch_data(args.url)
    run_id = insert_run(conn, data, args.url)
    count  = insert_items(conn, run_id, data["items"])

    print(f"  ✓ Inserted run #{run_id} with {count:,} formula rows.")
    print_summary(conn, run_id)
    conn.close()
    print(f"\n  Database saved to: {args.db}\n")


if __name__ == "__main__":
    main()
