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
├── brew_charts.py                # Generates charts from the database
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

### Generate charts
```bash
python3.11 brew_charts.py
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
