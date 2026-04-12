from flask import Flask, render_template_string
import sqlite3
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import base64
import os
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURATION ---
# Keeping paths and constants at the top makes them easy to change later
# without hunting through the code
DB_PATH = "brew_data.db"
TRENDS_FOLDER = "daily_trends"

# Make sure the daily_trends folder exists when the app starts.
# os.makedirs with exist_ok=True means: create it if it isn't there,
# but don't crash if it already exists.
os.makedirs(TRENDS_FOLDER, exist_ok=True)


# =============================================================================
# SECTION 1 — LATEST SNAPSHOT BAR CHART (top 10 by total installs)
# This is the same as before — shows the most popular packages right now.
# =============================================================================

def get_latest_snapshot_chart():
    """
    Reads the most recent snapshot from the database and returns
    a bar chart of the top 10 packages by raw install count.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT MAX(run_date) FROM snapshots")
    latest_date = cursor.fetchone()[0]

    if not latest_date:
        conn.close()
        return None, None, "No snapshots in database yet."

    cursor.execute(
        "SELECT id, run_time FROM snapshots WHERE run_date = ?",
        (latest_date,)
    )
    row = cursor.fetchone()
    snapshot_id, run_time = row[0], row[1]

    cursor.execute("""
        SELECT formula, count FROM installations
        WHERE snapshot_id = ?
        ORDER BY count DESC
        LIMIT 10
    """, (snapshot_id,))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return None, latest_date, "No installation data found."

    names = [row[0] for row in rows]
    counts = [row[1] for row in rows]

    plt.figure(figsize=(12, 6))
    plt.bar(range(len(names)), counts, color="steelblue")
    plt.xticks(range(len(names)), names, rotation=45, ha="right")
    plt.xlabel("Package Name")
    plt.ylabel("Install Events (rolling 30 days)")
    plt.title(f"Top 10 Most Installed Homebrew Packages — {latest_date}")
    plt.tight_layout()

    buffer = io.BytesIO()
    plt.savefig(buffer, format="png")
    buffer.seek(0)
    plt.close()

    image_base64 = base64.b64encode(buffer.read()).decode("utf-8")
    return image_base64, latest_date, None


# =============================================================================
# SECTION 2 — DAILY TREND CHART (top 10 most DYNAMIC packages)
#
# This is the new feature Paul asked for. Instead of showing which packages
# are most popular, it shows which packages CHANGED THE MOST between the
# two most recent snapshots.
#
# The logic:
# 1. Get the two most recent snapshot IDs
# 2. For each package, calculate: change = today's count - yesterday's count
# 3. Rank packages by the absolute size of that change (biggest movers first)
# 4. Plot the top 10 as a horizontal bar chart — positive = growing, negative = shrinking
# 5. Save the chart as a .png in the daily_trends folder
# =============================================================================

def get_daily_change_chart():
    """
    Calculates which packages changed the most between the two most
    recent snapshots, plots them, saves to daily_trends/, and returns
    the chart as a base64 string for the webpage.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get the two most recent snapshots, newest first
    cursor.execute("""
        SELECT id, run_date FROM snapshots
        ORDER BY run_date DESC
        LIMIT 2
    """)
    recent = cursor.fetchall()

    # We need at least 2 snapshots to calculate a change
    if len(recent) < 2:
        conn.close()
        return None, None, None, "Need at least 2 snapshots to calculate daily changes. Check back tomorrow!"

    # Unpack — recent[0] is today, recent[1] is yesterday
    today_id, today_date = recent[0]
    yesterday_id, yesterday_date = recent[1]

    # Fetch all packages from today's snapshot into a dictionary:
    # { formula: count }
    cursor.execute("""
        SELECT formula, count FROM installations
        WHERE snapshot_id = ?
    """, (today_id,))
    today_data = {row[0]: row[1] for row in cursor.fetchall()}

    # Fetch all packages from yesterday's snapshot into a dictionary:
    # { formula: count }
    cursor.execute("""
        SELECT formula, count FROM installations
        WHERE snapshot_id = ?
    """, (yesterday_id,))
    yesterday_data = {row[0]: row[1] for row in cursor.fetchall()}

    conn.close()

    # Calculate the change for every package that appears in both snapshots.
    # We skip packages that only appear in one snapshot to avoid bad data.
    changes = []
    for formula in today_data:
        if formula in yesterday_data:
            change = today_data[formula] - yesterday_data[formula]
            changes.append((formula, change))

    if not changes:
        return None, today_date, yesterday_date, "No overlapping packages found between snapshots."

    # Sort by absolute change — biggest movers first, regardless of direction
    # abs() gives us the magnitude: abs(-5000) = 5000, abs(+5000) = 5000
    changes.sort(key=lambda x: abs(x[1]), reverse=True)

    # Take only the top 10 most dynamic packages
    top_10 = changes[:10]

    names = [item[0] for item in top_10]
    values = [item[1] for item in top_10]

    # Colour bars green if growing, red if shrinking — makes it instantly readable
    colors = ["green" if v >= 0 else "crimson" for v in values]

    # Horizontal bar chart works better here because package names are long
    # and changes can be positive or negative — the zero line becomes a clear
    # visual anchor in the middle
    plt.figure(figsize=(12, 7))
    plt.barh(range(len(names)), values, color=colors)
    plt.yticks(range(len(names)), names)
    plt.xlabel("Change in Install Events")
    plt.ylabel("Package")
    plt.title(f"Top 10 Most Dynamic Packages\n{yesterday_date} → {today_date}")

    # Draw a vertical line at zero so the direction of change is clear
    plt.axvline(x=0, color="black", linewidth=0.8, linestyle="--")
    plt.tight_layout()

    # --- Save to daily_trends folder as a .png ---
    # Named by today's date so every day gets its own permanent file
    filename = f"trend_{today_date}.png"
    filepath = os.path.join(TRENDS_FOLDER, filename)
    plt.savefig(filepath)

    # Also encode as base64 for the webpage
    buffer = io.BytesIO()
    plt.savefig(buffer, format="png")
    buffer.seek(0)
    plt.close()

    trend_base64 = base64.b64encode(buffer.read()).decode("utf-8")
    return trend_base64, today_date, yesterday_date, None


