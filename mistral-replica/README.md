# Bellhaven CRM interface

Start the connected Test mode app from the parent Bellhaven directory:

```sh
.venv/bin/python server.py
```

Follow the parent [README](../README.md) to install dependencies and initialize local databases first. Open **http://localhost:8000**. Choose Test mode to start the real pipeline and view Runs. Home, Decisions, Sources, counts, and account views use `data/demo.sqlite`. Approvals and rejections are saved as drafts; Submit applies approved changes and records history. Production mode requires an authorized API token and uses a separate local database. Settings can reset Test mode with a backup once active runs finish; production is never reset. The scheduling control is unavailable.

See [WEB_APP.md](../WEB_APP.md) for the full flow and boundaries.

## UI sources

- `review.html`, `dist/assets/review.css`, `dist/assets/review.js`: proposal cards, duplicate resolution, reviews, and decision history.
- `dist/assets/app.js`: local-server requests and state refresh.
- `dist/assets/runs.js`: real run progress and history.
- `dist/assets/replica.js`: shell navigation, mode gate, and sticky controls.
- `reference/duplicate-card-design.html`: supplied duplicate-card design reference.
- `buildcrmui.py`: builds `dist/index.html`, `dist/crm-content.html`, and `Mistral Studio Replica.html`.

Rebuild from this folder with `python3 buildcrmui.py`. The standalone HTML preserves the layout but requires the local server for database actions.

## Card layouts and design export

The review renderer uses the supplied numbered designs in `reference/card-layouts/`
for types 2–10. It selects the layout from account operations, keeps duplicate
resolution unchanged, and uses separate proposals for each rename/parent candidate.
Same-address candidate grouping and optional deactivation are not enabled by this
visual update. Sandusky still follows the existing absence rule.

Editable account values in the card drawers use the existing manual-decision API.
Parent/status constraints and contact-resolution controls remain enforced; contact
writes and the inactivity note are displayed from the saved proposal. Full record
comparisons and exact writes remain available in each drawer.

After rebuilding the app, run `python3 mistral-replica/export_cards.py` from the
project root to refresh `exports/Bellhaven Cards - Real Data.html`. This exports a
read-only snapshot of the demo database with fonts, styles, scripts and data
embedded; it never stages or submits decisions to a database or remote CRM.
