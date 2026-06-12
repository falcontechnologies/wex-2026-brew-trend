# Homebrew Analytics to SQLite

A Python tool that fetches daily Homebrew formula install analytics from the Homebrew API and stores them in a local SQLite database. Includes daily delta calculations, chart generation, and automated scheduling via launchd on macOS.

---

## What it does

- Fetches 30-day rolling install counts for all Homebrew formulae from the official Homebrew API
- Stores the data in a local SQLite database
- Calculates day-over-day changes (deltas) for each formula
- Generates bar and line charts from the stored data
- Can be scheduled to run automatically once per day on macOS

---

## Requirements

- Python 3.11+
- macOS (for launchd scheduling)

Install dependencies:
```bash
pip3.11 install -r requirements.txt
```

---

## Project structure

```
.
├── brew_analytics_to_sqlite.py   # Fetches data and stores it in SQLite
├── brew_archive.py               # Archives old data and vacuums active DB
├── brew_charts.py                # Generates charts from the database
├── app.py                        # Flask web dashboard
├── templates/
│   └── index.html                # Dashboard frontend
├── com.brewanalytics.fetch.plist # launchd schedule config (macOS)
├── requirements.txt              # Python dependencies
└── README.md
```

---

## Database structure

The database contains three tables:

**`analytics_runs`** — one row per fetch, stores metadata about each run:
- `id`, `fetched_at`, `category`, `start_date`, `end_date`, `total_items`, `total_count`, `source_url`

**`formula_installs`** — one row per formula per run, stores the raw install data:
- `id`, `run_id`, `rank`, `formula`, `install_count`, `percent`

**`formula_deltas`** — one row per formula per run, stores day-over-day changes:
- `id`, `run_id`, `formula`, `date`, `install_count`, `prev_install_count`, `delta_count`, `delta_percent`, `is_new`

---

## Usage

### Fetch data manually
```bash
python3.11 brew_analytics_to_sqlite.py
```

### Specify a custom database path
```bash
python3.11 brew_analytics_to_sqlite.py --db ~/path/to/your.db
```

### Use a different time window (30d, 90d, 365d)
```bash
python3.11 brew_analytics_to_sqlite.py --url https://formulae.brew.sh/api/analytics/install/90d.json
```

### Run the web dashboard
```bash
pip3.11 install flask
python3.11 app.py
```
Then open `http://localhost:5000` in your browser.

**Mac gotchas:**
- Use `http://127.0.0.1` or `HTTP://localhost:8080` instead of `http://localhost` — macOS can block localhost access
- Port 5000 is reserved by macOS Control Center, so always run on a different port:
  ```bash
  python3.11 app.py --port 8080
  ```
- Make sure `index.html` is inside a `templates/` folder in the same directory as `app.py` — Flask won't find it otherwise:
  ```
  your-project/
  ├── app.py
  └── templates/
      └── index.html
  ```
- The dashboard only works while `app.py` is running. Press `Ctrl+C` to stop it.

### Archive old data manually
```bash
python3.11 brew_archive.py
```

By default keeps the last 30 days in the active database and moves everything older to `brew_analytics_archive.db`. To keep a different window:
```bash
python3.11 brew_archive.py --days 60
```

This produces two files:
- `brew_top10_bar.png` — bar chart of the top 10 formulae by installs (latest run)
- `brew_trends_line.png` — line chart of install trends over time for tracked formulae

### Track specific formulae in the line chart
```bash
python3.11 brew_charts.py --track node git curl wget python@3.13
```

---

## Automated scheduling (macOS)

The project includes a launchd plist file to run the fetch script automatically at 9am every day.

**1. Check your python3.11 path:**
```bash
which python3.11
```

**2. Update the plist** if the path differs from `/usr/local/bin/python3.11`, and confirm the script and database paths match your machine.

**3. Install and activate the schedule:**
```bash
cp com.brewanalytics.fetch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.brewanalytics.fetch.plist
```

**4. Test it immediately:**
```bash
launchctl start com.brewanalytics.fetch
```

**5. Check the logs:**
```bash
cat brew_analytics.log
cat brew_analytics_error.log
```

**To remove the schedule:**
```bash
launchctl unload ~/Library/LaunchAgents/com.brewanalytics.fetch.plist
```

