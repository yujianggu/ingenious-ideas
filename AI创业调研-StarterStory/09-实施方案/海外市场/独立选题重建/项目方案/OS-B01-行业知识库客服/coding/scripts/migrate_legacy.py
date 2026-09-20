#!/usr/bin/env python3
"""Copy this idea's records and owners from a legacy database into a NEW directory."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

CODE = 'B01'

def migrate(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Destination must be a new directory; existing data is never overwritten')
    if not source.is_file():
        raise ValueError('Source must be the legacy SQLite file')
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as old:
        old.execute('BEGIN')
        records = old.execute('SELECT id,code,owner,data FROM product_records WHERE code=?', (CODE,)).fetchall()
        owners = sorted({row[2] for row in records})
        users = []
        for owner in owners:
            user = old.execute('SELECT id,email,password,data FROM users WHERE id=?', (owner,)).fetchone()
            if not user:
                raise ValueError('Source contains a record without an owner; nothing copied')
            users.append(user)
        for row in records:
            json.loads(row[3])
    # Reserve the destination atomically. A concurrent migration cannot overwrite it.
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    # Importing the ASGI module creates its default app. Redirect that default to
    # the same new directory so this command never touches the user's live data.
    import os
    os.environ[CODE + '_DATA_DIR'] = str(destination)
    from app.main import create_app
    application = create_app(data_dir=str(destination))
    with application.state.store.tx() as db:
        db.executemany('INSERT INTO users(id,email,password,data) VALUES(?,?,?,?)', users)
        db.executemany('INSERT INTO product_records(id,code,owner,data) VALUES(?,?,?,?)', records)
    return len(records), len(users)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='Legacy episodes.sqlite3 (read only)')
    parser.add_argument('destination', help='New data directory; must not exist')
    args = parser.parse_args()
    try:
        records, users = migrate(args.source, args.destination)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'Migration stopped: {exc}\n')
    print(f'{CODE}: copied {records} records and {users} owners; sessions not copied. Sign in again.')
