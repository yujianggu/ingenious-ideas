import hashlib
import json
import multiprocessing
import sqlite3
import zipfile
from pathlib import Path

import pytest

from service_b10.backup import backup, restore
from service_b10.exports import export_bundle
from service_b10.inputs import import_file
from service_b10.store import Store


def _hold_lock(root, ready, release):
    store = Store(Path(root))
    with store.job_lock():
        ready.set()
        release.wait(10)


def _rewrite_archive(manifest, change):
    meta = json.loads(manifest.read_text(encoding="utf-8"))
    archive = manifest.parent / meta["archive"]["name"]
    members = {}
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            members[item.filename] = source.read(item)
    change(members)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in members.items():
            target.writestr(name, data)
    content = archive.read_bytes()
    meta["archive"].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    meta["files"][0].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    meta["members"] = [
        {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(members.items())
    ]
    manifest.write_text(json.dumps(meta), encoding="utf-8")


def test_backup_and_reject_corruption(tmp_path):
    s = Store(tmp_path / "live")
    s.create("a", "t1")
    digest = s.put("a", "t1", "x.txt", b"original")
    m = backup(s, tmp_path / "backup")
    restored = restore(m, tmp_path / "restored")
    assert restored.read("a", "t1", digest) == b"original"
    meta = json.loads(m.read_text())
    (m.parent / meta["files"][0]["name"]).write_bytes(b"broken")
    with pytest.raises(ValueError):
        restore(m, tmp_path / "reject")
    assert not (tmp_path / "reject").exists()
    assert s.read("a", "t1", digest) == b"original"


def test_wal_snapshot_includes_committed_data_and_not_live_database_copy(tmp_path, monkeypatch):
    store = Store(tmp_path / "live")
    store.create("a", "t1")
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE wal_marker(value TEXT)")
        connection.execute("INSERT INTO wal_marker VALUES ('committed')")

    def forbidden_copy(*args, **kwargs):
        raise AssertionError("the live database must use sqlite backup")

    monkeypatch.setattr("service_b10.backup.shutil.copyfile", forbidden_copy)
    manifest = backup(store, tmp_path / "backup")
    restored = restore(manifest, tmp_path / "restored")
    with sqlite3.connect(restored.db_path) as connection:
        assert connection.execute("SELECT value FROM wal_marker").fetchone() == ("committed",)


def test_backup_rejects_lock_held_by_another_process(tmp_path):
    store = Store(tmp_path / "live")
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_lock, args=(str(store.root), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(ValueError, match="^JOB_BUSY$"):
            backup(store, tmp_path / "backup")
        assert not (tmp_path / "backup").exists()
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
        assert process.exitcode == 0


def test_restore_rejects_missing_referenced_blob_and_keeps_target_absent(tmp_path):
    store = Store(tmp_path / "live")
    store.create("a", "t1")
    digest = store.put("a", "t1", "raw.csv", b"raw")
    manifest = backup(store, tmp_path / "backup")
    _rewrite_archive(manifest, lambda members: members.pop(f"a/t1/blobs/{digest}"))
    with pytest.raises(ValueError, match="^MISSING_BLOB$"):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert store.read("a", "t1", digest) == b"raw"


@pytest.mark.parametrize("bad_name", ["../escape", "/absolute", "a/../../escape"])
def test_restore_rejects_archive_traversal(tmp_path, bad_name):
    store = Store(tmp_path / "live")
    manifest = backup(store, tmp_path / "backup")
    meta = json.loads(manifest.read_text())
    archive = manifest.parent / meta["archive"]["name"]
    with zipfile.ZipFile(archive, "a") as payload:
        payload.writestr(bad_name, b"escape")
    content = archive.read_bytes()
    meta["archive"].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    meta["files"][0].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    meta["members"].append({"name": bad_name, "size": 6, "sha256": hashlib.sha256(b"escape").hexdigest()})
    manifest.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="^INVALID_PATH$"):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("attack", ["duplicate", "symlink", "oversize"])
def test_restore_rejects_unsafe_archive_members(tmp_path, attack):
    store = Store(tmp_path / "live")
    manifest = backup(store, tmp_path / "backup")
    meta = json.loads(manifest.read_text())
    archive = manifest.parent / meta["archive"]["name"]
    if attack == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            with zipfile.ZipFile(archive, "a") as payload:
                payload.writestr("store.sqlite3", b"duplicate")
    elif attack == "symlink":
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        with zipfile.ZipFile(archive, "a") as payload:
            payload.writestr(link, b"outside")
    else:
        meta["limits"]["max_file_bytes"] = 1
    content = archive.read_bytes()
    meta["archive"].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    meta["files"][0].update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
    manifest.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="^(INVALID_ARCHIVE|ARCHIVE_LIMIT_EXCEEDED)$"):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_rejects_future_manifest_and_existing_target(tmp_path):
    store = Store(tmp_path / "live")
    manifest = backup(store, tmp_path / "backup")
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="^TARGET_EXISTS$"):
        restore(manifest, target)
    assert marker.read_text() == "keep"

    meta = json.loads(manifest.read_text())
    meta["schema_version"] += 1
    manifest.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="^UNSUPPORTED_BACKUP_VERSION$"):
        restore(manifest, tmp_path / "future")
    assert not (tmp_path / "future").exists()