---

## Useful queries

**All fetch runs:**
```sql
SELECT * FROM analytics_runs;
```

**Top 10 formulae from the latest run:**
```sql
SELECT rank, formula, install_count, percent
FROM formula_installs
WHERE run_id = (SELECT MAX(id) FROM analytics_runs)
ORDER BY rank
LIMIT 10;
```

**Top rising formulae today (min 1,000 installs):**
```sql
SELECT formula, delta_count, delta_percent
FROM formula_deltas
WHERE date = (SELECT MAX(date) FROM formula_deltas)
  AND is_new = 0
  AND install_count >= 1000
ORDER BY delta_percent DESC
LIMIT 10;
```

**Track a formula over time:**
```sql
SELECT date, install_count, delta_count, delta_percent
FROM formula_deltas
WHERE formula = 'node'
ORDER BY date;
```

**Search for a specific formula:**
```sql
SELECT * FROM formula_installs
WHERE formula = 'node';
```

---

## Notes

- The Homebrew API updates once per day, so running the script multiple times on the same day will not produce new delta data
- Delta calculations require at least two runs on different days to produce results
- Charts are overwritten on each run — they always reflect the latest data
- The `formula_deltas` table excludes formulae with no prior data from percentage calculations and marks them with `is_new = 1`

---

## Project status & cleanup

### Stopping the nightly scheduler

The launchd scheduler was removed to prevent the database from continuing to grow on local disk. To stop it:

```bash
launchctl unload ~/Library/LaunchAgents/com.brewanalytics.fetch.plist
```

Confirm it's no longer running:
```bash
launchctl list | grep brewanalytics
```
If nothing is returned, it has been successfully removed.

### Database location

The default database was stored at:
```
/Users/sarahye/Documents/wex-2026-brew-trend
```

The database file is listed in `.gitignore` and was never committed to the repository. After stopping the scheduler, the database file was deleted from local disk as it contained only experimental/test data collected during development. No backup was retained.

To delete it yourself:
```bash
rm /Users/sarahye/Documents/wex-2026-brew-trend
```

### Log files

The launchd log files were also removed:
```bash
rm brew_analytics.log
rm brew_analytics_error.log
```

---

## Database growth strategy (for future production use)

Each daily run adds ~23,000 rows to `formula_installs` and ~23,000 rows to `formula_deltas`. Over a year that's ~17 million rows — significant on local disk.

### Recommended two-tier approach

For a production deployment the recommended strategy is to keep only the last 30 days of data in the active database, and archive anything older to cheap slow storage (e.g. AWS S3, an external drive, or a separate archive SQLite file).

**Why 30 days?** That's the window users care about for trending. Nobody queries "what was trending 6 months ago" in a day-to-day analytics tool.

**Archival process** (to run after each daily fetch):
1. Copy rows older than 30 days from `formula_installs` and `formula_deltas` into an archive database
2. Delete those rows from the active database
3. Run `VACUUM` to reclaim disk space

```sql
-- Example: delete old formula_installs rows (after archiving)
DELETE FROM formula_installs
WHERE run_id IN (
    SELECT id FROM analytics_runs
    WHERE end_date < DATE('now', '-30 days')
);

-- Reclaim disk space
VACUUM;
```

This keeps the active database at a roughly constant size regardless of how long the system runs.

---

## Future improvements

### Web dashboard
A browser-based dashboard would make the data significantly more accessible than Beekeeper Studio or static chart images. Proposed features:

- Dynamic line chart filterable by formula name
- Configurable minimum install count cutoff (replaces the hardcoded 1,000)
- Date range selector to zoom into specific periods
- Toggle between raw count view and percentage change view
- Trending table showing top N risers/fallers, sortable by delta count or delta percent
- New formula panel highlighting `is_new = 1` entries with high install counts

### Trending definition
A single day's delta is noisy. A more meaningful "trending" signal would be formulae that have risen consistently over 7 days — a moving average or streak counter would filter out one-off spikes and surface genuinely growing packages.

### Configurable cutoffs
The 1,000 install minimum is currently hardcoded. A configuration file or CLI argument would let users adjust this and other thresholds without modifying the source code.

