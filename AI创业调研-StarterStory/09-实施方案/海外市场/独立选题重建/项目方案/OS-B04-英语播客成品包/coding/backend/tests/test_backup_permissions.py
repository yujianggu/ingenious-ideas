import os
import stat
from app.store import Store
from app.backup import backup, restore


def assert_private(root):
    assert stat.S_IMODE(root.stat().st_mode)==0o700
    for path in root.rglob('*'):
        assert stat.S_IMODE(path.stat().st_mode)==(0o700 if path.is_dir() else 0o600), str(path)


def test_backup_restore_private_before_server_start(tmp_path):
    previous=os.umask(0o022)
    try:
        source=tmp_path/'live'; store=Store(source)
        # Deliberately permissive input proves copy2 cannot carry permissions through.
        (store.files/'asset').write_bytes(b'private video')
        (store.files/'asset').chmod(0o644)
        with store.tx() as db: db.execute('INSERT INTO files VALUES(?,?)',('asset','asset'))
        snapshot=tmp_path/'snapshot'; restored=tmp_path/'restored'
        backup(source,snapshot)
        assert_private(snapshot)
        restore(snapshot,restored)
        assert_private(restored)
        assert (restored/'files'/'asset').read_bytes()==b'private video'
    finally: os.umask(previous)
