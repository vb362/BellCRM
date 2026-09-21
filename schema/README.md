# Working database setup

`working.sql` defines the shared version-3 structure for `demo.sqlite` and `production.sqlite`: three existing data tables, `runs`, and the six tables described in `LOCAL_DATABASE_SCHEMA.md`.

From the project directory, apply it through the migration script:

```sh
python3 migrate_databases.py --database both
```

Use `--database demo` or `--database production` to update just one working database. The files must already exist. The script does not contact the website or CRM API and does not populate production from demo.

The migration:

- Never modifies `start.sqlite`, including through a symbolic link or hard link.
- Preserves existing CRM accounts, contacts, website records, and original scrape history.
- Replaces the old production snapshot tables only if they are empty; otherwise stops.
- Saves consistent pre-migration copies in `data/backups/`, excluded from Git.
- Applies each database migration in a transaction and rolls it back if validation fails.
- Checks schema, foreign keys, and SQLite integrity before committing.
- Does nothing when a version-3 database already matches the schema.

After a future demo reset copies `start.sqlite` into `demo.sqlite`, run:

```sh
python3 migrate_databases.py --database demo
```

This adds empty workflow tables to the restored copy. It does not add a fake historical run for the baseline website snapshot. A future saved-data replay must explicitly select that snapshot and create its own run.

## Run tracking

The original scrape columns remain unchanged. New columns are:

| Columns | Meaning |
|---|---|
| `run_type` | `scraper` or `pipeline`; no synchronization runs yet |
| `website_snapshot_id` | Website batch used by the run |
| `current_stage` | `scraping`, `normalization`, `matching`, or `complete` |
| `scraping_status`, `normalization_status`, `matching_status` | Each stage is `pending`, `running`, `complete`, `incomplete`, or `failed` |
| `crm_records_normalized`, `website_records_normalized` | Number of comparison records saved |
| `new_proposals`, `existing_proposals`, `decided_proposals_skipped` | Matching results |

Existing runs are identified as scrapes. Their original outcome fills `scraping_status`; normalization and matching remain pending. Small database triggers keep legacy scraper inserts and status updates consistent with these new fields. `run_pipeline.py` creates one `pipeline` run and passes its ID through all three stages, then records the overall result and finish time.

## Database rules and remaining application work

Enable `PRAGMA foreign_keys = ON` on every connection; SQLite does not enable it globally. Structured changes/evidence are validated text values inside SQLite, not intermediate files.

Submitted decisions and application history are permanent. One decision may have several failed attempts and at most one success. Local approval code must save all account changes and their success record in the same transaction. If that transaction fails, roll it back before separately recording failure.

`normalize_data.py --database demo --run-id YOUR_RUN_ID` now populates both normalized tables using the selected run's completed website scrape and current CRM accounts. It saves both batches together and reuses completed batches. It writes no CSV files. Its stage status and counts are stored in `runs`; overall pipeline completion is recorded by `run_pipeline.py`.

`match_records.py` now generates stable keys, saves proposals and run associations, and reuses pending or decided proposals. Approval/application remains future backend work. Version 3 adds `proposals.title` and the self-reference `proposals.depends_on_proposal_id` for duplicate resolution; existing version-2 rows and decisions are preserved. No synchronization code, remote ID column, or operation-order field is included. See `MATCHING_SCRIPT.md` for the proposal payload and dependency contract.

`start-and-demo.sql` is the original version-1 baseline definition. `legacy-production-v1.sql` preserves the historical production layout for migration tests. Use the migration script for working database setup rather than applying these historical definitions or `working.sql` directly.

Run checks without modifying real databases:

```sh
python3 -B -m unittest discover -s tests -v
```

The normal full-workflow entry point is `.venv/bin/python run_pipeline.py --database demo`. See [PIPELINE.md](../PIPELINE.md). Individual stage commands remain available for testing.


Working schema version 4 adds `production_plans` (immutable approved API previews) and `production_requests` (durable delivery/verification progress). Migration preserves CRM data and decisions and makes a pre-upgrade SQLite backup. Production mode upgrades its own database on first use. Existing version-3 demo databases remain usable; use `migrate_databases.py --database demo` to upgrade them explicitly.
