-- Original version-1 baseline schema. Never apply to the prepared start.sqlite.
-- Working copies are extended to version 2 by migrate_databases.py.
-- Optional fields remain nullable; original empty strings can be preserved.

BEGIN;

CREATE TABLE crm_accounts (
    account_id TEXT NOT NULL PRIMARY KEY,
    name TEXT,
    parent_id TEXT,
    parent_name TEXT,
    billing_street TEXT,
    billing_city TEXT,
    billing_state TEXT,
    billing_zip TEXT,
    care_type TEXT,
    status TEXT,
    phone TEXT,
    lifetime_revenue NUMERIC,
    outstanding_ar NUMERIC,
    chow_current_account TEXT,
    duplicate_of_account TEXT,
    note TEXT,
    created_by_candidate INTEGER CHECK (created_by_candidate IN (0, 1)),
    updated_at TEXT
);

CREATE TABLE crm_contacts (
    contact_id TEXT NOT NULL PRIMARY KEY,
    account_id TEXT,
    name TEXT,
    title TEXT,
    email TEXT,
    phone TEXT,
    is_active INTEGER CHECK (is_active IN (0, 1)),
    created_by_candidate INTEGER CHECK (created_by_candidate IN (0, 1)),
    updated_at TEXT
);

-- One location per source URL in each website snapshot.
-- care_offerings holds a JSON list, preserving every offering.
CREATE TABLE website_snapshots (
    snapshot_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    name TEXT,
    street TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    care_offerings TEXT,
    phone TEXT,
    administrator TEXT,
    raw_html TEXT,
    PRIMARY KEY (snapshot_id, source_url)
);

PRAGMA user_version = 1;

COMMIT;
