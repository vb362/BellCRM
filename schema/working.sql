-- Shared local schema for demo.sqlite and production.sqlite (version 4).
-- Apply to existing databases through migrate_databases.py, not directly.
-- JSON text below stays inside SQLite; scripts do not exchange JSON files.

CREATE TABLE IF NOT EXISTS crm_accounts (
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

CREATE TABLE IF NOT EXISTS crm_contacts (
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

CREATE TABLE IF NOT EXISTS website_snapshots (
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

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'complete', 'incomplete', 'failed')),
    locations_found INTEGER NOT NULL DEFAULT 0,
    locations_saved INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    run_type TEXT NOT NULL DEFAULT 'scraper' CHECK (run_type IN ('scraper', 'pipeline')),
    website_snapshot_id TEXT,
    current_stage TEXT NOT NULL DEFAULT 'scraping' CHECK (current_stage IN ('scraping', 'normalization', 'matching', 'complete')),
    scraping_status TEXT NOT NULL DEFAULT 'pending' CHECK (scraping_status IN ('pending', 'running', 'complete', 'incomplete', 'failed')),
    normalization_status TEXT NOT NULL DEFAULT 'pending' CHECK (normalization_status IN ('pending', 'running', 'complete', 'incomplete', 'failed')),
    matching_status TEXT NOT NULL DEFAULT 'pending' CHECK (matching_status IN ('pending', 'running', 'complete', 'incomplete', 'failed')),
    crm_records_normalized INTEGER NOT NULL DEFAULT 0 CHECK (crm_records_normalized >= 0),
    website_records_normalized INTEGER NOT NULL DEFAULT 0 CHECK (website_records_normalized >= 0),
    new_proposals INTEGER NOT NULL DEFAULT 0 CHECK (new_proposals >= 0),
    existing_proposals INTEGER NOT NULL DEFAULT 0 CHECK (existing_proposals >= 0),
    decided_proposals_skipped INTEGER NOT NULL DEFAULT 0 CHECK (decided_proposals_skipped >= 0)
);

CREATE TABLE IF NOT EXISTS normalized_crm_accounts (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    account_id TEXT NOT NULL REFERENCES crm_accounts(account_id),
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
    normalized_name TEXT,
    normalized_street TEXT,
    normalized_city TEXT,
    normalized_state TEXT,
    address_fields_present INTEGER NOT NULL CHECK (address_fields_present IN (0, 1)),
    CHECK (address_fields_present = CASE WHEN
        normalized_street IS NOT NULL AND trim(normalized_street) <> '' AND
        normalized_city IS NOT NULL AND trim(normalized_city) <> '' AND
        normalized_state IS NOT NULL AND trim(normalized_state) <> ''
        THEN 1 ELSE 0 END),
    PRIMARY KEY (run_id, account_id)
);

CREATE TABLE IF NOT EXISTS normalized_website_locations (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    snapshot_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    normalized_name TEXT,
    normalized_street TEXT,
    normalized_city TEXT,
    normalized_state TEXT,
    address_fields_present INTEGER NOT NULL CHECK (address_fields_present IN (0, 1)),
    CHECK (address_fields_present = CASE WHEN
        normalized_street IS NOT NULL AND trim(normalized_street) <> '' AND
        normalized_city IS NOT NULL AND trim(normalized_city) <> '' AND
        normalized_state IS NOT NULL AND trim(normalized_state) <> ''
        THEN 1 ELSE 0 END),
    PRIMARY KEY (run_id, snapshot_id, source_url),
    FOREIGN KEY (snapshot_id, source_url) REFERENCES website_snapshots(snapshot_id, source_url)
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT NOT NULL PRIMARY KEY,
    proposal_key TEXT NOT NULL UNIQUE CHECK (length(trim(proposal_key)) > 0),
    classification TEXT NOT NULL CHECK (length(trim(classification)) > 0),
    account_id TEXT REFERENCES crm_accounts(account_id),
    source_url TEXT,
    proposed_changes TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(proposed_changes) AND json_type(proposed_changes) = 'array'),
    explanation TEXT NOT NULL,
    supporting_evidence TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(supporting_evidence) AND json_type(supporting_evidence) = 'object'),
    created_at TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    depends_on_proposal_id TEXT REFERENCES proposals(proposal_id)
);

CREATE TABLE IF NOT EXISTS run_proposals (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
    PRIMARY KEY (run_id, proposal_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT NOT NULL PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(proposal_id),
    choice TEXT NOT NULL CHECK (choice IN ('approved', 'rejected', 'reviewed')),
    approved_changes TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(approved_changes) AND json_type(approved_changes) = 'array'),
    reviewer_note TEXT,
    decided_at TEXT NOT NULL,
    submitted_at TEXT,
    CHECK (choice = 'approved' OR json_array_length(approved_changes) = 0)
);

