from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .exports import (
    BUNDLE_MARKER_BYTES,
    BUNDLE_MARKER_NAME,
    _publish_no_replace,
    verify_bundle,
)
from .store import Store, checked_child


BACKUP_SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 0
ARCHIVE_NAME = "payload.zip"
DATABASE_NAME = "store.sqlite3"
MAX_FILES = 100_000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


def snapshot_database(source: Path, target: Path) -> None:
    """Create a transactionally consistent SQLite snapshot."""
    try:
        with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("CORRUPT_DATABASE")
    except sqlite3.DatabaseError as error:
        raise ValueError("CORRUPT_DATABASE") from error


def backup(store: Store, target: Path) -> Path:
    """Publish a locked, consistent backup and return its manifest path."""
    destination = Path(target)
    if os.path.lexists(destination):
        raise ValueError("TARGET_EXISTS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}-", dir=destination.parent
    ))
    snapshot = temporary / DATABASE_NAME
    archive = temporary / ARCHIVE_NAME
    try:
        with store.job_lock(blocking=False):
            snapshot_database(store.db_path, snapshot)
            sources = _frozen_files(store)
            bundles = _discover_export_bundles(store.root)
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as payload:
                payload.write(snapshot, DATABASE_NAME)
                for relative, source in sources:
                    payload.write(source, relative.as_posix())

        members = _zip_member_records(archive)
        _enforce_limits(members, {
            "max_files": MAX_FILES,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
        })
        archive_record = _file_record(archive)
        manifest_data = {
            "format": "service_b10_backup",
            "schema_version": BACKUP_SCHEMA_VERSION,
            "database": {
                "name": DATABASE_NAME,
                "user_version": _database_user_version(snapshot),
                "schema_sha256": _database_schema_digest(snapshot),
            },
            "archive": dict(archive_record),
            "files": [dict(archive_record)],
            "members": members,
            "bundles": bundles,
            "limits": {
                "max_files": MAX_FILES,
                "max_file_bytes": MAX_FILE_BYTES,
                "max_total_bytes": MAX_TOTAL_BYTES,
            },
        }
        manifest = temporary / "manifest.json"
        manifest.write_bytes(_canonical_bytes(manifest_data))
        snapshot.unlink()
        _publish_no_replace(temporary, destination)
        return destination / "manifest.json"
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def restore(manifest: Path, target: Path) -> Store:
    """Validate a complete backup before atomically publishing a new Store."""
    manifest = Path(manifest)
    destination = Path(target)
    if os.path.lexists(destination):
        raise ValueError("TARGET_EXISTS")
    meta = _read_manifest(manifest)
    archive = _verified_archive(manifest, meta)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}-", dir=destination.parent
    ))
    try:
        _safe_extract(archive, temporary, meta)
        database = temporary / DATABASE_NAME
        _validate_database(database, meta["database"])
        _validate_blob_references(database, temporary)
        _verify_export_bundles(temporary, meta["bundles"])
        _restore_task_directories(database, temporary)
        restored = Store(temporary)
        _publish_no_replace(temporary, destination)
        restored.root = destination.resolve()
        restored.db_path = checked_child(restored.root, DATABASE_NAME)
        restored.lock_path = checked_child(restored.root, "job.lock")
        return restored
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _frozen_files(store: Store) -> list[tuple[PurePosixPath, Path]]:
    excluded = {
        store.db_path.resolve(), store.lock_path.resolve(),
        Path(f"{store.db_path}-wal").resolve(),
        Path(f"{store.db_path}-shm").resolve(),
    }
    files: list[tuple[PurePosixPath, Path]] = []
    for path in sorted(store.root.rglob("*")):
        if path.is_symlink():
            raise ValueError("INVALID_PATH")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("INVALID_PATH")
        resolved = path.resolve()
        if resolved in excluded:
            continue
        relative = PurePosixPath(path.relative_to(store.root).as_posix())
        _safe_member_name(relative.as_posix())
        files.append((relative, path))
    return files


