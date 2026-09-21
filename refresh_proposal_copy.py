"""Refresh saved proposal wording without rerunning matching or applying CRM writes.

    python3 refresh_proposal_copy.py --database both

Only proposal titles, explanations and evidence prose are updated. Decisions, operations,
proposal keys, CRM records, and history are preserved. Changed databases are
backed up first; repeated runs do not write unchanged rows.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from normalize_data import DATABASES, START_DATABASE
from proposal_copy import proposal_copy, proposal_explanation


def refresh(database):
    path = Path(database).resolve()
    if path == START_DATABASE.resolve() or (START_DATABASE.exists() and path.samefile(START_DATABASE)):
        raise ValueError('The starting database is protected')
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)) as connection:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            updates = []
            for pid, classification, old_title, old_explanation, raw in connection.execute(
                    'SELECT proposal_id, classification, title, explanation, supporting_evidence FROM proposals'):
                old_evidence = json.loads(raw)
                title, evidence = proposal_copy(classification, old_title, old_evidence)
                explanation = proposal_explanation(old_explanation, evidence)
                if title != old_title or explanation != old_explanation or evidence != old_evidence:
                    serialized = (json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                                  if evidence != old_evidence else raw)
                    updates.append((title, explanation, serialized, pid))
            if not updates:
                return 0, None
            backup_dir = path.parent / 'backups'
            backup_dir.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = backup_dir / f'{path.stem}-before-proposal-copy-{stamp}.sqlite'
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(backup)) as destination:
                    source.backup(destination)
            connection.executemany(
                'UPDATE proposals SET title=?, explanation=?, supporting_evidence=? WHERE proposal_id=?', updates)
            return len(updates), backup


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', choices=['demo', 'production', 'both'], required=True)
    args = parser.parse_args()
    for mode in DATABASES if args.database == 'both' else [args.database]:
        count, backup = refresh(DATABASES[mode])
        print(f'{mode}: updated {count} proposal(s).' + (f' Backup: {backup}' if backup else ''))
