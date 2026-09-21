"""Prepare the starting baseline and seed the demo with the original CRM.

Before running:
  1. Finish a successful website scrape into data/demo.sqlite.
  2. Close any app or scraper using these databases.
  3. Set BELLHAVEN_API_TOKEN in your environment or this folder's .env file.
  4. Install requests if needed: python3 -m pip install requests

Run when ready: .venv/bin/python prepare_start.py

This script only READS the online CRM. It does not change it.
It copies the latest complete demo website scrape into the empty start database,
then saves the downloaded CRM records in both start and demo. Existing demo
website snapshots and run history are preserved. Both databases are updated in
one transaction, so a failed save does not leave them half prepared.
It does not run the website scraper or touch production.sqlite.
Once prepared, start.sqlite is a fixed baseline; this script refuses to reseed it.
"""

from contextlib import closing
from pathlib import Path
import os
import sqlite3

import requests


# Settings: database paths are relative to this script, not your terminal folder.
BASE_URL = "https://analyst-assessment-production.up.railway.app/api/v1"
DATA_FOLDER = Path(__file__).resolve().parent / "data"
START_DB = DATA_FOLDER / "start.sqlite"
DEMO_DB = DATA_FOLDER / "demo.sqlite"


def load_token():
    """Read the token locally; never put it in source code or print it."""
    token = os.environ.get("BELLHAVEN_API_TOKEN", "").strip()
    env_file = DATA_FOLDER.parent / ".env"
    if not token and env_file.is_file():
        for line in env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "BELLHAVEN_API_TOKEN":
                token = value.strip().strip("\"'")
                break
    if not token or token == "PASTE_YOUR_TOKEN_HERE":
        raise SystemExit("Set BELLHAVEN_API_TOKEN in Bellhaven/.env or your environment first.")
    return token


def download_all(resource, id_field, token):
    """Read one page at a time until we have the API's reported total."""
    records = []
    page = 1
    expected_total = None

    while True:
        response = requests.get(
            f"{BASE_URL}/{resource}",
            headers={"Authorization": "Bearer " + token},
            params={"page": page, "page_size": 50},
            timeout=30,
        )
        response.raise_for_status()  # Stop if the server reports an error.
        result = response.json()    # Turn the JSON reply into Python data.

        if expected_total is None:
            expected_total = result["total"]
        elif result["total"] != expected_total:
            raise RuntimeError(f"The {resource} count changed during download. Try again.")

        records.extend(result["data"])  # Add this page's records to our list.

        # A repeated page must not make an incomplete download look complete.
        ids = [record[id_field] for record in records]
        if len(ids) != len(set(ids)):
            raise RuntimeError(f"Repeated IDs found in {resource}. Nothing saved.")

        print(f"{resource}: downloaded {len(records)} of {expected_total}")
        if len(records) == expected_total:
            return records
        if len(records) > expected_total or not result["data"]:
            raise RuntimeError(f"Unexpected page or total for {resource}. Nothing saved.")

        page += 1


def save_records(database, table, records, schema="main"):
    """Match the API field names to the columns already in our database."""
    # main means start.sqlite; demo means the attached demo.sqlite database.
    columns = [column[1] for column in database.execute(f"PRAGMA {schema}.table_info({table})")]
    if not columns:
        raise RuntimeError(f"Missing table: {table}")

    # Question marks let SQLite safely insert values without treating them as SQL.
    placeholders = ", ".join("?" for column in columns)
    column_names = ", ".join(columns)
    insert = f"INSERT INTO {schema}.{table} ({column_names}) VALUES ({placeholders})"

    for record in records:
        if set(record) - set(columns):
            raise RuntimeError(f"The API returned new fields for {table}. Update its schema first.")
        values = [record.get(column) for column in columns]
        database.execute(insert, values)


def has_any_data(database, schema="main", allowed_tables=()):
    """Check every table so we do not overwrite someone's existing demo work."""
    tables = database.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (table,) in tables:
        if table in allowed_tables:
            continue
        quoted_name = '"' + table.replace('"', '""') + '"'
        if database.execute(f"SELECT 1 FROM {schema}.{quoted_name} LIMIT 1").fetchone():
            return True
    return False


def check_setup(database):
    """Protect previous work and require a fully successful demo website scrape."""
    if has_any_data(database):
        raise SystemExit("start.sqlite already contains data. Leaving it unchanged.")
    if has_any_data(database, "demo", allowed_tables=("website_snapshots", "runs")):
        raise SystemExit("demo.sqlite contains CRM records or other work. Leaving it unchanged.")
    # Use the newest run, so a recent failed scrape is not silently ignored.
    run = database.execute("""
        SELECT run_id, status, locations_found, locations_saved
        FROM demo.runs ORDER BY started_at DESC, rowid DESC LIMIT 1
    """).fetchone()
    if not run or run[1] != "complete" or run[2] != run[3] or run[3] == 0:
        raise SystemExit("First finish a successful website scrape into demo.sqlite.")
    saved = database.execute(
        "SELECT COUNT(*) FROM demo.website_snapshots WHERE snapshot_id = ?", (run[0],)
    ).fetchone()[0]
    if saved != run[3]:
        raise SystemExit("Saved website rows do not match the completed scrape. Nothing changed.")
    return run[0]


def main():
    # 1. Check credentials and files BEFORE contacting the API.
    token = load_token()
    if not START_DB.is_file() or not DEMO_DB.is_file():
        raise SystemExit("Both data/start.sqlite and data/demo.sqlite must already exist.")
    if START_DB.samefile(DEMO_DB):
        raise SystemExit("start.sqlite and demo.sqlite must be different files.")

    with closing(sqlite3.connect(START_DB.as_uri() + "?mode=rw", uri=True)) as start:
        # Attaching demo lets SQLite save both files together in one transaction.
        start.execute("ATTACH DATABASE ? AS demo", (str(DEMO_DB),))
        for schema in ("main", "demo"):
            mode = start.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0]
            if mode not in ("delete", "truncate", "persist"):
                raise SystemExit("Initial setup needs SQLite rollback journals for a safe two-file save.")
        check_setup(start)

        # 2. Download BOTH complete lists before changing either database.
        accounts = download_all("accounts", "account_id", token)
        contacts = download_all("contacts", "contact_id", token)

        # 3. Lock both databases, recheck their state, then save everything together.
        # If any insert fails, SQLite rolls back changes in both files.
        with start:
            start.execute("BEGIN IMMEDIATE")
            snapshot_id = check_setup(start)
            columns = "snapshot_id, source_url, fetched_at, name, street, city, state, zip, care_offerings, phone, administrator, raw_html"
            start.execute(
                f"INSERT INTO main.website_snapshots ({columns}) "
                f"SELECT {columns} FROM demo.website_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            )
            for schema in ("main", "demo"):
                save_records(start, "crm_accounts", accounts, schema)
                save_records(start, "crm_contacts", contacts, schema)

    # 5. All database connections are now closed.
    print(f"Prepared {START_DB}")
    print(f"Seeded {DEMO_DB}; preserved its website snapshots and scrape history.")
    print("Keep start.sqlite unchanged from now on. Use demo.sqlite for practice.")


# Importing this file does not run it. It runs only when launched as a script.
if __name__ == "__main__":
    main()
