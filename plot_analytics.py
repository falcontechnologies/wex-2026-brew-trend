#!/usr/bin/env python3
"""
Homebrew Analytics Visualizer
==============================
Reads data from homebrew_analytics.db and generates matplotlib charts:
  1. Line chart - install count trends over time per package
  2. Bar chart  - latest snapshot rankings
"""

import sqlite3
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from collections import defaultdict

DATABASE_FILE = "homebrew_analytics.db"


def load_data():
    """
    Load one snapshot per day (the latest one for that date) to avoid
    duplicate entries from the same day inflating the trend line.
    """
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    # One snapshot per date (latest id wins)
    cursor.execute('''
        SELECT s.date, p.package_name, p.install_count, p.rank
        FROM packages p
        JOIN snapshots s ON p.snapshot_id = s.id
        WHERE s.id IN (
            SELECT MAX(id) FROM snapshots GROUP BY date
        )
        ORDER BY s.date, p.rank
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows


def build_series(rows):
    """Organise rows into {package: [(date, count), ...]} and latest snapshot list."""
    series = defaultdict(list)
    latest_date = None
    latest_packages = []

    dates_seen = set()
    for date, name, count, rank in rows:
        series[name].append((date, count))
        if date not in dates_seen:
            dates_seen.add(date)
        latest_date = date

    # Latest snapshot packages in rank order
    latest_packages = [
        (name, count, rank)
        for date, name, count, rank in rows
        if date == latest_date
    ]
    latest_packages.sort(key=lambda x: x[2])  # sort by rank

    return series, latest_packages, sorted(dates_seen)


def plot(rows):
    series, latest_packages, all_dates = build_series(rows)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle("Homebrew Analytics Dashboard", fontsize=15, fontweight="bold")

    # ── Chart 1: Line chart - trends over time ────────────────────────────────
    colors = plt.cm.tab10.colors
    for i, (package, points) in enumerate(sorted(series.items())):
        dates = [p[0] for p in points]
        counts = [p[1] for p in points]
        ax1.plot(dates, counts, marker="o", label=package, color=colors[i % len(colors)], linewidth=2)

    ax1.set_title("Install Count Trend (30-day window, per day)")
    ax1.set_xlabel("Date")
    ax1.set_ylabel("Install Count")
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax1.legend(fontsize=9)
    ax1.tick_params(axis="x", rotation=30)
    ax1.grid(True, linestyle="--", alpha=0.5)

    # ── Chart 2: Bar chart - latest snapshot ──────────────────────────────────
    names = [p[0] for p in latest_packages]
    counts = [p[1] for p in latest_packages]
    bar_colors = [colors[list(series.keys()).index(n) % len(colors)] for n in names]

    bars = ax2.barh(names[::-1], counts[::-1], color=bar_colors[::-1])
    ax2.set_title(f"Latest Snapshot Rankings\n({all_dates[-1]})")
    ax2.set_xlabel("Install Count")
    ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax2.grid(True, axis="x", linestyle="--", alpha=0.5)

    # Add count labels on bars
    for bar, count in zip(bars, counts[::-1]):
        ax2.text(
            bar.get_width() + 1000,
            bar.get_y() + bar.get_height() / 2,
            f"{count:,}",
            va="center", fontsize=9
        )

    plt.tight_layout()
    plt.savefig("homebrew_analytics.png", dpi=150, bbox_inches="tight")
    print("Chart saved to homebrew_analytics.png")
    plt.show()


if __name__ == "__main__":
    rows = load_data()
    if not rows:
        print("No data found in the database. Run run_collection.py first.")
    else:
        plot(rows)
