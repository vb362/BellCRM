#!/usr/bin/env python3
"""Collect Bellhaven communities into an existing demo or production database.

Install the two extra libraries:
    python3 -m pip install requests beautifulsoup4

Place this file in the Bellhaven project folder, beside data/.
Run these commands from that folder:
    python3 scrape_website.py --database demo --setup   # One-time setup; no scrape
    python3 scrape_website.py --database demo           # Collect into demo.sqlite
    python3 scrape_website.py --database production --setup
    python3 scrape_website.py --database production

The database choice is always required. This script never writes to start.sqlite
or the online CRM. Each scrape adds new snapshots; earlier snapshots stay intact.

IMPORTANT: The existing website_snapshots table calls its batch ID snapshot_id.
We put the run_id in that column, linking each snapshot to runs.run_id without
renaming columns or changing existing records.
"""

# 1. IMPORTS ---------------------------------------------------------------
# Everything except requests and BeautifulSoup is included with Python.
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import requests
from bs4 import BeautifulSoup


# 2. SETTINGS --------------------------------------------------------------
# Resolve paths relative to this script, not the terminal's current directory.
PROJECT_DIR = Path(__file__).resolve().parent
BASE_URL = "https://analyst-assessment-production.up.railway.app/"
DIRECTORY_URL = urljoin(BASE_URL, "communities")
DATABASES = {"demo": "demo.sqlite", "production": "production.sqlite"}
EXPECTED_LABELS = {"address", "care offerings", "phone", "administrator"}


def now():
    """Return a consistent UTC timestamp, for example 2026-09-19T16:30:00+00:00."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 3. DATABASE FUNCTIONS ----------------------------------------------------
def open_database(choice, setup=False):
    """Open an existing database. Only --setup is allowed to create runs."""
    return open_database_path(PROJECT_DIR / "data" / DATABASES[choice], setup=setup)


def open_database_path(database_path, setup=False):
    """Shared entry for the standalone scraper and pipeline; existing files only."""
    path = Path(database_path).resolve()
    start_path = PROJECT_DIR / "data" / "start.sqlite"
    if not path.is_file():
        raise ValueError(f"Database does not exist: {path}")
    # This also protects start.sqlite if someone links demo.sqlite to it.
    if path.name == "start.sqlite" or (start_path.exists() and path.samefile(start_path)):
        raise ValueError("The selected database points to start.sqlite. Stopping.")

    demo, production = (PROJECT_DIR / "data" / DATABASES[mode] for mode in ("demo", "production"))
    if demo.exists() and production.exists() and demo.samefile(production):
        raise ValueError("Demo and production must be separate database files.")

    # mode=rw prevents SQLite from silently creating a missing database file.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        required = {
            "snapshot_id", "source_url", "fetched_at", "name", "street", "city",
            "state", "zip", "care_offerings", "phone", "administrator", "raw_html",
        }
        check_columns(connection, "website_snapshots", required)
        if setup:
            # Setup adds only this table; it does not modify website_snapshots.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('running', 'complete', 'incomplete', 'failed')
                    ),
                    locations_found INTEGER NOT NULL DEFAULT 0,
                    locations_saved INTEGER NOT NULL DEFAULT 0,
                    errors TEXT NOT NULL DEFAULT '[]'
                )
            """)
            connection.commit()
        check_columns(connection, "runs", {
            "run_id", "started_at", "finished_at", "status",
            "locations_found", "locations_saved", "errors",
        })
        return connection
    except Exception:
        connection.close()
        raise


def check_columns(connection, table, required):
    """Explain incompatible tables before a scrape starts. Table names are internal."""
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    missing = required - existing
    if missing:
        hint = " Run once with --setup to create runs." if table == "runs" and not existing else ""
        raise ValueError(f"{table} is missing columns: {', '.join(sorted(missing))}.{hint}")


def start_run(connection):
    """Create a run and save it immediately so its start is recorded."""
    run_id = str(uuid4())
    with connection:
        connection.execute(
            "INSERT INTO runs (run_id, started_at, status) VALUES (?, ?, 'running')",
            (run_id, now()),
        )
    return run_id


def save_snapshot(connection, run_id, community):
    """Save one record in its own transaction: either all its fields save or none do."""
    record = dict(community)
    record["snapshot_id"] = run_id
    record["care_offerings"] = json.dumps(record["care_offerings"], ensure_ascii=False)
    with connection:
        connection.execute("""
            INSERT INTO website_snapshots (
                snapshot_id, source_url, fetched_at, name, street, city, state,
                zip, care_offerings, phone, administrator, raw_html
            ) VALUES (
                :snapshot_id, :source_url, :fetched_at, :name, :street, :city, :state,
                :zip, :care_offerings, :phone, :administrator, :raw_html
            )
        """, record)