def test_restore_validates_database_integrity_schema_and_foreign_keys(tmp_path):
    store = Store(tmp_path / "live")
    store.create("a", "t1")
    manifest = backup(store, tmp_path / "backup")

    def corrupt(members):
        members["store.sqlite3"] = b"not sqlite"

    _rewrite_archive(manifest, corrupt)
    with pytest.raises(ValueError, match="^CORRUPT_DATABASE$"):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("DROP TABLE blobs", "INVALID_DATABASE_SCHEMA"),
        (
            "PRAGMA foreign_keys=OFF; INSERT INTO blobs VALUES "
            "('missing','task','" + "0" * 64 + "','orphan')",
            "FOREIGN_KEY_VIOLATION",
        ),
    ],
)
def test_restore_rejects_schema_change_and_foreign_key_violation(
    tmp_path, mutation, error
):
    store = Store(tmp_path / "live")
    store.create("a", "t1")
    manifest = backup(store, tmp_path / "backup")

    def mutate_database(members):
        database = tmp_path / "mutated.sqlite3"
        database.write_bytes(members["store.sqlite3"])
        with sqlite3.connect(database) as connection:
            connection.executescript(mutation)
        members["store.sqlite3"] = database.read_bytes()

    _rewrite_archive(manifest, mutate_database)
    with pytest.raises(ValueError, match=f"^{error}$"):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_included_export_bundle_is_verified(tmp_path):
    store = Store(tmp_path / "live")
    payload = json.loads(Path("tests/fixtures/approved_payload.json").read_text())
    export_bundle(store.root / "exports" / "v1", payload)
    manifest = backup(store, tmp_path / "backup")

    def corrupt(members):
        members["exports/v1/report.xlsx"] = b"tampered"

    _rewrite_archive(manifest, corrupt)
    with pytest.raises(ValueError, match="^DIGEST_MISMATCH$"):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_publish_failure_rolls_back_without_touching_source_or_target(tmp_path, monkeypatch):
    import service_b10.backup as backup_module

    store = Store(tmp_path / "live")
    store.create("a", "t1")
    digest = store.put("a", "t1", "raw.csv", b"original")
    manifest = backup(store, tmp_path / "backup")

    def fail_publish(source, destination):
        raise OSError("injected publish failure")

    monkeypatch.setattr(backup_module, "_publish_no_replace", fail_publish)
    with pytest.raises(OSError, match="injected publish failure"):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert store.read("a", "t1", digest) == b"original"
    assert not any(path.name.startswith(".restored-") for path in tmp_path.iterdir())