def _read_manifest(manifest: Path) -> dict[str, Any]:
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("INVALID_PATH")
    try:
        meta = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("INVALID_MANIFEST") from None
    if not isinstance(meta, dict) or meta.get("format") != "service_b10_backup":
        raise ValueError("INVALID_MANIFEST")
    version = meta.get("schema_version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != BACKUP_SCHEMA_VERSION
    ):
        if (
            isinstance(version, int)
            and not isinstance(version, bool)
            and version > BACKUP_SCHEMA_VERSION
        ):
            raise ValueError("UNSUPPORTED_BACKUP_VERSION")
        raise ValueError("INVALID_MANIFEST")
    database = meta.get("database")
    archive = meta.get("archive")
    files = meta.get("files")
    members = meta.get("members")
    bundles = meta.get("bundles")
    limits = meta.get("limits")
    if (
        not isinstance(database, dict)
        or database.get("name") != DATABASE_NAME
        or not isinstance(archive, dict)
        or not isinstance(files, list)
        or files != [archive]
        or not isinstance(members, list)
        or not isinstance(bundles, list)
        or not isinstance(limits, dict)
    ):
        raise ValueError("INVALID_MANIFEST")
    return meta


def _verified_archive(manifest: Path, meta: dict[str, Any]) -> Path:
    record = meta["archive"]
    name = record.get("name")
    if name != ARCHIVE_NAME:
        raise ValueError("INVALID_PATH")
    archive = manifest.parent / name
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("INVALID_PATH")
    try:
        size = archive.stat().st_size
    except OSError:
        raise ValueError("INVALID_PATH") from None
    if (
        record.get("size") != size
        or not _valid_digest(record.get("sha256"))
        or _digest_file(archive) != record["sha256"]
    ):
        raise ValueError("DIGEST_MISMATCH")
    return archive


