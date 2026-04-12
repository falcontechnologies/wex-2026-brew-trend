import requests
import sqlite3
import matplotlib.pyplot as plt
from datetime import date

import logging
from datetime import datetime

# Set up a log file in your project folder.
# Every time the script runs, it appends a new line to brew_log.txt
# so you have a permanent record of every run.
logging.basicConfig(
    filename=r"C:\Users\BC-Tech\Documents\GitHub\Falcon-Technologies-WEX-project\Falcon-Technologies-WEX-project\brew_log.txt",
    level=logging.INFO,
    format="%(asctime)s — %(message)s"
)

print("Script started")

URL = "https://formulae.brew.sh/api/analytics/install/30d.json"

def fetch_and_store():
    """
    Fetches a snapshot from the Homebrew API and stores
    every package record in the SQLite database.
    """
    print(f"Fetching data for {date.today()}")

    response = requests.get(URL)

    if response.status_code != 200:
        print(f"Request failed. Status code: {response.status_code}")
        return

    data = response.json()
    print(data)

fetch_and_store()