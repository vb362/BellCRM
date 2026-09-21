# Run the Bellhaven test app

From the Bellhaven project folder:

```sh
.venv/bin/python server.py
```

Open **http://localhost:8000**. Keep the terminal running; use Ctrl+C to stop it. If a pipeline is still active, the server waits for it to finish safely. Use `--port 8002` if you need another port.

## What happens

1. Choose **Test mode**. Other screens are locked until you choose. This starts the real scraping → normalization → matching pipeline against `data/demo.sqlite` and opens **Runs**. If a pipeline is already running, the app shows that run instead of starting another.
2. Runs displays saved stage statuses, collected-location counts, errors, and proposal counts. After completion, go to **Home** to review the proposals. Matches with no proposed CRM changes are automatically recorded and remembered in the database, without requiring approval or Submit. These automatic no-change matches are excluded from Home request lists and counts (including Closed), and hidden from the Decisions screen. Duplicate-dependent matches wait for the duplicate decision first. Other correction cards stay hidden until their duplicate-resolution decision is submitted.
3. **Approve**, **Decline**, and manual edits save draft decisions to SQLite. They do not change accounts yet. **Review and submit** shows the selected decisions; **Submit** applies approved account and contact changes and records decisions/history in one transaction. If any proposal is stale or blocked, the entire submission rolls back and shows an error.
4. **Decisions** shows drafts, submitted decisions, reviewer notes, and before/after values. Record links open the online CRM. **Sources** shows the latest completed website snapshot. Reloading requires choosing the mode again, but saved decisions remain.

Duplicate resolution previews all contacts retained on the survivor and requires a phone choice when facility numbers differ. Contacts on other accounts are copied with new IDs and preserved details/status; active originals become inactive after verified creation. The chosen phone is applied, losers are marked and linked, and their corrections are dismissed. The survivor's prepared proposal is refreshed against its consolidated contacts and the chosen phone before separate review. Explicitly keeping both releases both proposals without contact or phone changes; declining duplicate resolution leaves them blocked. Matching does not run again after submission. CHOW creates the new account and links the old one together, preserving the old account's other fields.

## Boundaries and implementation

- Only `demo.sqlite` is used. `start.sqlite` and `production.sqlite` are untouched; no remote CRM writes occur. The scraper reads the assessment website.
- Production, reset, and scheduling controls are unavailable until implemented.
- The server binds to `127.0.0.1` for local use. It is not a hosted or multi-user deployment.
- `server.py` serves the existing HTML and its local endpoints. `review_service.py` handles saved decisions and transactions. `run_pipeline.py` remains the three-step runner.
- API requests need the Test mode session. The server validates proposal versions, decision revisions, duplicate dependencies, and current account values and the saved contact set. Retrying a submitted decision does not apply it again.
- To remove a draft, use **Delete** in the submission dialog. Submitted decisions are permanent. If account data changed after review, remove the draft and run the pipeline again to regenerate current evidence.
- Rebuild HTML after frontend changes with `python3 mistral-replica/buildcrmui.py`. Opening the standalone HTML directly explains how to start the server; connected actions require localhost.

Tests: `.venv/bin/python -B -m unittest discover -s tests -q`. Tests use temporary databases, including the HTTP and transaction checks.

CRM links open the online assessment account in a new tab, using its existing account ID. There is no local-record viewer. Test-created accounts have no online link. Website links open the corresponding facility page; absence cards link to the facility directory and label it **directory**. Test changes do not change the online record.

Website comparison rows use **Bellhaven Senior Living (Parent Account)** as their parent, because these are Bellhaven website facilities. The CRM column continues to show the actual saved CRM parent.

Expanded comparison tables show every saved account field and the linked contacts, with differing values first. Website comparisons also show all facility fields and source details; internal normalization fields and raw HTML are omitted. Account values come from the proposal evidence; contact details for new correction proposals are saved with the review evidence. Duplicate cards show current contacts.

Only fields supported by both compared sources are highlighted and sorted as differences. Source-specific fields remain visible without highlighting; the unsupported side shows a dash. Duplicate account comparisons still compare all account/contact fields. The explanation appears before the comparison table.

Comparison highlighting uses the same Python name/address normalization as matching. Differing facility names, address fields, and parents come before other differences. Only the two source cells are shaded; proposed values are bold on a neutral background. New-account values are labeled separately from updates. Ownership checks use plain text, with no colored box.

Phone, care offerings and administrator changes are included after matching, including for
otherwise perfect matches. Administrator rows compare the website name with active CRM
Administrator contacts. Expanded cards list each proposed contact creation, role change,
reactivation or deactivation. Ambiguous contacts require selecting the person to reuse
(or creating a new contact), checking former contacts to deactivate, and confirming the
selection before approval. Submission and Decisions include contact writes and history.