# =============================================================================
# SECTION 3 — TREND OVER TIME LINE CHART (unchanged from before)
# Shows how the top 10 popular packages have moved across ALL snapshots.
# =============================================================================

def get_trend_chart():
    """
    Line chart showing how the top 10 packages by popularity have
    changed across all collected snapshots over time.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, run_date FROM snapshots
        ORDER BY run_date ASC
    """)
    snapshots = cursor.fetchall()

    if len(snapshots) < 2:
        conn.close()
        return None, "Need at least 2 snapshots to show a trend."

    latest_snapshot_id = snapshots[-1][0]

    cursor.execute("""
        SELECT formula FROM installations
        WHERE snapshot_id = ?
        ORDER BY count DESC
        LIMIT 10
    """, (latest_snapshot_id,))
    top_10_formulas = [row[0] for row in cursor.fetchall()]

    trend_data = {formula: {} for formula in top_10_formulas}

    for snapshot_id, run_date in snapshots:
        cursor.execute("""
            SELECT formula, count FROM installations
            WHERE snapshot_id = ?
            AND formula IN ({})
        """.format(",".join("?" * len(top_10_formulas))),
        [snapshot_id] + top_10_formulas)

        for formula, count in cursor.fetchall():
            trend_data[formula][run_date] = count

    conn.close()

    all_dates = [snap[1] for snap in snapshots]

    plt.figure(figsize=(14, 7))
    for formula in top_10_formulas:
        counts = [trend_data[formula].get(date) for date in all_dates]
        plt.plot(all_dates, counts, marker="o", label=formula)

    plt.xlabel("Snapshot Date")
    plt.ylabel("Install Events (rolling 30 days)")
    plt.title("Top 10 Homebrew Packages — Cumulative Trend Over Time")
    plt.xticks(rotation=45, ha="right")
    plt.legend(loc="upper left", fontsize=8)
    plt.tight_layout()

    buffer = io.BytesIO()
    plt.savefig(buffer, format="png")
    buffer.seek(0)
    plt.close()

    trend_base64 = base64.b64encode(buffer.read()).decode("utf-8")
    return trend_base64, None


