#!/usr/bin/env python3
"""Add the agreed local workflow schema without changing CRM or website records.

    python3 migrate_databases.py --database demo
    python3 migrate_databases.py --database production
    python3 migrate_databases.py --database both

Use the demo command again after restoring demo.sqlite from start.sqlite.
No reset, scraping, normalization, matching, or API access happens here.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile


PROJECT_DIR = Path(__file__).resolve().parent
START_DB = PROJECT_DIR / 'data' / 'start.sqlite'
SCHEMA_PATH = PROJECT_DIR / 'schema' / 'working.sql'
VERSION = 4


def statements(script):
    """Execute DDL without executescript's implicit transaction commit."""
    pending = ''
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ''
    if pending.strip():
        raise ValueError('Incomplete SQL at the end of the schema file')


def run_column_definitions(script):
    """The added run columns have one source of truth: working.sql."""
    block = script.split('CREATE TABLE IF NOT EXISTS runs (', 1)[1].split('\n);', 1)[0]
    for line in block.splitlines():
        definition = line.strip().removesuffix(',')
        if definition:
            yield definition.split()[0], definition


def table_names(connection):
    return {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def validate(connection, expected):
    """Reject an unexpected schema or damaged data before committing."""
    if table_names(connection) != table_names(expected):
        raise ValueError('Database tables do not match the shared working schema')
    for table in table_names(expected):
        actual = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        wanted = expected.execute(f'PRAGMA table_info("{table}")').fetchall()
        if actual != wanted:
            raise ValueError(f'Unexpected columns in {table}')
        if connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall() != expected.execute(
                f'PRAGMA foreign_key_list("{table}")').fetchall():
            raise ValueError(f'Unexpected foreign keys in {table}')
    expected_objects = expected.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND sql IS NOT NULL ORDER BY name"
    ).fetchall()
    actual_objects = connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND sql IS NOT NULL ORDER BY name"
    ).fetchall()
    if actual_objects != expected_objects:
        raise ValueError('Unexpected indexes or triggers')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Foreign-key validation failed')
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('SQLite integrity check failed')


def save_backup(path, backup_dir):
    """Use SQLite's backup API so committed journal/WAL data is included."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    with tempfile.NamedTemporaryFile(prefix=f'{path.stem}-before-v{VERSION}-{stamp}-',
                                     suffix='.sqlite', dir=backup_dir, delete=False) as file:
        destination = Path(file.name)
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(destination)) as backup:
                source.backup(backup)
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def migrate_database(path, mode, backup_dir, protected_start=START_DB):
    if mode not in ('demo', 'production'):
        raise ValueError('Only demo or production databases may be migrated')
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f'Database does not exist: {path}')
    if path.name == 'start.sqlite' or (protected_start.exists() and path.samefile(protected_start)):
        raise ValueError('start.sqlite is protected and cannot be migrated')

    script = SCHEMA_PATH.read_text(encoding='utf-8')
    with closing(sqlite3.connect(':memory:')) as expected:
        for statement in statements(script):
            expected.execute(statement)
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)) as connection:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute('BEGIN IMMEDIATE')
            try:
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                if version == VERSION:
                    validate(connection, expected)
                    connection.rollback()
                    return None
                if version not in (1, 2, 3):
                    raise ValueError(f'Unsupported schema version: {version}')
                existing = table_names(connection)
                required = {'website_snapshots'}
                if mode == 'demo':
                    required |= {'crm_accounts', 'crm_contacts'}
                if not required.issubset(existing):
                    raise ValueError('Missing starting data tables')
                legacy = existing & {'crm_snapshots', 'contact_snapshots'}
                if legacy and mode != 'production':
                    raise ValueError('Snapshot conversion applies only to production')
                for table in legacy:
                    if connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                        raise ValueError(f'Refusing to remove nonempty table: {table}')
                if mode == 'production' and not (
                        {'crm_snapshots', 'contact_snapshots'}.issubset(existing)
                        or {'crm_accounts', 'crm_contacts'}.issubset(existing)):
                    raise ValueError('Missing production account/contact tables')
                if 'runs' in existing and connection.execute(
                        "SELECT 1 FROM runs WHERE status = 'running' LIMIT 1").fetchone():
                    raise ValueError('Finish or resolve running work before migrating')

                # This connection holds the write reservation. A separate read-only
                # connection can back up the pre-migration state while writers wait.
                backup = save_backup(path, Path(backup_dir))
                if version == 2:
                    connection.execute("ALTER TABLE proposals ADD COLUMN title TEXT NOT NULL DEFAULT ''")
                    connection.execute('ALTER TABLE proposals ADD COLUMN depends_on_proposal_id TEXT REFERENCES proposals(proposal_id)')
                for table in sorted(legacy):
                    connection.execute(f'DROP TABLE "{table}"')

                # Extend runs before installing triggers that reference new columns.
                if 'runs' in existing:
                    columns = {r[1] for r in connection.execute('PRAGMA table_info(runs)')}
                    for name, definition in run_column_definitions(script):
                        if name not in columns:
                            connection.execute(f'ALTER TABLE runs ADD COLUMN {definition}')
                for statement in statements(script):
                    connection.execute(statement)
                if 'runs' in existing and version == 1:
                    connection.execute("""
                        UPDATE runs SET website_snapshot_id = run_id,
                            scraping_status = status,
                            current_stage = CASE WHEN status = 'complete' THEN 'complete' ELSE 'scraping' END
                        WHERE run_type = 'scraper'
                    """)
                validate(connection, expected)
                connection.execute(f'PRAGMA user_version = {VERSION}')
                connection.commit()
                return backup
            except Exception:
                connection.rollback()
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database', required=True, choices=('demo', 'production', 'both'))
    args = parser.parse_args()
    modes = ('demo', 'production') if args.database == 'both' else (args.database,)
    demo = PROJECT_DIR / 'data' / 'demo.sqlite'
    production = PROJECT_DIR / 'data' / 'production.sqlite'
    try:
        if demo.exists() and production.exists() and demo.samefile(production):
            raise ValueError('Demo and production must be separate files')
        for mode in modes:
            path = PROJECT_DIR / 'data' / f'{mode}.sqlite'
            backup = migrate_database(path, mode, PROJECT_DIR / 'data' / 'backups')
            print(f'{mode}: ' + (f'migrated to version {VERSION}. Backup: {backup}' if backup else 'already up to date; no changes.'))
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f'Migration stopped: {error}\n')


if __name__ == '__main__':
    main()
