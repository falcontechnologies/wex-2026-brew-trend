#!/usr/bin/env python3
"""
Generate two charts from brew_analytics.db:
  1. Bar chart  — Top 10 formulae by installs (latest run)
  2. Line chart — Installs over time for a set of tracked formulae

Usage:
    python3.11 brew_charts.py
    python3.11 brew_charts.py --db ~/path/to/brew_analytics.db
    python3.11 brew_charts.py --track node git curl wget
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

DEFAULT_DB      = "brew_analytics.db"
DEFAULT_TRACK   = ["node", "git", "curl", "wget", "python@3.13"]
OUTPUT_BAR      = "brew_top10_bar.png"
OUTPUT_LINE     = "brew_trends_line.png"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def latest_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(id) AS id FROM analytics_runs").fetchone()
    if row["id"] is None:
        raise RuntimeError("No runs found in the database. Run brew_analytics_to_sqlite.py first.")
    return row["id"]


def fetch_top10(conn: sqlite3.Connection, run_id: int) -> list:
    return conn.execute(
        """
        SELECT formula, install_count
        FROM   formula_installs
        WHERE  run_id = ?
        ORDER  BY rank
        LIMIT  10
        """,
        (run_id,),
    ).fetchall()


def fetch_run_date(conn: sqlite3.Connection, run_id: int) -> str:
    row = conn.execute(
        "SELECT end_date FROM analytics_runs WHERE id = ?", (run_id,)
    ).fetchone()
    return row["end_date"]


def fetch_trends(conn: sqlite3.Connection, formulae: list) -> dict:
    """
    Returns {formula: [(end_date, install_count), ...]} for all runs.
    """
    placeholders = ",".join("?" * len(formulae))
    rows = conn.execute(
        f"""
        SELECT r.end_date, f.formula, f.install_count
        FROM   formula_installs f
        JOIN   analytics_runs r ON r.id = f.run_id
        WHERE  f.formula IN ({placeholders})
        ORDER  BY r.end_date
        """,
        formulae,
    ).fetchall()

    trends: dict = {formula: [] for formula in formulae}
    for row in rows:
        trends[row["formula"]].append((row["end_date"], row["install_count"]))
    return trends


# ---------------------------------------------------------------------------
# Chart 1: Bar chart — Top 10
# ---------------------------------------------------------------------------

def plot_bar(conn: sqlite3.Connection, run_id: int, output_path: str) -> None:
    rows     = fetch_top10(conn, run_id)
    end_date = fetch_run_date(conn, run_id)

    formulae = [r["formula"] for r in rows]
    counts   = [r["install_count"] for r in rows]

    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.barh(formulae[::-1], counts[::-1], color="#f97316", edgecolor="white")

    # Value labels on bars
    for bar, count in zip(bars, counts[::-1]):
        ax.text(
            bar.get_width() + max(counts) * 0.01,
            bar.get_y() + bar.get_height() / 2,
            f"{count:,}",
            va="center", ha="left", fontsize=9, color="#374151"
        )

    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.set_xlabel("Installs", fontsize=11)
    ax.set_title(f"Top 10 Homebrew Formulae — 30 days ending {end_date}", fontsize=13, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"  ✓ Bar chart saved → {output_path}")


# ---------------------------------------------------------------------------
# Chart 2: Line chart — Trends over time
# ---------------------------------------------------------------------------

def plot_line(conn: sqlite3.Connection, formulae: list, output_path: str) -> None:
    trends = fetch_trends(conn, formulae)

    # Filter out formulae with no data
    found = {f: data for f, data in trends.items() if data}
    if not found:
        print("  ⚠ No trend data found for the tracked formulae. "
              "Run the fetch script more than once to build up history.")
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    colors = ["#3b82f6", "#f97316", "#10b981", "#8b5cf6", "#ef4444",
              "#f59e0b", "#06b6d4", "#84cc16"]

    for i, (formula, data) in enumerate(found.items()):
        dates  = [d[0] for d in data]
        counts = [d[1] for d in data]
        ax.plot(dates, counts, marker="o", label=formula,
                color=colors[i % len(colors)], linewidth=2, markersize=5)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.set_ylim(bottom=min(c for d in found.values() for _, c in d) * 0.90)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=10))
    ax.margins(x=0.05)
    ax.set_xlabel("Date", fontsize=11)
    ax.set_ylabel("Installs", fontsize=11)
    ax.set_title("Homebrew Formula Install Trends Over Time", fontsize=13, fontweight="bold")
    ax.legend(loc="upper left", fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"  ✓ Line chart saved → {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate charts from brew analytics SQLite db.")
    parser.add_argument("--db",    default=DEFAULT_DB,    help="Path to SQLite database")
    parser.add_argument("--track", nargs="+", default=DEFAULT_TRACK,
                        help="Formulae to track in the line chart")
    args = parser.parse_args()

    print("\n📊  Brew Analytics Charts")
    print(f"   Database : {args.db}\n")

    conn   = get_connection(args.db)
    run_id = latest_run_id(conn)

    plot_bar(conn, run_id, OUTPUT_BAR)
    plot_line(conn, args.track, OUTPUT_LINE)

    conn.close()
    print(f"\n  Done! Open {OUTPUT_BAR} and {OUTPUT_LINE} to view your charts.\n")


if __name__ == "__main__":
    main()
