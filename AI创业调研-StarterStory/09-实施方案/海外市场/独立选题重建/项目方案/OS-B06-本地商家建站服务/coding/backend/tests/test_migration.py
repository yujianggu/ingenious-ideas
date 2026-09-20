"""Run the real migration CLI in fresh processes, outside the project cwd."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

CODE = 'B06'
SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'migrate_legacy.py'


def legacy(tmp_path):
    source = tmp_path / 'legacy.sqlite3'
    users = [('alice', 'alice@example.com', 'existing-alice-hash', json.dumps({'id':'alice','name':'Alice','workspaceId':'w1','role':'editor'})),
             ('bob', 'bob@example.com', 'existing-bob-hash', json.dumps({'id':'bob','name':'Bob','workspaceId':'w2','role':'editor'})),
             ('carol', 'carol@example.com', 'existing-carol-hash', json.dumps({'id':'carol','name':'Carol','workspaceId':'w3','role':'editor'}))]
    other = 'C17' if CODE != 'C17' else 'B01'
    records = [('own-one', CODE, 'alice', json.dumps({'id':'own-one','code':CODE,'title':'旅行 / private','revision':7}, ensure_ascii=False)),
               ('own-two', CODE, 'bob', json.dumps({'id':'own-two','code':CODE,'title':'Second','revision':3})),
               ('foreign-one', other, 'alice', json.dumps({'id':'foreign-one','code':other,'title':'Other idea'})),
               ('foreign-two', other, 'carol', json.dumps({'id':'foreign-two','code':other,'title':'Private foreign'}))]
    with sqlite3.connect(source) as db:
        db.executescript('CREATE TABLE users(id TEXT PRIMARY KEY,email TEXT UNIQUE,password TEXT,data TEXT);'
                        'CREATE TABLE sessions(token TEXT PRIMARY KEY,user_id TEXT,expires TEXT);'
                        'CREATE TABLE product_records(id TEXT PRIMARY KEY,code TEXT,owner TEXT,data TEXT);'
                        'CREATE TABLE episodes(id TEXT PRIMARY KEY,workspace TEXT,client TEXT,data TEXT);')
        db.executemany('INSERT INTO users VALUES(?,?,?,?)', users)
        db.executemany('INSERT INTO product_records VALUES(?,?,?,?)', records)
        db.execute("INSERT INTO sessions VALUES('existing-session','alice','2099-01-01')")
        db.execute("INSERT INTO episodes VALUES('episode','w1','alice','{}')")
    return source, users[:2], records[:2]


def invoke(source, destination, tmp_path):
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    # The command must override a live-data env instead of importing into it.
    env[CODE + '_DATA_DIR'] = str(tmp_path / 'live-data-do-not-touch')
    return subprocess.run([sys.executable, str(SCRIPT), str(source), str(destination)],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)


def fingerprint(source):
    return hashlib.sha256(source.read_bytes()).hexdigest()


def test_migration_copies_only_own_records_and_owners_without_sessions(tmp_path):
    source, users, records = legacy(tmp_path)
    before = fingerprint(source)
    destination = tmp_path / 'migrated'
    result = invoke(source, destination, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '2 records and 2 owners' in result.stdout
    with sqlite3.connect(destination / 'project.sqlite3') as db:
        assert db.execute('SELECT id,email,password,data FROM users ORDER BY id').fetchall() == users
        assert db.execute('SELECT id,code,owner,data FROM product_records ORDER BY id').fetchall() == records
        assert db.execute('SELECT * FROM sessions').fetchall() == []
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {'users','sessions','product_records'}
    assert fingerprint(source) == before
    assert not (tmp_path / 'live-data-do-not-touch').exists()
    assert not (tmp_path / 'data').exists()
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / 'project.sqlite3').stat().st_mode & 0o777 == 0o600


def test_migration_refuses_existing_destination_without_changing_it(tmp_path):
    source, _, _ = legacy(tmp_path)
    destination = tmp_path / 'existing'
    destination.mkdir()
    marker = destination / 'project.sqlite3'
    marker.write_bytes(b'existing private user data')
    before = fingerprint(source)
    result = invoke(source, destination, tmp_path)
    assert result.returncode != 0
    assert 'Destination must be a new directory' in result.stderr
    assert marker.read_bytes() == b'existing private user data'
    assert sorted(p.name for p in destination.iterdir()) == ['project.sqlite3']
    assert fingerprint(source) == before
    assert not (tmp_path / 'live-data-do-not-touch').exists()


@pytest.mark.parametrize('invalid', ['orphan', 'invalid-json'])
def test_migration_rejects_invalid_source_before_creating_destination(tmp_path, invalid):
    source, _, _ = legacy(tmp_path)
    with sqlite3.connect(source) as db:
        if invalid == 'orphan':
            db.execute("UPDATE product_records SET owner='missing' WHERE id='own-one'")
        else:
            db.execute("UPDATE product_records SET data='not JSON' WHERE id='own-one'")
    before = fingerprint(source)
    destination = tmp_path / 'invalid-migration'
    result = invoke(source, destination, tmp_path)
    assert result.returncode != 0
    assert 'Migration stopped:' in result.stderr
    assert not destination.exists()
    assert fingerprint(source) == before
    assert not (tmp_path / 'live-data-do-not-touch').exists()
