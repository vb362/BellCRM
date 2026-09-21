#!/usr/bin/env python3
"""Save normalized comparison records in the selected working database.

Run from a terminal (Python's built-in libraries are enough):
    python3 normalize_data.py --database demo --run-id YOUR_RUN_ID
    python3 normalize_data.py --database production --run-id YOUR_RUN_ID

The selected run must have a complete website scrape and a website_snapshot_id.
Results go into normalized_crm_accounts and normalized_website_locations.
Both tables save together. Repeating a completed normalization reuses its batch.
Original source tables are unchanged. No CSV files or network requests are made.
"""

import argparse
import json
import sqlite3
from pathlib import Path


# 1. RULES: these dictionaries are the complete list of word replacements.
# No fuzzy matching, external service, or trained model is involved.
PROJECT_DIR = Path(__file__).resolve().parent
DATABASES = {
    "demo": PROJECT_DIR / "data" / "demo.sqlite",
    "production": PROJECT_DIR / "data" / "production.sqlite",
}
START_DATABASE = PROJECT_DIR / "data" / "start.sqlite"
CRM_FIELDS = (
    "account_id", "name", "parent_id", "parent_name", "billing_street",
    "billing_city", "billing_state", "billing_zip", "care_type", "status",
    "phone", "lifetime_revenue", "outstanding_ar", "chow_current_account",
    "duplicate_of_account", "note", "created_by_candidate", "updated_at",
)
COMPARISON_FIELDS = (
    "normalized_name", "normalized_street", "normalized_city",
    "normalized_state", "address_fields_present",
)

STREET_TYPES = {
    "road": "rd", "street": "st", "avenue": "ave",
    "boulevard": "blvd", "drive": "dr", "lane": "ln",
    "pk": "pike",
}
DIRECTIONS = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw",
    "southeast": "se", "southwest": "sw",
}
NAME_WORDS = {"centre": "center", "rehab": "rehabilitation", "&": "and", "at": "of"}

# Recognize both the original and normalized forms of each street suffix.
SUFFIXES = set(STREET_TYPES) | set(STREET_TYPES.values())
DIRECTION_WORDS = set(DIRECTIONS) | set(DIRECTIONS.values())


# 2. NORMALIZATION: each function accepts one value and returns a new value.
def normalize_text(value):
    """Lowercase text, collapse whitespace, and represent missing text as None."""
    if value is None:
        return None
    return " ".join(str(value).lower().split()) or None


def normalize_name(value):
    """Ignore a leading article and standalone dashes for name comparison only."""
    text = normalize_text(value)
    if text is None:
        return None

    words = [word for word in text.split() if word not in {'-', '–', '—'}]
    if words[:1] == ['the']:
        words = words[1:]
    result = []
    index = 0
    while index < len(words):
        # The two-word phrase is handled before the single-word dictionary.
        if words[index:index + 2] == ["health", "care"]:
            result.append("healthcare")
            index += 2
        else:
            result.append(NAME_WORDS.get(words[index], words[index]))
            index += 1
    return " ".join(result) or None


def normalize_street(value):
    """Apply positional rules, not replacements inside names such as Willow.

    Direction rules require a first token made entirely of ASCII digits.
    Numbers such as 123A or 123-125 keep their directions unchanged.
    An address with a trailing apartment/unit is not rearranged or stripped.
    Punctuation is preserved. These deliberately limited rules do not cover
    every US address, and equal results do not prove facility identity.
    """
    text = normalize_text(value)
    if text is None:
        return None
    words = text.split()

    # A suffix must be last, or immediately before a final direction.
    suffix_index = len(words) - 1
    has_final_direction = words[-1] in DIRECTION_WORDS
    if has_final_direction:
        suffix_index -= 1
    if suffix_index < 0 or words[suffix_index] not in SUFFIXES:
        return text

    suffix = words[suffix_index]
    words[suffix_index] = STREET_TYPES.get(suffix, suffix)

    # This is a precise, conservative number rule, not a validity check.
    numbered = all(character in "0123456789" for character in words[0])
    if numbered:
        # Example: 123 Main Street West -> 123 main st w.
        # At least one word must sit between the number and the suffix.
        if has_final_direction and suffix_index >= 2:
            words[-1] = DIRECTIONS.get(words[-1], words[-1])

        # Example: 123 West Main Street -> 123 w main st.
        # Keep West in 123 West Street: there is no separate street-name word.
        if suffix_index >= 3 and words[1] in DIRECTIONS:
            words[1] = DIRECTIONS[words[1]]

    return " ".join(words)


