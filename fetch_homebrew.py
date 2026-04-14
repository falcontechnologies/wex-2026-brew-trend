import requests
import sqlite3
import logging
from datetime import datetime
import matplotlib.pyplot as plt
import io
import base64
import os

TRENDS_FOLDER = "daily_trends"

# --- LOGGING SETUP ---
# Logs every run to a text file so you can see what happened
# even when Task Scheduler runs it silently in the background
logging.basicConfig(
    filename="brew_log.txt",
    level=logging.INFO,
    format="%(asctime)s — %(message)s"
)

URL = "https://formulae.brew.sh/api/analytics/install/30d.json"
DB_PATH = "brew_data.db"


def setup_database(cursor):
    """
    Creates both tables if they don't already exist.
    This runs every time but only actually creates tables once.
    """

    # snapshots table — one row per run of this script
    # stores metadata ABOUT the run, not the actual package data
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date    TEXT NOT NULL,
            run_time    TEXT NOT NULL,
            category    TEXT,
            total_count INTEGER,
            start_date  TEXT,
            end_date    TEXT
        )
    """)

    # installations table — one row per package per run
    # snapshot_id is the foreign key — it links each row back
    # to the specific run in the snapshots table it came from
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS installations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            rank        INTEGER,
            formula     TEXT,
            count       INTEGER,
            percent     REAL,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        )
    """)


def already_ran_today(cursor, today):
    """
    Checks whether we already have a snapshot for today's date.
    Returns True if yes (skip the run), False if no (proceed).
    This is the duplicate guard Paul mentioned.
    """
    cursor.execute(
        "SELECT COUNT(*) FROM snapshots WHERE run_date = ?",
        (today,)
    )
    count = cursor.fetchone()[0]
    return count > 0


def fetch_and_store():
    """
    Main function — fetches data from the Homebrew API
    and stores it across both tables with a proper relationship.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M:%S")

    logging.info("Script started")

    # --- STEP 1: Connect to database ---
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # --- STEP 2: Make sure both tables exist ---
    setup_database(cursor)

    # --- STEP 3: Check if we already ran today ---
    # If yes, exit cleanly — no duplicates
    if already_ran_today(cursor, today):
        logging.info(f"Already ran today ({today}). Skipping.")
        print(f"Already ran today ({today}). Skipping.")
        conn.close()
        return

    # --- STEP 4: Fetch data from the API ---
    logging.info(f"Fetching data from Homebrew API...")
    response = requests.get(URL)

    if response.status_code != 200:
        logging.error(f"API request failed. Status code: {response.status_code}")
        conn.close()
        return

    data = response.json()

    # --- STEP 5: Insert one row into snapshots ---
    # This records the EVENT of running the script.
    # We also store metadata from the API response itself —
    # things like what date range the API data covers.
    cursor.execute("""
        INSERT INTO snapshots (run_date, run_time, category, total_count, start_date, end_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        today,
        current_time,
        data.get("category"),
        data.get("total_count"),
        data.get("start_date"),
        data.get("end_date")
    ))

    # Get the id that was just assigned to this snapshot.
    # This is the value we'll store in every installation row
    # so they all link back to this specific run.
    snapshot_id = cursor.lastrowid
    logging.info(f"Snapshot created with id={snapshot_id} for {today}")

    # --- STEP 6: Insert one row per package into installations ---
    items = data["items"]

    for item in items:
        clean_count = int(item["count"].replace(",", ""))
        clean_percent = float(item["percent"])

        cursor.execute("""
            INSERT INTO installations (snapshot_id, rank, formula, count, percent)
            VALUES (?, ?, ?, ?, ?)
        """, (
            snapshot_id,           # links this row back to the snapshot
            item.get("number"),    # the rank of this package
            item["formula"],
            clean_count,
            clean_percent
        ))

    # --- STEP 7: Save everything and close ---
    conn.commit()
    conn.close()

    logging.info(f"Done. {len(items)} installation rows saved for snapshot {snapshot_id}.")
    print(f"Done. Snapshot {snapshot_id} saved with {len(items)} packages for {today}.")

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


# Run the function
fetch_and_store()