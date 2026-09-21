# Local databases

- `start.sqlite`: prepared CRM baseline included for Test mode. Keep it unchanged.
- `demo.sqlite`: local working copy, created from the baseline by the root README setup.
- `production.sqlite`: separate local CRM mirror and submission history used by Production mode.
- `backups/`: local migration backups.

Working databases and backups are excluded from Git to avoid committing changing application state. The prepared baseline is included.