def finish_run(connection, run_id, status, found, saved, errors):
    """Store the final counts and readable, structured problems in the runs row."""
    columns = {r[1] for r in connection.execute("PRAGMA table_info(runs)")}
    pipeline = 'run_type' in columns and connection.execute(
        "SELECT run_type FROM runs WHERE run_id=?", (run_id,)).fetchone()[0] == 'pipeline'
    with connection:
        if pipeline:
            # The runner owns overall status and finished_at. Scraping owns only
            # its stage, counts, snapshot, and errors in this shared run row.
            connection.execute("""UPDATE runs SET scraping_status=?, locations_found=?,
                locations_saved=?, errors=? WHERE run_id=?""",
                (status, found, saved, json.dumps(errors, ensure_ascii=False), run_id))
        else:
            connection.execute("""UPDATE runs SET finished_at=?, status=?, locations_found=?,
                locations_saved=?, errors=? WHERE run_id=?""",
                (now(), status, found, saved, json.dumps(errors, ensure_ascii=False), run_id))


# 4. DOWNLOAD A PAGE -------------------------------------------------------
def fetch_page(session, url):
    """Download HTML. Network errors and HTTP errors are handled by the caller."""
    response = session.get(url, timeout=60)
    response.raise_for_status()
    if urlsplit(response.url).netloc != urlsplit(BASE_URL).netloc:
        raise ValueError(f"Page redirected away from the Bellhaven website: {response.url}")
    # This website declares UTF-8. Explicit decoding preserves arrows and accents.
    response.encoding = "utf-8"
    return response.text, now()


# 5. FIND COMMUNITY LINKS --------------------------------------------------
def find_community_links(html, page_url):
    """Return distinct detail-page URLs, regardless of the community's name."""
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for link in soup.find_all("a", href=True):
        url = urlsplit(urljoin(page_url, link["href"]))
        if (url.scheme in {"http", "https"}
                and url.netloc == urlsplit(BASE_URL).netloc
                and re.fullmatch(r"/communities/[^/]+/?", url.path)):
            links.add(url._replace(fragment="").geturl())
    return links


def discover_communities(session, errors, problem_folder, urls):
    """Fill urls from both sources. A failure in one source does not erase the other."""
    homepage_total = None
    html = None
    try:
        html, _ = fetch_page(session, BASE_URL)
        soup = BeautifulSoup(html, "html.parser")
        urls.update(find_community_links(html, BASE_URL))
        # A blank/error/login page must not be mistaken for a homepage with no news.
        directory_links = [a for a in soup.find_all("a", href=True)
                           if urljoin(BASE_URL, a["href"]) == DIRECTORY_URL]
        if not soup.find("h1") or not directory_links:
            raise ValueError("Homepage heading or directory link is missing; check its layout.")
        total = re.search(r"serve\s+([\d,]+)\s+communities", soup.get_text(" ", strip=True), re.I)
        if total:
            homepage_total = int(total[1].replace(",", ""))
        else:
            record_problem(errors, "warning", BASE_URL, "homepage_total_missing",
                           "Could not read the homepage total. Check whether its wording changed.")
    except (requests.RequestException, ValueError) as error:
        record_problem(errors, "error", BASE_URL, "homepage_failed", str(error),
                       html, problem_folder)

    directory_links = set()
    visited_pages = set()
    expected_count = None
    page_url = DIRECTORY_URL
    while page_url:
        html = None
        try:
            if page_url in visited_pages:
                raise ValueError("Pagination returned to a page already visited.")
            visited_pages.add(page_url)
            html, _ = fetch_page(session, page_url)
            soup = BeautifulSoup(html, "html.parser")
            found = find_community_links(html, page_url)
            directory_links.update(found)
            urls.update(found)

            # The summary helps detect a missing Next link after a redesign.
            summary = re.search(
                r"Page\s+(\d+)\s+of\s+(\d+)\s*·\s*([\d,]+)\s+communities listed",
                soup.get_text(" ", strip=True), re.I,
            )
            if not summary:
                raise ValueError("Directory page summary is missing or changed; check pagination.")
            page_number, page_count, expected_count = [int(n.replace(",", "")) for n in summary.groups()]
            if not found and expected_count > 0:
                raise ValueError("Directory reports communities but no community links were recognized.")
            next_links = [a for a in soup.find_all("a", href=True)
                          if "next" in a.get("rel", [])
                          or re.match(r"^next\b", a.get_text(" ", strip=True), re.I)]
            if len(next_links) > 1 or (page_number < page_count and not next_links):
                raise ValueError("Expected one Next link, but pagination is missing or ambiguous.")
            if not next_links:
                break
            next_url = urljoin(page_url, next_links[0]["href"])
            parsed = urlsplit(next_url)
            if parsed.netloc != urlsplit(BASE_URL).netloc or parsed.path.rstrip("/") != "/communities":
                raise ValueError("Next link no longer points to the community directory.")
            page_url = next_url
        except (requests.RequestException, ValueError) as error:
            record_problem(errors, "error", page_url, "directory_failed", str(error),
                           html, problem_folder)
            break

    if expected_count is not None and len(directory_links) != expected_count:
        record_problem(errors, "error", DIRECTORY_URL, "directory_count_mismatch",
                       f"Directory reports {expected_count} communities; found {len(directory_links)} unique links.")
    return homepage_total


