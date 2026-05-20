import sqlite3
conn = sqlite3.connect('brew_data.db')

# Check snapshots
print("=== SNAPSHOTS ===")
cur = conn.execute("SELECT * FROM snapshots LIMIT 3")
rows = cur.fetchall()
if rows:
    print(f"Columns: {[d[0] for d in cur.description]}")
    for row in rows:
        print(row)

# Check installations
print("\n=== INSTALLATIONS ===")
cur = conn.execute("SELECT * FROM installations LIMIT 3")
rows = cur.fetchall()
if rows:
    print(f"Columns: {[d[0] for d in cur.description]}")
    for row in rows:
        print(row)