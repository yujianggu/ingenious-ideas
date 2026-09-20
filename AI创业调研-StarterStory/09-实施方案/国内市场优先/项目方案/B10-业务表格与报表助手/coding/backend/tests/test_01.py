import hashlib
import sqlite3
from pathlib import Path

import pytest
from service_b10.store import Store


def test_isolated_immutable(tmp_path):
    s = Store(tmp_path)
    s.create("a", "t1")
    s.create("b", "t1")
    digest = s.put("a", "t1", "source.txt", b"first")
    assert s.put("a", "t1", "source.txt", b"first") == digest
    assert s.read("a", "t1", digest) == b"first"
    with pytest.raises(ValueError, match="NOT_FOUND"):
        s.read("b", "t1", digest)
    with pytest.raises(ValueError, match="INVALID_PATH"):
        s.put("a", "t1", "../escape", b"x")


def test_symlink_escape(tmp_path):
    s = Store(tmp_path / "safe")
    s.create("a", "t1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "safe" / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="INVALID_PATH"):
        s.create("escape", "t2")


def test_job_lock_can_reject_a_concurrent_backup(tmp_path):
    store = Store(tmp_path)
    backup_user = Store(tmp_path)

    with store.job_lock():
        with pytest.raises(ValueError, match="JOB_BUSY"):
            with backup_user.job_lock(blocking=False):
                pass


class _CommitFailureConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.connection.rollback()
        self.connection.close()
        if exc_type is None:
            raise sqlite3.OperationalError("injected commit failure")
        return False

    def __getattr__(self, name):
        return getattr(self.connection, name)


def test_commit_failure_cleans_owned_blob_but_preserves_preexisting(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    store.create("a", "new")
    store.create("a", "existing")
    data = b"original"
    digest = hashlib.sha256(data).hexdigest()
    new_blob = tmp_path / "a" / "new" / "blobs" / digest
    existing_blob = tmp_path / "a" / "existing" / "blobs" / digest
    existing_blob.write_bytes(data)
    real_connect = sqlite3.connect

    def failing_connect(path):
        return _CommitFailureConnection(real_connect(path))

    monkeypatch.setattr("service_b10.store.sqlite3.connect", failing_connect)
    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        store.put("a", "new", "source.txt", data)
    assert not new_blob.exists()
    with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
        store.put("a", "existing", "source.txt", data)
    assert existing_blob.read_bytes() == data

    monkeypatch.undo()
    assert store.put("a", "new", "source.txt", data) == digest
    assert store.put("a", "existing", "source.txt", data) == digest


@pytest.mark.parametrize("failure_stage", ["write", "flush"])
def test_partial_write_or_flush_failure_cleans_owned_blob_and_retry_works(
    tmp_path, monkeypatch, failure_stage
):
    store = Store(tmp_path)
    store.create("a", "t1")
    data = b"complete"
    digest = hashlib.sha256(data).hexdigest()
    blob_path = tmp_path / "a" / "t1" / "blobs" / digest
    real_open = Path.open
    injected = False

    class PartialWriter:
        def __init__(self, file_object):
            self.file_object = file_object

        def __enter__(self):
            self.file_object.__enter__()
            return self

        def write(self, value):
            if failure_stage == "write":
                self.file_object.write(value[:1])
                raise OSError("injected write failure")
            return self.file_object.write(value)

        def flush(self):
            if failure_stage == "flush":
                raise OSError("injected flush failure")
            return self.file_object.flush()

        def __exit__(self, *args):
            return self.file_object.__exit__(*args)

    def failing_open(path, mode="r", *args, **kwargs):
        nonlocal injected
        file_object = real_open(path, mode, *args, **kwargs)
        if path == blob_path and mode == "xb" and not injected:
            injected = True
            return PartialWriter(file_object)
        return file_object

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError, match=f"injected {failure_stage} failure"):
        store.put("a", "t1", "source.txt", data)
    assert not blob_path.exists()
    assert store.put("a", "t1", "source.txt", data) == digest
    assert store.read("a", "t1", digest) == data


def test_persisted_bytes_are_verified_before_registration(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.create("a", "t1")
    data = b"expected"
    digest = hashlib.sha256(data).hexdigest()
    blob_path = tmp_path / "a" / "t1" / "blobs" / digest
    real_open = Path.open
    injected = False

    class CorruptingWriter:
        def __init__(self, file_object):
            self.file_object = file_object

        def __enter__(self):
            self.file_object.__enter__()
            return self

        def write(self, value):
            return self.file_object.write(b"corrupt")

        def flush(self):
            return self.file_object.flush()

        def __exit__(self, *args):
            return self.file_object.__exit__(*args)

    def corrupting_open(path, mode="r", *args, **kwargs):
        nonlocal injected
        file_object = real_open(path, mode, *args, **kwargs)
        if path == blob_path and mode == "xb" and not injected:
            injected = True
            return CorruptingWriter(file_object)
        return file_object

    monkeypatch.setattr(Path, "open", corrupting_open)
    with pytest.raises(ValueError, match="CORRUPT_BLOB"):
        store.put("a", "t1", "source.txt", data)
    assert not blob_path.exists()
    assert store.put("a", "t1", "source.txt", data) == digest
    assert store.read("a", "t1", digest) == data
