# Bellhaven CRM Cleaner

Find and review differences between Bellhaven’s website and Clipboard's CRM records.

<p align="center">
  <a href="#project-description">Project Description</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#how-to-use">How to Use</a> ·
  <a href="#matching-rules">Matching Rules</a> ·
  <a href="#sidebar-sections">Sidebar Sections</a>
</p>

![Bellhaven welcome screen](https://github.com/user-attachments/assets/deae5aed-b2bc-45cf-aeda-69d01c596fd2)

## Project Description

Bellhaven CRM Cleaner compares facility information on the Ballhaven website with Clipboard's CRM records to find outdated details, missing facilities, and duplicates. It prompts a user to review the suggested changes and choose what edits to apply to the CRM. 

## Quick Start

With Python 3.11+ installed, run these commands from the project folder (macOS or Linux):

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

Then open [localhost:8000](http://localhost:8000). Keep the terminal running. 

The repository includes the prepared `data/start.sqlite` baseline for Test mode and the standalone card snapshot in `exports/`. Setup copies the baseline into a local demo database and initializes a separate empty production database. Existing working databases are preserved. If the baseline is unavailable, setup creates an empty demo database.

## How to Use

1. Choose **Test mode** to scan the website and compare records.
2. Once the run finishes, open **Home** to review, approve, edit, or decline suggested changes.
3. Click **Review and submit**, then **Submit** to save your choices and apply approved changes to the local CRM copy.
4. Open **Decisions** to see your saved choices and change history.

<!-- Add screenshots alongside the steps where useful. -->

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
| **Settings** | Contains demo reset and daily scheduling controls. These are not connected yet. |
