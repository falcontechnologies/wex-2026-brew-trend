#!/usr/bin/env python3
"""
Flask web dashboard for Homebrew Analytics.

Usage:
    pip3.11 install flask
    python3.11 app.py --port 8080
    Then open http://localhost:5000 in your browser.
"""

import sqlite3
import argparse
from flask import Flask, jsonify, render_template, request

DEFAULT_DB = "brew_analytics.db"

app = Flask(__name__)
db_path = DEFAULT_DB


def get_connection():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/runs")
def api_runs():
    """Return all analytics runs."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, end_date, total_items, total_count FROM analytics_runs ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/top")
def api_top():
    """
    Return top N formulae for the latest run.
    Query params:
        n       — number of results (default 10)
        run_id  — specific run (default latest)
    """
    n      = int(request.args.get("n", 10))
    run_id = request.args.get("run_id")

    conn = get_connection()
    if not run_id:
        row = conn.execute("SELECT MAX(id) AS id FROM analytics_runs").fetchone()
        run_id = row["id"]

    rows = conn.execute(
        """
        SELECT rank, formula, install_count, percent
        FROM   formula_installs
        WHERE  run_id = ?
        ORDER  BY rank
        LIMIT  ?
        """,
        (run_id, n),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/trending")
def api_trending():
    """
    Return top trending formulae by delta_percent for the latest date.
    Query params:
        n           — number of results (default 10)
        min_installs — minimum install count filter (default 1000)
        date        — specific date (default latest)
    """
    n            = int(request.args.get("n", 10))
    min_installs = int(request.args.get("min_installs", 1000))
    date         = request.args.get("date")

    conn = get_connection()
    if not date:
        row = conn.execute("SELECT MAX(date) AS date FROM formula_deltas").fetchone()
        date = row["date"] if row else None

    if not date:
        conn.close()
        return jsonify([])

    rows = conn.execute(
        """
        SELECT formula, install_count, delta_count, delta_percent
        FROM   formula_deltas
        WHERE  date         = ?
          AND  is_new       = 0
          AND  install_count >= ?
        ORDER  BY delta_percent DESC
        LIMIT  ?
        """,
        (date, min_installs, n),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/trend/<formula>")
def api_formula_trend(formula):
    """Return historical delta data for a single formula."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT date, install_count, delta_count, delta_percent
        FROM   formula_deltas
        WHERE  formula = ?
        ORDER  BY date
        """,
        (formula,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/search")
def api_search():
    """
    Search for formulae by name prefix.
    Query params:
        q — search query
    """
    q = request.args.get("q", "")
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT DISTINCT formula
        FROM   formula_installs
        WHERE  formula LIKE ?
        ORDER  BY formula
        LIMIT  20
        """,
        (f"{q}%",),
    ).fetchall()
    conn.close()
    return jsonify([r["formula"] for r in rows])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Homebrew Analytics Dashboard")
    parser.add_argument("--db",   default=DEFAULT_DB, help="Path to SQLite database")
    parser.add_argument("--port", default=5000,       type=int, help="Port to run on")
    parser.add_argument("--debug", action="store_true", help="Run in debug mode")
    args = parser.parse_args()

    db_path = args.db
    print(f"\n🍺  Brew Analytics Dashboard")
    print(f"   Database : {db_path}")
    print(f"   Open     : http://localhost:{args.port}\n")
    app.run(host="0.0.0.0", port=args.port, debug=args.debug)