# 3. INPUT: select an explicit run in an existing working database.
def open_database(database_path):
    """Allow working databases only; never create or migrate a database here."""
    path = Path(database_path).resolve()
    if not path.is_file():
        raise ValueError(f"Database does not exist: {path}")
    if path.name == "start.sqlite" or (START_DATABASE.exists() and path.samefile(START_DATABASE)):
        raise ValueError("start.sqlite is protected. Select a working database.")
    if DATABASES["demo"].exists() and DATABASES["production"].exists() and DATABASES["demo"].samefile(DATABASES["production"]):
        raise ValueError("Demo and production must be separate database files.")
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA user_version").fetchone()[0] not in (2, 3, 4):
            raise ValueError("Schema version 2, 3 or 4 is required. Run migrate_databases.py first.")
        return connection
    except Exception:
        connection.close()
        raise


def load_data(connection, run):
    """Read the run's exact website snapshot and all current local CRM fields."""
    website = connection.execute(
        "SELECT snapshot_id, source_url, name, street, city, state, zip "
        "FROM website_snapshots WHERE snapshot_id = ? ORDER BY source_url",
        (run["website_snapshot_id"],),
    ).fetchall()
    if not website or len(website) != run["locations_saved"] or len(website) != run["locations_found"]:
        raise ValueError("The selected website snapshot has missing or inconsistent records.")
    crm = connection.execute(
        f"SELECT {', '.join(CRM_FIELDS)} FROM crm_accounts ORDER BY account_id"
    ).fetchall()
    if not crm:
        raise ValueError("The selected database has no CRM accounts to normalize.")
    return [dict(row) for row in crm], [dict(row) for row in website]


# 4. PROCESSING: preserve original fields and append comparison fields.
def normalize_records(records, crm=False):
    """Use the same rules on both sources despite their different column names."""
    prefix = "billing_" if crm else ""
    results = []
    for original in records:
        row = dict(original)
        row["normalized_name"] = normalize_name(original["name"])
        row["normalized_street"] = normalize_street(original[prefix + "street"])
        row["normalized_city"] = normalize_text(original[prefix + "city"])
        row["normalized_state"] = normalize_text(original[prefix + "state"])

        # A blank address must never count as an address match later.
        # This flag checks presence only; it does not validate the address.
        row["address_fields_present"] = all(
            row["normalized_" + field] is not None
            for field in ("street", "city", "state")
        )
        results.append(row)
    return results


# 5. OUTPUT: append one batch to the normalized tables, inside the caller's transaction.
def save_results(connection, run_id, crm_records, website_records):
    """Preserve CRM originals; website originals stay in website_snapshots."""
    for table, fields, records in (
        ("normalized_crm_accounts", CRM_FIELDS + COMPARISON_FIELDS, crm_records),
        ("normalized_website_locations", ("snapshot_id", "source_url") + COMPARISON_FIELDS, website_records),
    ):
        columns = ("run_id",) + fields
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [(run_id,) + tuple(row[field] for field in fields) for row in records],
        )


def batch_summary(connection, run, reused):
    """Check the saved batch without comparing it to today's mutable CRM."""
    counts = []
    missing = 0
    for table in ("normalized_crm_accounts", "normalized_website_locations"):
        count, absent = connection.execute(
            f"SELECT COUNT(*), COALESCE(SUM(address_fields_present = 0), 0) FROM {table} WHERE run_id = ?",
            (run["run_id"],),
        ).fetchone()
        counts.append(count)
        missing += absent
    other_snapshots = connection.execute(
        "SELECT COUNT(*) FROM normalized_website_locations WHERE run_id = ? AND snapshot_id <> ?",
        (run["run_id"], run["website_snapshot_id"]),
    ).fetchone()[0]
    if (counts != [run["crm_records_normalized"], run["website_records_normalized"]]
            or not all(counts) or counts[1] != run["locations_saved"] or other_snapshots):
        raise ValueError("Saved normalization is inconsistent. Preserve it for investigation; use a new run.")
    return {"run_id": run["run_id"], "website_snapshot_id": run["website_snapshot_id"],
            "crm_count": counts[0], "website_count": counts[1],
            "missing_addresses": missing, "reused": reused}


