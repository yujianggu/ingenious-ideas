from __future__ import annotations

import fcntl
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_PATH_PART_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


def checked_child(root: Path, *parts: str) -> Path:
    if not all(
        _PATH_PART_PATTERN.fullmatch(part) and part not in (".", "..")
        for part in parts
    ):
        raise ValueError("INVALID_PATH")
    resolved_root = root.resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(resolved_root):
        raise ValueError("INVALID_PATH")
    return path


def blob_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checked_id(value: str) -> str:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError("INVALID_PATH")
    return value


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        self.db_path = checked_child(self.root, "store.sqlite3")
        self.lock_path = checked_child(self.root, "job.lock")
        with self.job_lock():
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        client TEXT NOT NULL,
                        task TEXT NOT NULL,
                        PRIMARY KEY (client, task)
                    );
                    CREATE TABLE IF NOT EXISTS blobs (
                        client TEXT NOT NULL,
                        task TEXT NOT NULL,
                        digest TEXT NOT NULL,
                        name TEXT NOT NULL,
                        PRIMARY KEY (client, task, digest),
                        FOREIGN KEY (client, task)
                            REFERENCES tasks (client, task)
                    );
                    """
                )

    @contextmanager
    def job_lock(self, *, blocking: bool = True) -> Iterator[None]:
        """Hold the process-independent lock shared by mutations and backups."""
        with self.lock_path.open("a+b") as lock_file:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(lock_file.fileno(), flags)
            except BlockingIOError as error:
                raise ValueError("JOB_BUSY") from error
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def create(self, client: str, task: str) -> None:
        client = _checked_id(client)
        task = _checked_id(task)
        task_dir = checked_child(self.root, client, task)
        with self.job_lock():
            task_dir.mkdir(parents=True, exist_ok=True)
            checked_child(self.root, client, task, "blobs").mkdir(exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO tasks(client, task) VALUES (?, ?)",
                    (client, task),
                )

    def put(
        self, client: str, task: str, name: str, data: bytes
    ) -> str:
        client = _checked_id(client)
        task = _checked_id(task)
        checked_child(self.root, name)
        digest = blob_digest(data)
        created = False
        with self.job_lock():
            blob_path = checked_child(self.root, client, task, "blobs", digest)
            try:
                with self._connect() as connection:
                    if connection.execute(
                        "SELECT 1 FROM tasks WHERE client = ? AND task = ?",
                        (client, task),
                    ).fetchone() is None:
                        raise ValueError("NOT_FOUND")
                    try:
                        blob = blob_path.open("xb")
                    except FileExistsError:
                        if blob_digest(blob_path.read_bytes()) != digest:
                            raise ValueError("CORRUPT_BLOB")
                    else:
                        created = True
                        with blob:
                            blob.write(data)
                            blob.flush()
                        if blob_digest(blob_path.read_bytes()) != digest:
                            raise ValueError("CORRUPT_BLOB")
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO blobs(client, task, digest, name)
                        VALUES (?, ?, ?, ?)
                        """,
                        (client, task, digest, name),
                    )
            except BaseException:
                if created:
                    blob_path.unlink(missing_ok=True)
                raise
        return digest

    def read(self, client: str, task: str, digest: str) -> bytes:
        client = _checked_id(client)
        task = _checked_id(task)
        if not _DIGEST_PATTERN.fullmatch(digest):
            raise ValueError("INVALID_PATH")
        with self._connect() as connection:
            found = connection.execute(
                """
                SELECT 1 FROM blobs
                WHERE client = ? AND task = ? AND digest = ?
                """,
                (client, task, digest),
            ).fetchone()
        if found is None:
            raise ValueError("NOT_FOUND")
        blob_path = checked_child(self.root, client, task, "blobs", digest)
        try:
            data = blob_path.read_bytes()
        except FileNotFoundError as error:
            raise ValueError("NOT_FOUND") from error
        if blob_digest(data) != digest:
            raise ValueError("CORRUPT_BLOB")
        return data

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
