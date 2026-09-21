# One run: scrape → normalize → match

`run_pipeline.py` calls the three scripts in order:

```python
scrape_database(database_path, run_id=run_id)
normalize_database(database_path, run_id)
match_database(database_path, run_id)
```

Each call finishes before the next starts. Each script saves its output in the selected SQLite database. The runner passes the same database path and run ID throughout; no intermediate files and no “latest scrape” lookup are involved.

Start the whole workflow with one command from the project folder:

```sh
.venv/bin/python run_pipeline.py --database demo
```

The project environment already contains the scraper's `requests` and `beautifulsoup4` dependencies. `--database production` uses the same local workflow, but that database must first contain CRM accounts. Neither mode applies proposals or calls the CRM API. Scraping reads public website pages only.

The future UI can call `run_pipeline(database_path)` instead of invoking the command. The individual scripts remain independently runnable.

## What the runner tracks

The runner creates one `runs` row with `run_type = pipeline`. Its ID is also the website snapshot ID. Scraping, normalization, and matching each update their own status and counts. The runner records the overall result and finish time only when the workflow finishes or stops.

- If all three stages succeed, the run is **complete** and proposals are saved for review.
- If scraping fails or is incomplete, normalization and matching do not start.
- If normalization fails, matching does not start.
- A failed matching batch saves no partial proposals. Completed earlier outputs remain available for investigation.
- A keyboard interruption records failure and stops later stages.
- Every new pipeline call creates a fresh run. Matching reuses existing proposals and remembers submitted decisions.
- A second pipeline cannot start in the same database while one is marked running. If the process was forcibly killed, inspect and resolve that interrupted run before starting another; the runner does not guess that an existing process has died.

The working databases must already have schema version 3. The runner does not silently migrate, reset, seed, or copy databases. `start.sqlite` is protected. No schema change was needed to add this runner.

## Daily schedule

The included [GitHub Actions workflow](.github/workflows/daily-pipeline.yml) runs
the whole pipeline every day at **03:17 UTC** (`17 3 * * *`). It also supports
manual runs through **Actions → Daily Bellhaven pipeline → Run workflow**.
GitHub schedules are best effort; the start time can be delayed. See the
[GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).

To enable it later:

1. Register a persistent Linux or macOS self-hosted runner with the custom label
   `bellhaven`, on the machine holding the application's working database.
2. Set the repository Actions variable `BELLHAVEN_PROJECT_DIR` to the absolute
   path of the installed Bellhaven project, outside the runner's temporary checkout.
   Set `BELLHAVEN_DATABASE` to `demo` or `production` (default: `production`).
   Choose the same database used for human review in the web app.
3. Prepare that installation once: Python 3.11 or newer, a `.venv` containing
   `requirements.txt`, and an initialized schema-version-3 database with CRM accounts.
   Keep the runner online and give its user write access to the database directory.
4. Commit the workflow to the repository's default branch. This configuration
   has only been added as a file; no runner or live schedule has been installed.

The workflow runs the code already installed at `BELLHAVEN_PROJECT_DIR`; deploy
code updates there separately while keeping `data/` intact. It deliberately does
not check out, seed, reset, or recreate the database each day. A fresh hosted
runner with a fresh database would lose the decision history and could re-propose
settled items. A cache or expiring artifact is not the authoritative database.

Re-runs create fresh scrape snapshots but reuse proposal identities. Pending
proposals are reused, and submitted decisions (including rejections) remain
decided. Staged choices are not final until submitted. A materially different
proposed change can produce a new proposal. GitHub concurrency prevents overlapping
workflow executions; the database also rejects overlapping pipeline runs, including
ones started by the UI. Errors produce a nonzero exit and a failed workflow.
After a force kill or job timeout, inspect and resolve any run still marked
`running` before retrying.

Alternatively, install this with `crontab -e` on the machine holding the database,
replacing `/absolute/path/to/Bellhaven` with the actual project path:

```cron
17 3 * * * cd /absolute/path/to/Bellhaven && .venv/bin/python -u run_pipeline.py --database production >> pipeline.log 2>&1
```

Quote the path if it contains spaces. Cron uses the machine's configured timezone.
Choose either cron or GitHub Actions so there is only one daily scheduler.

## Verification

```sh
.venv/bin/python -B -m unittest discover -s tests
```

Pipeline tests run the real parser, normalizer, and matcher against local HTML and temporary databases. They check stage order, one shared run ID, repeated runs, incomplete scrapes, failures, interruption, and database protection without network requests.