def test_backup_snapshot_failure_leaves_no_backup_and_keeps_live_store(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "live")
    store.create("a", "t1")
    digest = store.put("a", "t1", "raw.csv", b"original")

    def fail_snapshot(source, target):
        target.write_bytes(b"partial")
        raise OSError("injected snapshot failure")

    monkeypatch.setattr("service_b10.backup.snapshot_database", fail_snapshot)
    with pytest.raises(OSError, match="injected snapshot failure"):
        backup(store, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()
    assert store.read("a", "t1", digest) == b"original"
    assert not any(path.name.startswith(".backup-") for path in tmp_path.iterdir())


def test_empty_task_round_trip_can_accept_its_first_blob(tmp_path):
    store = Store(tmp_path / "live")
    store.create("a", "empty")
    store.create("a", "nonempty")
    existing = store.put("a", "nonempty", "old.txt", b"old")

    restored = restore(
        backup(store, tmp_path / "backup"), tmp_path / "restored"
    )
    first = restored.put("a", "empty", "first.txt", b"first")
    assert restored.read("a", "empty", first) == b"first"
    assert restored.read("a", "nonempty", existing) == b"old"


@pytest.mark.parametrize("reference_kind", ["input_batch", "workflow_event"])
def test_restore_rejects_business_reference_whose_blob_registration_is_missing(
    tmp_path, reference_kind
):
    store = Store(tmp_path / "live")
    store.create("a", "t1")
    if reference_kind == "input_batch":
        batch = import_file(
            store, "a", "t1", "orders", "2026-09", "orders.csv",
            b"shop,order,paid\ns1,o1,1.00\n",
            {
                "columns": {"shop": "shop", "order": "order", "paid": "paid"},
                "required": ["shop", "order", "paid"],
                "currency": "CNY", "places": 2, "money": ["paid"],
                "encoding": "utf-8", "thousands": ",",
            },
        )
        digest = batch.digest
    else:
        import service_b10.workflow as workflow

        digest = store.put("a", "t1", "evidence.txt", b"evidence")
        with store.job_lock():
            with store._connect() as connection:
                workflow._schema(connection)
                connection.execute(
                    "INSERT INTO workflow_revisions VALUES "
                    "('a','t1',1,'draft','{}','{}',NULL,NULL,NULL)"
                )
                connection.execute(
                    """
                    INSERT INTO workflow_events
                        (client,task,revision,action,from_state,to_state,
                         actor_json,evidence,reason)
                    VALUES ('a','t1',1,'submit','draft','submitted','{}',?,'')
                    """,
                    (digest,),
                )

    with store.job_lock():
        with store._connect() as connection:
            connection.execute(
                "DELETE FROM blobs WHERE client='a' AND task='t1' AND digest=?",
                (digest,),
            )
        (store.root / "a" / "t1" / "blobs" / digest).unlink()

    manifest = backup(store, tmp_path / "backup")
    with pytest.raises(ValueError, match="^MISSING_BLOB_REFERENCE$"):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("location", ["exports/v1", "a/t1/exports/v1", "custom/deep/v1"])
@pytest.mark.parametrize("damage", ["empty_manifest", "missing_manifest", "missing_member"])
def test_restore_fails_closed_for_damaged_export_bundle(tmp_path, location, damage):
    store = Store(tmp_path / "live")
    payload = json.loads(Path("tests/fixtures/approved_payload.json").read_text())
    bundle = store.root / location
    export_bundle(bundle, payload)
    if damage == "empty_manifest":
        (bundle / "manifest.json").write_text("{}", encoding="utf-8")
    elif damage == "missing_manifest":
        (bundle / "manifest.json").unlink()
    else:
        (bundle / "report.xlsx").unlink()

    manifest = backup(store, tmp_path / "backup")
    with pytest.raises(ValueError):
        restore(manifest, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_client_named_exports_is_not_mistaken_for_a_bundle(tmp_path):
    store = Store(tmp_path / "live")
    store.create("exports", "t1")
    digest = store.put("exports", "t1", "raw.txt", b"raw")
    restored = restore(
        backup(store, tmp_path / "backup"), tmp_path / "restored"
    )
    assert restored.read("exports", "t1", digest) == b"raw"


def test_intact_legacy_bundle_without_marker_is_still_verified(tmp_path):
    store = Store(tmp_path / "live")
    payload = json.loads(Path("tests/fixtures/approved_payload.json").read_text())
    bundle = store.root / "legacy" / "v1"
    export_bundle(bundle, payload)
    (bundle / ".service-b10-export.json").unlink()
    restored = restore(
        backup(store, tmp_path / "backup"), tmp_path / "restored"
    )
    assert (restored.root / "legacy" / "v1" / "report.xlsx").is_file()


@pytest.mark.parametrize("damage", ["missing_xlsx", "corrupt_xlsx", "missing_snapshot"])
def test_identifiable_legacy_bundle_damage_is_not_downgraded_to_plain_json(
    tmp_path, damage
):
    store = Store(tmp_path / "live")
    payload = json.loads(Path("tests/fixtures/approved_payload.json").read_text())
    bundle = store.root / "legacy" / "v1"
    export_bundle(bundle, payload)
    (bundle / ".service-b10-export.json").unlink()
    if damage == "missing_xlsx":
        (bundle / "report.xlsx").unlink()
    elif damage == "missing_snapshot":
        (bundle / "snapshot.json").unlink()
    else:
        (bundle / "report.xlsx").write_bytes(b"broken")
    with pytest.raises(ValueError):
        restore(backup(store, tmp_path / "backup"), tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("suffix", ["#1", "?1", "%1", "中文 空格"])
def test_sqlite_paths_with_uri_characters_round_trip_without_external_residue(
    tmp_path, suffix
):
    store = Store(tmp_path / f"live{suffix}")
    store.create("a", "t1")
    digest = store.put("a", "t1", "raw.txt", b"raw")
    backup_dir = tmp_path / f"backup{suffix}"
    restored_dir = tmp_path / f"restored{suffix}"
    manifest = backup(store, backup_dir)
    restored = restore(manifest, restored_dir)
    assert restored.read("a", "t1", digest) == b"raw"
    assert set(tmp_path.iterdir()) == {store.root, backup_dir, restored_dir}