# 6. READ AND CHECK A COMMUNITY PAGE ---------------------------------------
def parse_address(address):
    """Read street line(s), then City, ST ZIP. Never guess an unfamiliar format."""
    lines = list(address.stripped_strings)
    if len(lines) < 2:
        raise ValueError("Address no longer has a street line and a separate city/state/ZIP line.")
    match = re.fullmatch(r"(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", lines[-1])
    if not match:
        raise ValueError(f"Cannot split city, state and ZIP from: {lines[-1]!r}")
    return {"street": " ".join(lines[:-1]), "city": match[1].strip(),
            "state": match[2], "zip": match[3]}


def parse_community(html, source_url, fetched_at):
    """Return a record and warnings. Raise ValueError if required data is unreliable."""
    soup = BeautifulSoup(html, "html.parser")
    headings = soup.find_all("h1")
    if len(headings) != 1 or not headings[0].get_text(strip=True):
        raise ValueError("Expected one nonempty community name in the main heading.")

    # HTML uses dt for a label and dd for its value. Match by label, not position.
    fields = {}
    for label in soup.select("dl dt"):
        name = " ".join(label.stripped_strings).casefold()
        value = label.find_next_sibling()
        if name in fields or value is None or value.name != "dd":
            raise ValueError(f"Duplicate label or missing value block: {name!r}")
        fields[name] = value

    for name in ("address", "care offerings"):
        if name not in fields or not fields[name].get_text(strip=True):
            raise ValueError(f"Required field is missing or empty: {name}")

    warnings = []
    for label in sorted(set(fields) - EXPECTED_LABELS):
        warnings.append(f"New field appeared: {label!r}. Check whether it should be collected.")
    for label in ("phone", "administrator"):
        if label not in fields:
            warnings.append(f"Optional label {label!r} disappeared. Saved NULL; check the layout.")

    # Each badge is one offering. Do not split on '&': it belongs to a care name.
    care = [badge.get_text(" ", strip=True) for badge in fields["care offerings"].select(".badge")]
    if not care or not all(care):
        raise ValueError("Care offerings are no longer readable as separate nonempty badges.")
    if " ".join(fields["care offerings"].stripped_strings) != " ".join(care):
        raise ValueError("Care offerings contain text outside the expected badges; check the layout.")

    community = {
        "source_url": source_url, "fetched_at": fetched_at,
        "name": headings[0].get_text(" ", strip=True),
        **parse_address(fields["address"]),
        "care_offerings": care, "raw_html": html,
    }
    for label in ("phone", "administrator"):
        community[label] = None
        if label in fields:
            community[label] = fields[label].get_text(" ", strip=True) or None
    return community, warnings


# 7. KEEP PROBLEMS EASY TO INVESTIGATE -------------------------------------
def record_problem(errors, severity, url, code, message, html=None, folder=None):
    """Append a JSON-friendly problem, saving failed HTML when it is available."""
    problem = {"severity": severity, "source_url": url, "code": code, "message": message}
    if html is not None and folder is not None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{uuid4().hex}.html"
            path.write_text(html, encoding="utf-8")
            problem["diagnostic_path"] = str(path)
        except OSError as error:
            problem["diagnostic_error"] = str(error)
    errors.append(problem)


