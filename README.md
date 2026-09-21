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

## Production mode

The server automatically loads `BELLHAVEN_API_TOKEN` from the project's local `.env` file at startup. This file is excluded from Git. A token already set in the server's environment takes precedence. To set a temporary environment override in macOS's default zsh, enter these lines one at a time (the token is hidden while typing):

```sh
read -s "BELLHAVEN_API_TOKEN?API token: "
printf '\n'
export BELLHAVEN_API_TOKEN
.venv/bin/python server.py
```

The token stays in the server process; the API preview redacts authentication. Restart an existing server after updating the code or token. The application listens on localhost only.

1. Select **Production mode**. It reads all CRM accounts and contacts into the separate `data/production.sqlite` mirror, then runs the same website comparison pipeline. Its schema upgrades automatically with a backup on first use.
2. Review and stage decisions as usual. This does not send CRM writes.
3. Open **Review and submit**. The app refreshes the CRM, checks the decisions, and saves the exact ordered methods, URLs, and JSON bodies. New-account references explicitly point to the call that will return their ID.
4. Click **Confirm and run API calls**. The server executes that saved plan, GETs each changed record to verify it, and records the actual CRM IDs and timestamps locally. Changes to the reviewed decisions or CRM since preview block a fresh submission.
5. Open **Decisions** for the confirmed before/after history. Test mode still applies only local changes.

If a call fails, the remaining calls stop. Reopen **View submission** to inspect progress and explicitly resume. Verified calls are skipped; known successful writes are read again, not resent. An uncertain creation is never automatically repeated: inspect the CRM, enter the newly created record's ID in the saved submission, and resume verification. If its outcome cannot be established, leave the submission paused for reconciliation. An uncertain PATCH is also read back without automatic replay; a mismatch needs manual reconciliation. A plan can be discarded only when it has no successful or uncertain writes.

The CRM does not document transactions or conditional updates. A multi-call submission can partially succeed, and a concurrent CRM edit can still occur between a check and a write. The executor does not promise remote rollback or exactly-once delivery. The local decision history is finalized only after the entire saved plan verifies; partial progress is retained separately in `production_requests`.

The implementation is in [production_api.py](production_api.py), mode/session routing in [server.py](server.py), and the verified API contract in [API.md](API.md). Production credentials are required to activate the live mode. Automated tests use a simulated CRM and temporary databases:

```sh
.venv/bin/python -m unittest discover -s tests -q
```

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