# =============================================================================
# SECTION 4 — SNAPSHOT HISTORY PANEL
# =============================================================================

def get_all_snapshot_dates():
    """Returns all snapshot dates for the info panel, newest first."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, run_date, run_time FROM snapshots
        ORDER BY run_date DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


# =============================================================================
# SECTION 5 — FLASK ROUTES
# =============================================================================

@app.route("/")
def index():
    # Gather all chart data
    bar_base64, latest_date, bar_error = get_latest_snapshot_chart()
    change_base64, today_date, yesterday_date, change_error = get_daily_change_chart()
    trend_base64, trend_error = get_trend_chart()
    all_snapshots = get_all_snapshot_dates()

    html = """
    <html>
    <head>
        <title>Homebrew Analytics Dashboard</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                text-align: center;
                padding: 20px;
                background: #f5f5f5;
                color: #333;
            }
            h1 { color: #222; }
            h2 { color: #555; margin-top: 50px; border-top: 1px solid #ddd; padding-top: 20px; }
            p  { color: #666; }
            img {
                max-width: 90%;
                border: 1px solid #ddd;
                background: white;
                padding: 10px;
                margin-bottom: 10px;
                border-radius: 4px;
            }
            .error { color: red; font-weight: bold; }
            .info  { color: #888; font-style: italic; }
            .label {
                display: inline-block;
                background: #e8f4f8;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                color: #555;
                margin-bottom: 8px;
            }
            .snapshot-list {
                display: inline-block;
                text-align: left;
                margin-top: 30px;
            }
            .snapshot-list ul { list-style: none; padding: 0; }
            .snapshot-list li {
                padding: 4px 10px;
                background: white;
                margin: 4px 0;
                border-radius: 4px;
                font-size: 14px;
                color: #444;
            }
        </style>
    </head>
    <body>
        <h1>🍺 Homebrew Analytics Dashboard</h1>

        <!-- CHART 1: Latest snapshot top 10 by popularity -->
        <h2>Latest Snapshot — Top 10 by Popularity</h2>
        {% if bar_error %}
            <p class="error">{{ bar_error }}</p>
        {% else %}
            <p>Most recent snapshot: <strong>{{ latest_date }}</strong></p>
            <img src="data:image/png;base64,{{ bar_base64 }}">
        {% endif %}

        <!-- CHART 2: Daily change — most dynamic packages -->
        <h2>Daily Movement — Top 10 Most Dynamic Packages</h2>
        {% if change_error %}
            <p class="info">{{ change_error }}</p>
        {% else %}
            <p>
                Comparing <strong>{{ yesterday_date }}</strong>
                → <strong>{{ today_date }}</strong>.
                Green = growing, Red = shrinking.
            </p>
            <img src="data:image/png;base64,{{ change_base64 }}">
            <br>
            <span class="label">📁 Chart also saved to daily_trends/trend_{{ today_date }}.png</span>
        {% endif %}

        <!-- CHART 3: Cumulative trend over all snapshots -->
        <h2>Cumulative Trend — Top 10 Over All Snapshots</h2>
        {% if trend_error %}
            <p class="info">{{ trend_error }}</p>
        {% else %}
            <p>Tracking how install counts have shifted across every snapshot collected.</p>
            <img src="data:image/png;base64,{{ trend_base64 }}">
        {% endif %}

        <!-- SNAPSHOT HISTORY -->
        <div class="snapshot-list">
            <h2>Snapshots Collected ({{ all_snapshots|length }} total)</h2>
            <ul>
            {% for snap in all_snapshots %}
                <li>Snapshot #{{ snap[0] }} — {{ snap[1] }} at {{ snap[2] }}</li>
            {% endfor %}
            </ul>
        </div>

    </body>
    </html>
    """

    return render_template_string(
        html,
        bar_base64=bar_base64,
        latest_date=latest_date,
        bar_error=bar_error,
        change_base64=change_base64,
        today_date=today_date,
        yesterday_date=yesterday_date,
        change_error=change_error,
        trend_base64=trend_base64,
        trend_error=trend_error,
        all_snapshots=all_snapshots
    )


if __name__ == "__main__":
    app.run(debug=True)