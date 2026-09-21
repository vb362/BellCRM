#!/usr/bin/env python3
"""Run scraping -> normalization -> matching, once each, in the selected database.

    .venv/bin/python run_pipeline.py --database demo

Each step saves its output in SQLite. Only the database path and run ID are
passed between steps. Nothing applies CRM changes or calls the CRM API.
"""

import argparse
from contextlib import closing
import json
import sqlite3
from uuid import uuid4

from scrape_website import scrape_database, now
from normalize_data import DATABASES, open_database, normalize_database
from match_records import match_database


class PipelineStopped(RuntimeError):
    """A step did not finish; later steps must not execute."""

    def __init__(self, run_id, stage, message, status='failed'):
        super().__init__(f'Run {run_id} stopped at {stage}: {message}')
        self.run_id, self.stage, self.status = run_id, stage, status


def start_pipeline(database_path):
    """Check setup and create one shared run before any network requests."""
    with closing(open_database(database_path)) as connection:
        if connection.execute('PRAGMA user_version').fetchone()[0] not in (3, 4):
            raise ValueError('Pipeline requires schema version 3. Run migrate_databases.py first.')
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            from review_service import require_no_submission
            require_no_submission(connection)
            if not connection.execute('SELECT 1 FROM crm_accounts LIMIT 1').fetchone():
                raise ValueError('The selected database has no CRM accounts. Load them before running the pipeline.')
            active = connection.execute("SELECT run_id FROM runs WHERE run_type='pipeline' AND status='running' LIMIT 1").fetchone()
            if active:
                raise ValueError(f"Pipeline {active['run_id']} is already running; resolve it before starting another.")
            run_id = str(uuid4())
            connection.execute('''INSERT INTO runs
                (run_id,started_at,status,run_type,website_snapshot_id,current_stage)
                VALUES (?,?,'running','pipeline',?,'scraping')''', (run_id, now(), run_id))
        return run_id


def check_stage(database_path, run_id, stage):
    """Do not advance merely because a function returned; check its saved result."""
    with closing(open_database(database_path)) as connection:
        row = connection.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if (not row or row['run_type'] != 'pipeline' or row['status'] != 'running'
                or row['website_snapshot_id'] != run_id or row[stage + '_status'] != 'complete'):
            raise PipelineStopped(run_id, stage, 'The stage did not save a complete result for this run.')


def finish_pipeline(database_path, run_id, status, stage, error=None):
    """The runner owns overall status; stage functions own their saved outputs."""
    with closing(open_database(database_path)) as connection:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            run = connection.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if not run or run['run_type'] != 'pipeline':
                raise ValueError('Pipeline run is missing or has the wrong type')
            errors = json.loads(run['errors'])
            if status == 'complete':
                if any(run[s + '_status'] != 'complete' for s in ('scraping', 'normalization', 'matching')):
                    raise ValueError('Cannot complete a pipeline with unfinished stages')
                connection.execute("UPDATE runs SET status='complete',finished_at=?,current_stage='complete' WHERE run_id=?", (now(), run_id))
            else:
                errors.append(dict(stage=stage, severity='error', code='pipeline_stopped',
                                   message=str(error) or 'Pipeline interrupted.'))
                stage_status = run[stage + '_status']
                if stage_status not in ('incomplete', 'failed'):
                    stage_status = 'failed'
                # stage is an internal constant, never a CLI/SQL identifier input.
                connection.execute(f'''UPDATE runs SET status=?,finished_at=?,current_stage=?,
                    {stage}_status=?,errors=? WHERE run_id=?''',
                    (status, now(), stage, stage_status, json.dumps(errors, ensure_ascii=False), run_id))


def run_pipeline(database_path, run_id=None):
    """The actual workflow: call each script in order, using the same saved run."""
    # The web server creates the row before returning its ID to the browser.
    # CLI callers still create their own run here.
    if run_id is None:
        run_id = start_pipeline(database_path)
    else:
        with closing(open_database(database_path)) as connection:
            row = connection.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if not row or row['run_type'] != 'pipeline' or row['status'] != 'running' or row['scraping_status'] != 'pending':
                raise ValueError('Expected a newly created pipeline run')
    stage = 'scraping'
    try:
        print(f'Pipeline run: {run_id}\n1/3 Scraping', flush=True)
        scrape = scrape_database(database_path, run_id=run_id)
        if scrape['interrupted']:
            raise KeyboardInterrupt()
        if scrape['status'] != 'complete' or scrape['run_id'] != run_id:
            raise PipelineStopped(run_id, stage, 'Scrape failed or was incomplete; later steps were not started.',
                                  status='incomplete' if scrape['status'] == 'incomplete' else 'failed')
        check_stage(database_path, run_id, stage)

        stage = 'normalization'
        print('2/3 Normalizing', flush=True)
        normalized = normalize_database(database_path, run_id)
        check_stage(database_path, run_id, stage)

        stage = 'matching'
        print('3/3 Matching', flush=True)
        matched = match_database(database_path, run_id)
        check_stage(database_path, run_id, stage)

        finish_pipeline(database_path, run_id, 'complete', stage)
        return dict(run_id=run_id, status='complete', scrape=scrape, normalization=normalized, matching=matched)
    except (Exception, KeyboardInterrupt) as error:
        status = error.status if isinstance(error, PipelineStopped) else 'failed'
        try:
            finish_pipeline(database_path, run_id, status, stage, error)
        except (ValueError, OSError, sqlite3.Error) as recording_error:
            # Preserve the original failure even if a full disk prevents logging.
            error.add_note(f'Could not finalize the pipeline row: {recording_error}')
        if isinstance(error, (KeyboardInterrupt, PipelineStopped)):
            raise
        stopped = PipelineStopped(run_id, stage, str(error))
        for note in getattr(error, '__notes__', []):
            stopped.add_note(note)
        raise stopped from error


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database', required=True, choices=DATABASES)
    args = parser.parse_args()
    try:
        result = run_pipeline(DATABASES[args.database])
    except KeyboardInterrupt:
        parser.exit(130, 'Pipeline interrupted; later steps were not started.\n')
    except (PipelineStopped, ValueError, OSError, sqlite3.Error) as error:
        notes = ''.join(f'\n{note}' for note in getattr(error, '__notes__', []))
        parser.exit(1, f'{error}{notes}\n')
    matched = result['matching']
    print(f"Complete: {result['run_id']} | {matched['new_proposals']} new proposals, "
          f"{matched['existing_proposals']} reused, {matched['decided_proposals_skipped']} already decided.")
    print('CRM records are unchanged. Proposals are ready for human review.')


if __name__ == '__main__':
    main()
