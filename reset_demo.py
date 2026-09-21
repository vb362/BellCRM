"""Back up and transactionally restore Test mode. Never replace an open DB file."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile

from migrate_databases import SCHEMA_PATH, VERSION, statements, table_names, validate

BASE_TABLES = ('crm_accounts', 'crm_contacts', 'website_snapshots')


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def save_reset_backup(path, folder):
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    with tempfile.NamedTemporaryFile(prefix=f'demo-before-reset-{stamp}-',
                                     suffix='.sqlite', dir=folder, delete=False) as file:
        backup = Path(file.name)
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        return backup
    except Exception:
        backup.unlink(missing_ok=True)
        raise


def reset_demo(database, baseline, production_database):
    """Restore only the configured demo, retaining its previous contents in a backup.

    A SQLite write transaction serializes this with reviews and external pipeline
    starts. Schema replacement and data restoration commit together or roll back.
    Readers retain a consistent snapshot; no open connection can write an old inode.
    """
    path = Path(database).resolve()
    baseline = Path(baseline).resolve()
    production = Path(production_database).resolve()
    if path.name != 'demo.sqlite' or not path.is_file():
        raise ValueError('Reset is only available for the demo.sqlite test database.')
    if not baseline.is_file():
        raise ValueError('The starting database is missing. Test data was not changed.')
    if path.samefile(baseline) or path == production or (production.exists() and path.samefile(production)):
        raise ValueError('The baseline and production databases are protected from reset.')

    script = list(statements(SCHEMA_PATH.read_text()))
    with closing(sqlite3.connect(':memory:')) as prepared:
        prepared.execute('PRAGMA foreign_keys = ON')
        for statement in script:
            prepared.execute(statement)
        with closing(sqlite3.connect(baseline.as_uri() + '?mode=ro', uri=True)) as source:
            source.execute('BEGIN')
            if table_names(source) != set(BASE_TABLES) or source.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('The starting database is invalid. Test data was not changed.')
            for table in BASE_TABLES:
                columns = [row[1] for row in prepared.execute(f'PRAGMA table_info({quote(table)})')]
                if columns != [row[1] for row in source.execute(f'PRAGMA table_info({quote(table)})')]:
                    raise ValueError('The starting database has an unexpected schema.')
                names = ', '.join(map(quote, columns))
                prepared.executemany(f'INSERT INTO {quote(table)} ({names}) VALUES ({", ".join("?" for _ in columns)})',
                                     source.execute(f'SELECT {names} FROM {quote(table)}'))
            if not prepared.execute('SELECT 1 FROM crm_accounts LIMIT 1').fetchone():
                raise ValueError('The starting database has no CRM accounts. Test data was not changed.')
        prepared.commit()

        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)) as target:
            # Disable only on this connection, before the transaction, for schema
            # replacement. Validate all restored foreign keys before committing.
            target.execute('PRAGMA foreign_keys = OFF')
            target.execute('BEGIN IMMEDIATE')
            try:
                existing = table_names(target)
                # Older Test mode installs predate the production ledger tables.
                # Validate that exact older schema without migrating live data first.
                if target.execute('PRAGMA user_version').fetchone()[0] == 3 and not existing & {'production_plans', 'production_requests'}:
                    with closing(sqlite3.connect(':memory:')) as legacy:
                        prepared.backup(legacy)
                        legacy.execute('DROP TABLE production_requests')
                        legacy.execute('DROP TABLE production_plans')
                        validate(target, legacy)
                else:
                    validate(target, prepared)
                if target.execute("SELECT 1 FROM runs WHERE status='running'").fetchone():
                    raise ValueError('A run is active. Wait for it to finish before resetting Test mode.')
                if 'production_plans' in existing and target.execute('SELECT 1 FROM production_plans').fetchone():
                    raise ValueError('This database contains production submissions and cannot be reset.')
                backup = save_reset_backup(path, path.parent / 'backups')
                for (name,) in target.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                    target.execute(f'DROP TRIGGER {quote(name)}')
                for name in table_names(target):
                    target.execute(f'DROP TABLE {quote(name)}')
                for statement in script:
                    target.execute(statement)
                for table in BASE_TABLES:
                    columns = [row[1] for row in prepared.execute(f'PRAGMA table_info({quote(table)})')]
                    names = ', '.join(map(quote, columns))
                    target.executemany(f'INSERT INTO {quote(table)} ({names}) VALUES ({", ".join("?" for _ in columns)})',
                                       prepared.execute(f'SELECT {names} FROM {quote(table)}'))
                validate(target, prepared)
                target.execute(f'PRAGMA user_version = {VERSION}')
                target.commit()
            except Exception:
                target.rollback()
                raise
    return backup