# 8. CALLABLE SCRAPER: also used directly by run_pipeline.py -----------------
def scrape_database(database_path, run_id=None):
    """Save one scrape and return its exact run ID and outcome.

    Without run_id this creates a standalone scrape run. With run_id it claims
    a new pipeline run whose scraping stage is still pending. No existing batch
    is overwritten or selected by recency.
    """
    connection = open_database_path(database_path)
    errors = []
    urls = set()
    saved = 0
    interrupted = False
    started = False
    try:
        if run_id is None:
            run_id = start_run(connection)
        else:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""SELECT run_type,status,scraping_status,website_snapshot_id
                FROM runs WHERE run_id=?""", (run_id,)).fetchone()
            if row != ('pipeline', 'running', 'pending', run_id):
                raise ValueError("Scraper requires a new pending pipeline run with its own snapshot ID")
            if connection.execute("SELECT 1 FROM website_snapshots WHERE snapshot_id=? LIMIT 1", (run_id,)).fetchone():
                raise ValueError("This pipeline snapshot already contains records")
            connection.execute("UPDATE runs SET scraping_status='running',current_stage='scraping' WHERE run_id=?", (run_id,))
            connection.commit()
        started = True
        path = Path(database_path).resolve()
        project = path.parent.parent if path.parent.name == 'data' else path.parent
        problem_folder = project / "diagnostics" / path.stem / run_id
        print(f"Database: {path.name} | Run: {run_id}", flush=True)
        with requests.Session() as session:
            session.headers["User-Agent"] = "BellhavenCommunityCollector/1.0"
            homepage_total = discover_communities(session, errors, problem_folder, urls)
            if not urls:
                record_problem(errors, "error", BASE_URL, "no_communities", "No community links discovered.")
            if homepage_total is not None and len(urls) != homepage_total:
                record_problem(errors, "warning", BASE_URL, "homepage_count_mismatch",
                               f"Homepage says {homepage_total}; discovered {len(urls)} unique community links.")

            connection.execute('UPDATE runs SET locations_found=? WHERE run_id=?', (len(urls), run_id))
            connection.commit()
            for number, url in enumerate(sorted(urls), start=1):
                print(f"[{number}/{len(urls)}] {url}", flush=True)
                html = None
                try:
                    html, fetched_at = fetch_page(session, url)
                    community, warnings = parse_community(html, url, fetched_at)
                    save_snapshot(connection, run_id, community)
                    saved += 1
                    connection.execute('UPDATE runs SET locations_saved=? WHERE run_id=?', (saved, run_id))
                    connection.commit()
                    for message in warnings:
                        record_problem(errors, "warning", url, "layout_change", message)
                except (requests.RequestException, ValueError, sqlite3.Error) as error:
                    record_problem(errors, "error", url, "community_failed", str(error),
                                   html, problem_folder)
    except KeyboardInterrupt:
        interrupted = True
        record_problem(errors, "error", None, "interrupted", "Collection was stopped by the user.")
    except Exception as error:
        if not started:
            raise
        # A final safety net: preserve progress and mark unexpected failures visibly.
        record_problem(errors, "error", None, "unexpected_error", f"{type(error).__name__}: {error}")
    finally:
        has_errors = any(problem["severity"] == "error" for problem in errors)
        if saved == 0:
            status = "failed"
        elif has_errors or saved != len(urls):
            status = "incomplete"
        else:
            status = "complete"
        try:
            if started:
                finish_run(connection, run_id, status, len(urls), saved, errors)
        except sqlite3.Error as error:
            # For example, a full disk may prevent even the final report being saved.
            print(f"Could not finalize run {run_id}: {error}. Its stored status may still be running.", file=sys.stderr)
            status = "failed"
        finally:
            connection.close()

    print(f"\n{status.upper()}: {len(urls)} communities found; {saved} saved.")
    for problem in errors:
        print(f"{problem['severity'].upper()}: {problem['source_url'] or 'Run'}: {problem['message']}")
        if "diagnostic_path" in problem:
            print(f"  Saved HTML: {problem['diagnostic_path']}")
    return {"run_id": run_id, "status": status, "locations_found": len(urls),
            "locations_saved": saved, "errors": errors, "interrupted": interrupted}


def main():
    """Standalone CLI; the pipeline calls scrape_database instead of parsing logs."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=DATABASES)
    parser.add_argument("--setup", action="store_true",
                        help="Create the legacy runs table without scraping. Use migrate_databases.py for the full workflow schema.")
    args = parser.parse_args()
    try:
        if args.setup:
            connection = open_database(args.database, setup=True)
            connection.close()
            print(f"Setup complete for {DATABASES[args.database]}. No website pages collected.")
            return 0
        result = scrape_database(PROJECT_DIR / "data" / DATABASES[args.database])
    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"Cannot start: {error}", file=sys.stderr)
        return 1
    if result['interrupted']:
        return 130
    return 0 if result['status'] == 'complete' else 1


# 9. START ONLY WHEN THIS FILE IS RUN DIRECTLY ------------------------------
if __name__ == "__main__":
    sys.exit(main())
