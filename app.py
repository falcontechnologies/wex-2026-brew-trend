from flask import Flask, render_template_string, request
import sqlite3
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import base64

app = Flask(__name__)
DB_PATH = "brew_data.db"


def get_latest_snapshot_chart():
    """
    Reads the most recent snapshot and returns a bar chart
    of the top 10 packages as a base64 image.
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

    # Get top 10 only — cleaner for visualisation
    cursor.execute("""
        SELECT formula, count FROM installations
        WHERE snapshot_id = ?
        ORDER BY count DESC
        LIMIT 10
    """, (snapshot_id,))

    rows = cursor.fetchall()
    conn.close()

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


def get_trend_chart():
    """
    Builds a line chart showing how the top 10 packages' install
    counts have changed across all snapshot dates we have collected.

    The approach:
    1. Find which 10 packages ranked highest in the most recent snapshot
    2. For each of those packages, look up their count on every date we have
    3. Plot each package as a separate line across all dates
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Step 1 — Get all snapshot dates in chronological order
    cursor.execute("""
        SELECT id, run_date FROM snapshots
        ORDER BY run_date ASC
    """)
    snapshots = cursor.fetchall()

    if len(snapshots) < 2:
        conn.close()
        return None, "Need at least 2 snapshots to show a trend. Check back tomorrow!"

    # Step 2 — Identify the top 10 packages from the most recent snapshot
    # We use the most recent snapshot to decide WHICH packages to track
    latest_snapshot_id = snapshots[-1][0]

    cursor.execute("""
        SELECT formula FROM installations
        WHERE snapshot_id = ?
        ORDER BY count DESC
        LIMIT 10
    """, (latest_snapshot_id,))

    top_10_formulas = [row[0] for row in cursor.fetchall()]

    # Step 3 — For each of those packages, get their count on every date
    # We build a dictionary: { formula: { date: count } }
    # This lets us look up any package's count on any date easily
    trend_data = {formula: {} for formula in top_10_formulas}

    for snapshot_id, run_date in snapshots:
        cursor.execute("""
            SELECT formula, count FROM installations
            WHERE snapshot_id = ?
            AND formula IN ({})
            ORDER BY count DESC
        """.format(",".join("?" * len(top_10_formulas))),
        [snapshot_id] + top_10_formulas)

        for formula, count in cursor.fetchall():
            trend_data[formula][run_date] = count

    conn.close()

    # Step 4 — Build the line chart
    all_dates = [snap[1] for snap in snapshots]

    plt.figure(figsize=(14, 7))

    for formula in top_10_formulas:
        # For each date, get the count — use None if missing for that date
        counts = [trend_data[formula].get(date) for date in all_dates]
        plt.plot(all_dates, counts, marker="o", label=formula)

    plt.xlabel("Snapshot Date")
    plt.ylabel("Install Events (rolling 30 days)")
    plt.title("Top 10 Homebrew Packages — Trend Over Time")

    # Rotate date labels so they don't overlap
    plt.xticks(rotation=45, ha="right")

    # Add a legend so we know which line is which package
    plt.legend(loc="upper left", fontsize=8)
    plt.tight_layout()

    buffer = io.BytesIO()
    plt.savefig(buffer, format="png")
    buffer.seek(0)
    plt.close()

    trend_base64 = base64.b64encode(buffer.read()).decode("utf-8")
    return trend_base64, None


def get_all_snapshot_dates():
    """Returns all snapshot dates for the info panel."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, run_date, run_time FROM snapshots
        ORDER BY run_date DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows


@app.route("/")
def index():
    image_base64, latest_date, error = get_latest_snapshot_chart()
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
            }
            h1 { color: #333; }
            h2 { color: #555; margin-top: 40px; }
            p  { color: #666; }
            img {
                max-width: 100%;
                border: 1px solid #ddd;
                background: white;
                padding: 10px;
                margin-bottom: 10px;
            }
            .error { color: red; font-weight: bold; }
            .info  { color: #888; font-style: italic; }
            .snapshot-list {
                display: inline-block;
                text-align: left;
                margin-top: 30px;
            }
            .snapshot-list ul {
                list-style: none;
                padding: 0;
            }
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

        <!-- SECTION 1: Latest snapshot bar chart -->
        <h2>Latest Snapshot — Top 10 Packages</h2>
        {% if error %}
            <p class="error">{{ error }}</p>
        {% else %}
            <p>Most recent data: <strong>{{ latest_date }}</strong></p>
            <img src="data:image/png;base64,{{ image_base64 }}">
        {% endif %}

        <!-- SECTION 2: Trend line chart -->
        <h2>Trend Over Time — Top 10 Packages</h2>
        {% if trend_error %}
            <p class="info">{{ trend_error }}</p>
        {% else %}
            <p>Showing how install counts have changed across all collected snapshots.</p>
            <img src="data:image/png;base64,{{ trend_base64 }}">
        {% endif %}

        <!-- SECTION 3: Snapshot history -->
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
        image_base64=image_base64,
        latest_date=latest_date,
        error=error,
        trend_base64=trend_base64,
        trend_error=trend_error,
        all_snapshots=all_snapshots
    )


if __name__ == "__main__":
    app.run(debug=True)