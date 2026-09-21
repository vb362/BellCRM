# Bellhaven CRM Cleaner

Find and review differences between Bellhaven’s website and Clipboard's CRM records.

<p align="center">
  <a href="#project-description">Project Description</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#how-to-use">How to Use</a> ·
  <a href="#daily-production-run">Daily Production Run</a> ·
  <a href="#matching-rules">Matching Rules</a> ·
  <a href="#sidebar-sections">Sidebar Sections</a>
</p>

![Bellhaven welcome screen](https://github.com/user-attachments/assets/deae5aed-b2bc-45cf-aeda-69d01c596fd2)

## Project Description

Bellhaven CRM Cleaner compares facility information on the Ballhaven website with Clipboard's CRM records to find outdated details, missing facilities, and duplicates. It prompts a user to review the suggested changes and choose what edits to apply to the CRM. 

## Quick Start

With Python 3.11+ installed:

1. Download and unzip this project, or clone the repository.
2. Open your terminal.
3. Navigate to the project folder using `cd /path/to/project`.
4. Copy, paste and run the commands below (macOS or Linux):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python - <<'PY'
from pathlib import Path
import shutil
import sqlite3

Path('data').mkdir(exist_ok=True)
baseline = Path('data/start.sqlite')
for mode in ('demo', 'production'):
    target = Path('data') / f'{mode}.sqlite'
    if not target.exists():
        if mode == 'demo' and baseline.is_file():
            shutil.copy2(baseline, target)
        else:
            with sqlite3.connect(target) as database:
                database.executescript(Path('schema/start-and-demo.sql').read_text())
PY
.venv/bin/python migrate_databases.py --database both
.venv/bin/python server.py
```

Then open your browser with URL [localhost:8000](http://localhost:8000). Keep the terminal running. 

## How to Navigate Website 

1. Choose **Test mode** to scan the website and compare records.
2. Once the run finishes, open **Home** to review, approve, edit, or decline suggested changes.
3. Click **Review and submit**, then **Submit** to save your choices and apply approved changes to the local CRM copy.
4. Open **Decisions** to see your saved choices and change history.

<!-- Add screenshots alongside the steps where useful. -->

## Daily Production Run

The schedule is in [`.github/workflows/daily-pipeline.yml`](.github/workflows/daily-pipeline.yml): `0 8 * * *` with timezone `America/New_York`, daily at **8:00 a.m. New York time**, including daylight-saving changes. It runs `run_pipeline.py --database production` to scrape, normalize, and generate proposals for review; it does not submit CRM changes.

Re-runs reuse the persistent production database and skip already submitted decisions, including rejections. To enable the schedule, configure a self-hosted runner labelled `bellhaven` and set `BELLHAVEN_PROJECT_DIR` to the installed project path. See [setup details](PIPELINE.md#daily-schedule). GitHub may delay scheduled starts.

## Matching Rules

See the [full matching rules](MATCHING_RULES.md) for details.

## Sidebar Sections

| Section | What it does |
| --- | --- |
| **Welcome** | Choose Test mode for local edits or Production mode for reviewed API submission. |
| **Home** | Compare website and CRM details, then approve, edit, or decline suggested changes. |
| **Runs** | Start a new scan and see its progress, results, or errors. View past runs here too. |
| **Decisions** | View draft and submitted decisions, including what changed before and after. |
| **Sources** | See the facility pages used for the latest completed website scan and when they were collected. |
| **Settings** | Reset Test mode to the original data with a backup saved first. The scheduling control is not connected. |

To reset a test, open **Settings → Reset → Yes** in Test mode after any run finishes. This restores the baseline and clears test edits, decisions, and run history. A copy of the previous demo is saved in `data/backups/`; production is unchanged. Open **Runs → Run Scraper** to generate proposals again.
