#!/usr/bin/env python3
"""
Homebrew Tracker with SQLite - DEMO VERSION
============================================
Uses sample data so you can test it immediately without hitting the API.
"""

import sqlite3
from datetime import datetime
import random

# ============================================================================
# SAMPLE DATA
# ============================================================================

SAMPLE_API_DATA = {
    "total_items": 22771,
    "start_date": "2026-02-18",
    "end_date": "2026-03-20",
    "items": [
        {"number": 1, "formula": "openssl", "count": "515,827", "percent": "1.89"},
        {"number": 2, "formula": "ca-certificates", "count": "497,112", "percent": "1.82"},
        {"number": 3, "formula": "python", "count": "487,293", "percent": "1.78"},
        {"number": 4, "formula": "readline", "count": "485,621", "percent": "1.78"},
        {"number": 5, "formula": "sqlite", "count": "472,034", "percent": "1.73"},
    ]
}

DATABASE_FILE = "homebrew_analytics_demo.db"
TOP_N = 5

# ============================================================================
# DATABASE SETUP
# ============================================================================

def setup_database():
    """Create database and tables."""
    print("📊 Setting up SQLite database...")
    
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            date TEXT NOT NULL,
            total_packages INTEGER,
            api_start_date TEXT,
            api_end_date TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            package_name TEXT NOT NULL,
            install_count INTEGER NOT NULL,
            percentage TEXT,
            rank INTEGER,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_snapshot_id 
        ON packages(snapshot_id)
    ''')
    
    conn.commit()
    conn.close()
    
    print(f"✅ Database created: {DATABASE_FILE}")
    print(f"   You can open this with DB Browser for SQLite!")

def fetch_api_data():
    """DEMO: Return sample data with slight variations."""
    print("\n📡 [DEMO MODE] Using sample data...")
    
    # Add some random variation to simulate real data changes
    data = SAMPLE_API_DATA.copy()
    data['items'] = []
    
    for item in SAMPLE_API_DATA['items']:
        # Randomly adjust counts by ±5%
        base_count = int(item['count'].replace(',', ''))
        variation = random.randint(-5000, 5000)
        new_count = base_count + variation
        
        new_item = item.copy()
        new_item['count'] = f"{new_count:,}"
        data['items'].append(new_item)
    
    return data

def save_snapshot(api_data):
    """Save snapshot to database."""
    print("\n💾 Saving snapshot to database...")
    
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO snapshots (timestamp, date, total_packages, api_start_date, api_end_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(),
            datetime.now().strftime('%Y-%m-%d'),
            api_data.get('total_items', 0),
            api_data.get('start_date'),
            api_data.get('end_date')
        ))
        
        snapshot_id = cursor.lastrowid
        print(f"   Created snapshot #{snapshot_id}")
        
        items = api_data.get('items', [])[:TOP_N]
        
        for item in items:
            cursor.execute('''
                INSERT INTO packages (snapshot_id, package_name, install_count, percentage, rank)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                snapshot_id,
                item['formula'],
                int(item['count'].replace(',', '')),
                item['percent'],
                item['number']
            ))
            
            print(f"   └─ {item['formula']}: {item['count']} installs")
        
        conn.commit()
        print("✅ Snapshot saved!")
        return snapshot_id
    
    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}")
        return None
    
    finally:
        conn.close()

def show_database_summary():
    """Show what's in the database."""
    print("\n" + "=" * 60)
    print("📊 DATABASE SUMMARY")
    print("=" * 60)
    
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM snapshots')
    snapshot_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT MIN(date), MAX(date) FROM snapshots')
    first_date, last_date = cursor.fetchone()
    
    print(f"Total snapshots: {snapshot_count}")
    if snapshot_count > 0:
        print(f"Date range: {first_date} to {last_date}")
    
    cursor.execute('''
        SELECT p.rank, p.package_name, p.install_count, p.percentage
        FROM packages p
        JOIN snapshots s ON p.snapshot_id = s.id
        WHERE s.id = (SELECT MAX(id) FROM snapshots)
        ORDER BY p.rank
    ''')
    
    print(f"\nLatest Top {TOP_N}:")
    for rank, name, count, pct in cursor.fetchall():
        print(f"  #{rank}: {name:20} - {count:,} installs")
    
    if snapshot_count >= 2:
        print("\n📈 TREND ANALYSIS:")
        
        cursor.execute('''
            WITH first_snapshot AS (
                SELECT package_name, install_count
                FROM packages
                WHERE snapshot_id = (SELECT MIN(id) FROM snapshots)
            ),
            last_snapshot AS (
                SELECT package_name, install_count
                FROM packages
                WHERE snapshot_id = (SELECT MAX(id) FROM snapshots)
            )
            SELECT 
                l.package_name,
                f.install_count as first_count,
                l.install_count as last_count,
                l.install_count - COALESCE(f.install_count, 0) as change
            FROM last_snapshot l
            LEFT JOIN first_snapshot f ON l.package_name = f.package_name
            ORDER BY l.install_count DESC
        ''')
        
        for name, first, last, change in cursor.fetchall():
            if change > 0:
                print(f"  {name:20} ↗ +{change:,}")
            elif change < 0:
                print(f"  {name:20} ↘ {change:,}")
            else:
                print(f"  {name:20} → No change")
    
    conn.close()

def main():
    print("=" * 60)
    print("🍺 HOMEBREW TRACKER [DEMO - SQLite]")
    print("=" * 60)
    
    setup_database()
    api_data = fetch_api_data()
    
    if api_data:
        save_snapshot(api_data)
        show_database_summary()
    
    print("\n" + "=" * 60)
    print("✨ TIP: Run this multiple times to see trends!")
    print(f"   Then open {DATABASE_FILE} in DB Browser for SQLite")
    print("=" * 60)

if __name__ == "__main__":
    main()
