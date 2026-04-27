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
import ssl
import urllib.request
from datetime import datetime, timezone

import certifi

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

        -- Stores the day-over-day change for each formula.
        -- Calculated once per day after the fetch, not on the fly.
        CREATE TABLE IF NOT EXISTS formula_deltas (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id             INTEGER NOT NULL REFERENCES analytics_runs(id),
            formula            TEXT    NOT NULL,
            date               TEXT    NOT NULL,
            install_count      INTEGER NOT NULL,
            prev_install_count INTEGER,
            delta_count        INTEGER NOT NULL,
            delta_percent      REAL,
            is_new             INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_formula_deltas_formula
            ON formula_deltas(formula);
        CREATE INDEX IF NOT EXISTS idx_formula_deltas_date
            ON formula_deltas(date);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def fetch_data(url: str) -> dict:
    print(f"  Fetching {url} ...")
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, timeout=30, context=ssl_ctx) as resp:
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
# Delta calculation
# ---------------------------------------------------------------------------

def calculate_and_insert_deltas(conn: sqlite3.Connection, run_id: int) -> int:
    """
    For each formula in today's run, look up yesterday's install_count
    using a SQL self-join on formula_installs. Take the difference and
    store it in formula_deltas.

    How the SQL works:
      - 'today'     = rows from the current run_id
      - 'yesterday' = rows from the most recent run before today
      - LEFT JOIN   = keeps today's row even if no yesterday row exists
                      (that's how we detect new formulae)
    """

    # First find the previous run id (the most recent run before this one)
    prev_run = conn.execute(
        """
        SELECT id, end_date
        FROM   analytics_runs
        WHERE  id < ?
        ORDER  BY id DESC
        LIMIT  1
        """,
        (run_id,),
    ).fetchone()

    today_date = conn.execute(
        "SELECT end_date FROM analytics_runs WHERE id = ?", (run_id,)
    ).fetchone()["end_date"]

    if prev_run is None:
        # No previous run exists yet — mark every formula as new
        rows = conn.execute(
            """
            SELECT formula, install_count
            FROM   formula_installs
            WHERE  run_id = ?
            """,
            (run_id,),
        ).fetchall()

        delta_rows = [
            (run_id, row["formula"], today_date,
             row["install_count"], None,
             row["install_count"], None, 1)
            for row in rows
        ]
    else:
        # Use a LEFT JOIN to pair today's counts with yesterday's counts.
        # LAG() would be ideal here but SQLite supports it — using a join
        # instead for clarity.
        #
        # For each formula in today's run:
        #   - delta_count   = today - yesterday (or today if brand new)
        #   - delta_percent = (delta / yesterday) * 100
        #   - is_new        = 1 if no yesterday row existed
        rows = conn.execute(
            """
            SELECT
                today.formula,
                today.install_count                          AS curr_count,
                yesterday.install_count                      AS prev_count,
                today.install_count - COALESCE(yesterday.install_count, 0)
                                                             AS delta_count,
                CASE
                    WHEN yesterday.install_count IS NULL THEN NULL
                    ELSE ROUND(
                        (today.install_count - yesterday.install_count) * 100.0
                        / yesterday.install_count, 4)
                END                                          AS delta_percent,
                CASE WHEN yesterday.install_count IS NULL THEN 1 ELSE 0 END
                                                             AS is_new
            FROM formula_installs AS today
            LEFT JOIN formula_installs AS yesterday
                   ON today.formula   = yesterday.formula
                  AND yesterday.run_id = ?
            WHERE today.run_id = ?
            """,
            (prev_run["id"], run_id),
        ).fetchall()

        delta_rows = [
            (run_id, row["formula"], today_date,
             row["curr_count"], row["prev_count"],
             row["delta_count"], row["delta_percent"], row["is_new"])
            for row in rows
        ]

    conn.executemany(
        """
        INSERT INTO formula_deltas
            (run_id, formula, date, install_count, prev_install_count,
             delta_count, delta_percent, is_new)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        delta_rows,
    )
    conn.commit()
    return len(delta_rows)


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

    # Top 10 movers by percentage change (exclude new formulae, min 1000 installs)
    movers = conn.execute(
        """
        SELECT   formula, delta_count, delta_percent, install_count
        FROM     formula_deltas
        WHERE    run_id      = ?
          AND    is_new      = 0
          AND    install_count >= 1000
        ORDER BY delta_percent DESC
        LIMIT    10
        """,
        (run_id,),
    ).fetchall()

    if movers:
        print("\n  Top 10 Rising Formulae (% change, min 1,000 installs)")
        print("=" * 55)
        print(f"  {'Formula':<30} {'Δ Count':>10}  {'Δ %':>7}")
        print("-" * 55)
        for row in movers:
            sign = "+" if row["delta_count"] >= 0 else ""
            print(f"  {row['formula']:<30} "
                  f"{sign}{row['delta_count']:>9,}  "
                  f"{sign}{row['delta_percent']:>6.2f}%")
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

    delta_count = calculate_and_insert_deltas(conn, run_id)
    print(f"  ✓ Calculated {delta_count:,} daily deltas.")

    print_summary(conn, run_id)
    conn.close()
    print(f"\n  Database saved to: {args.db}\n")


if __name__ == "__main__":
    main()