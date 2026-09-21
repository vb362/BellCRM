Bellhaven CRM Cleaner

Find and review differences between Bellhaven’s website and Clipboard's CRM records.

<p align="center">
  <a href="#project-description">Project Description</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#how-to-use">How to Use</a> ·
  <a href="#matching-rules">Matching Rules</a> ·
  <a href="#sidebar-sections">Sidebar Sections</a>
</p>

![Bellhaven review screen](docs/screenshot.png)

## Project Description

Bellhaven CRM Cleaner compares facility information on the Ballhaven website with Clipboard's CRM records to find outdated details, missing facilities, and duplicates. It prompts a user to review the suggested changes and choose what edits to apply to the CRM. 

## Quick Start

With Python 3.11+ installed, run these commands from the project folder (macOS or Linux):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n data/start.sqlite data/demo.sqlite
.venv/bin/python migrate_databases.py --database demo
.venv/bin/python server.py
```

Then open [localhost:8000](http://localhost:8000). Keep the terminal running. 

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
| **Welcome** | Choose whether to start in Test or production mode using a local CRM copy. Production mode writes to the online DB using the API, test does not.<img width="1460" height="705" alt="Screenshot 2026-09-21 at 11 04 14 AM" src="https://github.com/user-attachments/assets/deae5aed-b2bc-45cf-aeda-69d01c596fd2" />
 |
| **Home** | Compare website and CRM details, then approve, edit, or decline suggested changes. |
| **Runs** | Start a new scan and see its progress, results, or errors. View past runs here too. |
| **Decisions** | View draft and submitted decisions, including what changed before and after. |
| **Sources** | See the facility pages used for the latest completed website scan and when they were collected. |
| **Settings** | Contains demo reset and daily scheduling controls. These are not connected yet. |