def _safe_extract(archive: Path, destination: Path, meta: dict[str, Any]) -> None:
    expected = meta["members"]
    for item in expected:
        if not isinstance(item, dict):
            raise ValueError("INVALID_MANIFEST")
        _safe_member_name(item.get("name"))
        if not _valid_member_record(item):
            raise ValueError("INVALID_MANIFEST")
    expected_by_name = {item["name"]: item for item in expected}
    if len(expected_by_name) != len(expected):
        raise ValueError("INVALID_MANIFEST")
    limits = meta["limits"]
    effective_limits = {
        "max_files": min(_positive_int(limits.get("max_files")), MAX_FILES),
        "max_file_bytes": min(
            _positive_int(limits.get("max_file_bytes")), MAX_FILE_BYTES
        ),
        "max_total_bytes": min(
            _positive_int(limits.get("max_total_bytes")), MAX_TOTAL_BYTES
        ),
    }
    try:
        with zipfile.ZipFile(archive) as payload:
            infos = payload.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                raise ValueError("INVALID_ARCHIVE")
            if set(names) != set(expected_by_name):
                raise ValueError("INVALID_ARCHIVE")
            records = []
            for item in infos:
                _safe_member_name(item.filename)
                mode = item.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if (
                    item.is_dir()
                    or stat.S_ISLNK(mode)
                    or file_type not in (0, stat.S_IFREG)
                ):
                    raise ValueError("INVALID_ARCHIVE")
                records.append({"name": item.filename, "size": item.file_size})
            _enforce_limits(records, effective_limits)
            for item in infos:
                target = destination.joinpath(*PurePosixPath(item.filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with payload.open(item) as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > effective_limits["max_file_bytes"]:
                            raise ValueError("ARCHIVE_LIMIT_EXCEEDED")
                        digest.update(chunk)
                        output.write(chunk)
                wanted = expected_by_name[item.filename]
                if written != wanted["size"] or digest.hexdigest() != wanted["sha256"]:
                    raise ValueError("DIGEST_MISMATCH")
    except zipfile.BadZipFile as error:
        raise ValueError("INVALID_ARCHIVE") from error


def _validate_database(database: Path, record: dict[str, Any]) -> None:
    try:
        with sqlite3.connect(_read_only_uri(database), uri=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("CORRUPT_DATABASE")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("FOREIGN_KEY_VIOLATION")
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
    except sqlite3.DatabaseError as error:
        raise ValueError("CORRUPT_DATABASE") from error
    if not {"tasks", "blobs"}.issubset(tables):
        raise ValueError("INVALID_DATABASE_SCHEMA")
    if user_version > DATABASE_SCHEMA_VERSION:
        raise ValueError("UNSUPPORTED_DATABASE_VERSION")
    if (
        record.get("user_version") != user_version
        or record.get("schema_sha256") != _database_schema_digest(database)
    ):
        raise ValueError("INVALID_DATABASE_SCHEMA")


def _validate_blob_references(database: Path, root: Path) -> None:
    expected: set[str] = set()
    registered: set[tuple[str, str, str]] = set()
    try:
        with sqlite3.connect(_read_only_uri(database), uri=True) as connection:
            rows = connection.execute(
                "SELECT client, task, digest FROM blobs"
            ).fetchall()
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            references = _business_blob_references(connection, tables)
    except sqlite3.DatabaseError as error:
        raise ValueError("CORRUPT_DATABASE") from error
    for client, task, digest in rows:
        relative = f"{client}/{task}/blobs/{digest}"
        _safe_member_name(relative)
        path = root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file():
            raise ValueError("MISSING_BLOB")
        if _digest_file(path) != digest:
            raise ValueError("CORRUPT_BLOB")
        expected.add(relative)
        registered.add((client, task, digest))
    actual = {
        path.relative_to(root).as_posix()
        for path in root.glob("*/*/blobs/*") if path.is_file()
    }
    if actual != expected:
        raise ValueError("UNREGISTERED_BLOB")
    if any(reference not in registered for reference in references):
        raise ValueError("MISSING_BLOB_REFERENCE")


def _business_blob_references(
    connection: sqlite3.Connection, tables: set[str]
) -> set[tuple[str, str, str]]:
    references: set[tuple[str, str, str]] = set()
    if "input_batches" in tables:
        for client, task, digest, source_file_id in connection.execute(
            "SELECT client, task, digest, source_file_id FROM input_batches"
        ):
            references.add((client, task, digest))
            references.add((client, task, source_file_id))
    queries = {
        "workflow_revisions": (
            "SELECT client, task, metric_rules_evidence "
            "FROM workflow_revisions WHERE metric_rules_evidence IS NOT NULL"
        ),
        "workflow_events": (
            "SELECT client, task, evidence FROM workflow_events"
        ),
        "workflow_resolutions": (
            "SELECT client, task, evidence FROM workflow_resolutions"
        ),
    }
    for table, query in queries.items():
        if table in tables:
            references.update(connection.execute(query))
    if "workflow_confirmation_proofs" in tables:
        for client, task, evidence, source_evidence in connection.execute(
            """
            SELECT client, task, evidence, source_evidence
            FROM workflow_confirmation_proofs
            """
        ):
            references.add((client, task, evidence))
            references.add((client, task, source_evidence))
    return references


def _discover_export_bundles(root: Path) -> list[str]:
    bundles: set[str] = set()
    for marker in root.rglob(BUNDLE_MARKER_NAME):
        if marker.is_symlink() or marker.read_bytes() != BUNDLE_MARKER_BYTES:
            raise ValueError("INVALID_BUNDLE_MARKER")
        relative = marker.parent.relative_to(root).as_posix()
        _safe_member_name(relative)
        bundles.add(relative)
    # Legacy identity is determined from manifest structure; member validation
    # is deliberately separate so a damaged identifiable bundle fails closed.
    for manifest in root.rglob("manifest.json"):
        relative = manifest.parent.relative_to(root).as_posix()
        if relative in bundles:
            continue
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not _is_legacy_bundle_manifest(value):
            continue
        verify_bundle(manifest)
        _safe_member_name(relative)
        bundles.add(relative)
    return sorted(bundles)


def _is_legacy_bundle_manifest(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return False
    revision = value.get("revision")
    if (
        not isinstance(value.get("client"), str)
        or not value["client"]
        or not isinstance(value.get("task"), str)
        or not value["task"]
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not _valid_digest(value.get("payload_sha256"))
    ):
        return False
    files = value.get("files")
    if not isinstance(files, list) or len(files) != 2:
        return False
    records: dict[str, Any] = {}
    for item in files:
        if not isinstance(item, dict) or not _valid_digest(item.get("sha256")):
            return False
        name = item.get("name")
        if not isinstance(name, str) or name in records:
            return False
        records[name] = item
    return set(records) == {"snapshot.json", "report.xlsx"}


def _verify_export_bundles(root: Path, bundle_names: list[Any]) -> None:
    if not all(isinstance(name, str) for name in bundle_names):
        raise ValueError("INVALID_MANIFEST")
    if len(bundle_names) != len(set(bundle_names)):
        raise ValueError("INVALID_MANIFEST")
    registered: set[str] = set()
    for name in bundle_names:
        relative = _safe_member_name(name)
        bundle = root.joinpath(*relative.parts)
        if not bundle.is_dir():
            raise ValueError("INVALID_BUNDLE_LAYOUT")
        marker = bundle / BUNDLE_MARKER_NAME
        expected = {"manifest.json", "snapshot.json", "report.xlsx"}
        if marker.exists():
            if not marker.is_file() or marker.read_bytes() != BUNDLE_MARKER_BYTES:
                raise ValueError("INVALID_BUNDLE_MARKER")
            expected.add(BUNDLE_MARKER_NAME)
        entries = {entry.name for entry in bundle.iterdir() if entry.is_file()}
        if entries != expected or any(not entry.is_file() for entry in bundle.iterdir()):
            raise ValueError("INVALID_BUNDLE_LAYOUT")
        verify_bundle(bundle / "manifest.json")
        registered.add(name)
    marked = {
        marker.parent.relative_to(root).as_posix()
        for marker in root.rglob(BUNDLE_MARKER_NAME)
    }
    if not marked.issubset(registered):
        raise ValueError("UNREGISTERED_BUNDLE")


def _restore_task_directories(database: Path, root: Path) -> None:
    try:
        with sqlite3.connect(_read_only_uri(database), uri=True) as connection:
            tasks = connection.execute("SELECT client, task FROM tasks").fetchall()
    except sqlite3.DatabaseError as error:
        raise ValueError("CORRUPT_DATABASE") from error
    for client, task in tasks:
        relative = _safe_member_name(f"{client}/{task}/blobs")
        root.joinpath(*relative.parts).mkdir(parents=True, exist_ok=True)


def _database_schema_digest(database: Path) -> str:
    try:
        with sqlite3.connect(_read_only_uri(database), uri=True) as connection:
            rows = connection.execute(
                """
                SELECT type, name, tbl_name, sql FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
    except sqlite3.DatabaseError as error:
        raise ValueError("CORRUPT_DATABASE") from error
    return hashlib.sha256(_canonical_bytes(rows)).hexdigest()


def _database_user_version(database: Path) -> int:
    try:
        with sqlite3.connect(_read_only_uri(database), uri=True) as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise ValueError("CORRUPT_DATABASE") from error


def _read_only_uri(database: Path) -> str:
    return f"{database.resolve().as_uri()}?mode=ro"


def _zip_member_records(archive: Path) -> list[dict[str, Any]]:
    records = []
    with zipfile.ZipFile(archive) as payload:
        for item in sorted(payload.infolist(), key=lambda value: value.filename):
            with payload.open(item) as source:
                digest = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            records.append({
                "name": item.filename,
                "size": item.file_size,
                "sha256": digest.hexdigest(),
            })
    return records


def _enforce_limits(records: list[dict[str, Any]], limits: dict[str, int]) -> None:
    if len(records) > limits["max_files"]:
        raise ValueError("ARCHIVE_LIMIT_EXCEEDED")
    total = 0
    for record in records:
        size = record["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("INVALID_MANIFEST")
        if size > limits["max_file_bytes"]:
            raise ValueError("ARCHIVE_LIMIT_EXCEEDED")
        total += size
        if total > limits["max_total_bytes"]:
            raise ValueError("ARCHIVE_LIMIT_EXCEEDED")


def _safe_member_name(name: Any) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("INVALID_PATH")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("INVALID_PATH")
    return path


def _valid_member_record(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        _safe_member_name(value.get("name"))
    except ValueError:
        return False
    size = value.get("size")
    return (
        isinstance(size, int) and not isinstance(size, bool) and size >= 0
        and _valid_digest(value.get("sha256"))
    )


def _positive_int(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("INVALID_MANIFEST")
    return value


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "size": path.stat().st_size,
        "sha256": _digest_file(path),
    }


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
