-- Historical version-1 production schema, retained for migration tests.
-- Current demo and production schema: working.sql + migrate_databases.py.
-- Never use this historical layout to initialize a new working database.

BEGIN;

CREATE TABLE crm_snapshots (
    snapshot_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    account_id TEXT NOT NULL,
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
    updated_at TEXT,
    PRIMARY KEY (snapshot_id, account_id)
);

CREATE TABLE contact_snapshots (
    snapshot_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    account_id TEXT,
    name TEXT,
    title TEXT,
    email TEXT,
    phone TEXT,
    is_active INTEGER CHECK (is_active IN (0, 1)),
    created_by_candidate INTEGER CHECK (created_by_candidate IN (0, 1)),
    updated_at TEXT,
    PRIMARY KEY (snapshot_id, contact_id)
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