def normalize_database(database_path, run_id):
    """Read -> normalize -> save atomically. Called by the CLI or a future pipeline."""
    connection = open_database(database_path)
    try:
        # Serializes writers and keeps the source records consistent while we copy them.
        connection.execute("BEGIN IMMEDIATE")
        run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"Run does not exist: {run_id}")
        if run["scraping_status"] != "complete" or not run["website_snapshot_id"]:
            raise ValueError("This run has no completed website scrape. No older snapshot will be substituted.")
        if run["normalization_status"] == "complete":
            summary = batch_summary(connection, run, reused=True)
            connection.rollback()
            return summary
        if run["matching_status"] != "pending" or connection.execute(
                "SELECT 1 FROM run_proposals WHERE run_id = ? LIMIT 1", (run_id,)).fetchone():
            raise ValueError("Matching has already used this run. Use a new run instead of replacing its evidence.")
        for table in ("normalized_crm_accounts", "normalized_website_locations"):
            if connection.execute(f"SELECT 1 FROM {table} WHERE run_id = ? LIMIT 1", (run_id,)).fetchone():
                raise ValueError("An unfinished normalized batch already contains records. Use a new run.")
        errors = json.loads(run["errors"])
        if not isinstance(errors, list) or not all(isinstance(error, dict) for error in errors):
            raise ValueError("The run's error log must be a list of error records.")
        # Keep scraper warnings/errors, but remove a resolved normalization error on retry.
        errors = [error for error in errors if error.get("stage") != "normalization"]
        connection.execute(
            "UPDATE runs SET normalization_status = 'running', "
            "current_stage = CASE WHEN run_type = 'pipeline' THEN 'normalization' ELSE current_stage END "
            "WHERE run_id = ?", (run_id,),
        )
        connection.execute("SAVEPOINT normalized_batch")
        try:
            crm, website = load_data(connection, run)
            save_results(connection, run_id, normalize_records(crm, crm=True), normalize_records(website))
            connection.execute(
                "UPDATE runs SET normalization_status = 'complete', crm_records_normalized = ?, "
                "website_records_normalized = ?, errors = ? WHERE run_id = ?",
                (len(crm), len(website), json.dumps(errors, ensure_ascii=False), run_id),
            )
            saved_run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            summary = batch_summary(connection, saved_run, reused=False)
            connection.commit()
            return summary
        except (Exception, KeyboardInterrupt) as error:
            try:
                connection.execute("ROLLBACK TO normalized_batch")
                connection.execute("RELEASE normalized_batch")
                errors.append({"stage": "normalization", "severity": "error", "code": "normalization_failed",
                               "message": str(error) or type(error).__name__})
                connection.execute(
                    "UPDATE runs SET normalization_status = 'failed', crm_records_normalized = 0, "
                    "website_records_normalized = 0, errors = ? WHERE run_id = ?",
                    (json.dumps(errors, ensure_ascii=False), run_id),
                )
                connection.commit()
            except sqlite3.Error:
                # Disk/connection failures can prevent even error logging. Preserve
                # the original error, and leave no partially committed batch.
                connection.rollback()
            raise
    finally:
        # Invalid inputs or an unrecordable failure leave source and batch data unchanged.
        connection.close()


# 6. ENTRY POINT: choose a database and run, then print the saved counts.
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, choices=DATABASES,
                        help="Choose demo or production. start.sqlite cannot be written.")
    parser.add_argument("--run-id", required=True, help="Existing run with a completed scrape.")
    args = parser.parse_args()

    try:
        result = normalize_database(DATABASES[args.database], args.run_id)
    except (sqlite3.Error, OSError, ValueError) as error:
        parser.exit(1, f"Normalization stopped: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Normalization interrupted. No partial batch was saved.\n")

    print(f"Database: {args.database}")
    print(f"Run: {result['run_id']}")
    print(f"Website snapshot: {result['website_snapshot_id']}")
    print("Reused completed batch." if result["reused"] else "Saved normalized batch.")
    print(f"CRM records: {result['crm_count']}")
    print(f"Website records: {result['website_count']}")
    print(f"Records with missing street, city, or state: {result['missing_addresses']}")
    print("Source CRM and website records are unchanged. No matching was performed.")


if __name__ == "__main__":
    main()