CREATE TABLE IF NOT EXISTS change_history (
    change_id TEXT NOT NULL PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
    attempted_at TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('succeeded', 'failed')),
    before_values TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(before_values) AND json_type(before_values) = 'array'),
    after_values TEXT
        CHECK (after_values IS NULL OR (json_valid(after_values) AND json_type(after_values) = 'array')),
    error TEXT,
    CHECK ((result = 'succeeded' AND after_values IS NOT NULL AND error IS NULL)
        OR (result = 'failed' AND after_values IS NULL AND error IS NOT NULL))
);

-- Failed attempts can repeat, but each decision can succeed only once.
CREATE UNIQUE INDEX IF NOT EXISTS one_success_per_decision
    ON change_history(decision_id) WHERE result = 'succeeded';
CREATE INDEX IF NOT EXISTS change_history_decision ON change_history(decision_id);
CREATE INDEX IF NOT EXISTS run_proposals_proposal ON run_proposals(proposal_id);
CREATE INDEX IF NOT EXISTS proposals_account ON proposals(account_id);
CREATE INDEX IF NOT EXISTS proposals_dependency ON proposals(depends_on_proposal_id);
CREATE INDEX IF NOT EXISTS normalized_crm_account ON normalized_crm_accounts(account_id);
CREATE INDEX IF NOT EXISTS normalized_website_source ON normalized_website_locations(snapshot_id, source_url);

-- History must refer to submitted approvals that actually contain changes.
CREATE TRIGGER IF NOT EXISTS change_history_requires_approval
BEFORE INSERT ON change_history
WHEN NOT EXISTS (
    SELECT 1 FROM decisions WHERE decision_id = NEW.decision_id
    AND choice = 'approved' AND submitted_at IS NOT NULL
    AND json_array_length(approved_changes) > 0
)
BEGIN
    SELECT RAISE(ABORT, 'Change history requires a submitted approval with changes');
END;

-- Submitted decisions and recorded attempts are permanent audit records.
CREATE TRIGGER IF NOT EXISTS submitted_decision_no_update
BEFORE UPDATE ON decisions WHEN OLD.submitted_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Submitted decisions cannot be edited');
END;
CREATE TRIGGER IF NOT EXISTS submitted_decision_no_delete
BEFORE DELETE ON decisions WHEN OLD.submitted_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Submitted decisions cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS change_history_no_update
BEFORE UPDATE ON change_history
BEGIN
    SELECT RAISE(ABORT, 'Change history cannot be edited');
END;
CREATE TRIGGER IF NOT EXISTS change_history_no_delete
BEFORE DELETE ON change_history
BEGIN
    SELECT RAISE(ABORT, 'Change history cannot be deleted');
END;

-- Keep legacy scrape commands compatible with the new run metadata.
CREATE TRIGGER IF NOT EXISTS scraper_run_insert
AFTER INSERT ON runs WHEN NEW.run_type = 'scraper'
BEGIN
    UPDATE runs SET website_snapshot_id = COALESCE(NEW.website_snapshot_id, NEW.run_id),
        scraping_status = NEW.status,
        current_stage = CASE WHEN NEW.status = 'complete' THEN 'complete' ELSE 'scraping' END
    WHERE run_id = NEW.run_id;
END;
CREATE TRIGGER IF NOT EXISTS scraper_run_status
AFTER UPDATE OF status ON runs WHEN NEW.run_type = 'scraper'
BEGIN
    UPDATE runs SET scraping_status = NEW.status,
        current_stage = CASE WHEN NEW.status = 'complete' THEN 'complete' ELSE 'scraping' END
    WHERE run_id = NEW.run_id;
END;

-- Immutable previews; mutable delivery progress is kept separately.
CREATE TABLE IF NOT EXISTS production_plans (
    plan_id TEXT NOT NULL PRIMARY KEY,
    created_at TEXT NOT NULL,
    plan_json TEXT NOT NULL CHECK (json_valid(plan_json)),
    digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('preview','running','paused','complete','invalidated')),
    error TEXT,
    finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_production_plan
    ON production_plans ((1)) WHERE status IN ('running','paused');
CREATE TABLE IF NOT EXISTS production_requests (
    plan_id TEXT NOT NULL REFERENCES production_plans(plan_id),
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','written','verified','failed','uncertain')),
    remote_id TEXT,
    resolved_body TEXT,
    response_json TEXT,
    after_json TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, ordinal)
);
CREATE TRIGGER IF NOT EXISTS production_plan_immutable
BEFORE UPDATE OF plan_json,digest,created_at ON production_plans
BEGIN
    SELECT RAISE(ABORT, 'Saved production calls cannot be edited');
END;
CREATE TRIGGER IF NOT EXISTS production_plan_no_delete
BEFORE DELETE ON production_plans
BEGIN
    SELECT RAISE(ABORT, 'Production submission history cannot be deleted');
END;
