from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import pytest
from django.db import close_old_connections, connection
from django.utils import timezone
from test_stale_confirmation import create
from test_exports import enqueue


@pytest.mark.django_db(transaction=True)
def test_expired_lease_fences_old_worker_and_rerun_is_idempotent(api, settings, tmp_path):
    from jobs.models import Job
    from jobs.worker import claim_job, run_job
    from files.models import FileRecord
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    jid = enqueue(api, create(api)).json()['job_id']
    old = claim_job('crashed', 60)
    assert claim_job('other', 60) is None
    Job.objects.filter(pk=jid).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    new = claim_job('recovered', 120)
    assert new.id == old.id and new.lease_token != old.lease_token
    run_job(old.id, lease_token=old.lease_token)
    assert not FileRecord.objects.exists()
    run_job(new.id, lease_token=new.lease_token)
    run_job(new.id, lease_token=new.lease_token)
    run_job(old.id, lease_token=old.lease_token)
    assert FileRecord.objects.count() == 1
    assert FileRecord.objects.get().id == new.id
    assert Job.objects.get().state == 'succeeded'
    assert len(list(settings.PRIVATE_LOCAL_ROOT.iterdir())) == 1


@pytest.mark.django_db(transaction=True)
def test_two_real_pg_workers_only_one_claims(api):
    from jobs.worker import claim_job
    enqueue(api, create(api))
    barrier = Barrier(2)
    def worker(n):
        close_old_connections()
        try:
            with connection.cursor() as cur:
                cur.execute('select pg_backend_pid()')
                pid = cur.fetchone()[0]
            barrier.wait(timeout=10)
            job = claim_job(str(n), 60)
            return pid, job.id if job else None
        finally:
            close_old_connections()
    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(worker, range(2)))
    assert result[0][0] != result[1][0]
    assert sum(jid is not None for _, jid in result) == 1


@pytest.mark.django_db(transaction=True)
def test_worker_finishing_after_lease_replaced_cannot_publish(api, settings, tmp_path, monkeypatch):
    from threading import Event
    from jobs import worker
    from jobs.models import Job
    from files.models import FileRecord
    settings.PRIVATE_LOCAL_ROOT=tmp_path/'objects'
    enqueue(api,create(api))
    old=worker.claim_job('old',120)
    started,released=Event(),Event()
    build=worker.build_archive
    def blocked(*args):
        if not started.is_set():
            started.set()
            assert released.wait(20)
        return build(*args)
    monkeypatch.setattr(worker,'build_archive',blocked)
    def old_run():
        close_old_connections()
        try: worker.run_job(old.id,lease_token=old.lease_token)
        finally: close_old_connections()
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(old_run)
        assert started.wait(10)
        Job.objects.filter(pk=old.id).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        new=worker.claim_job('new',120)
        worker.run_job(new.id,lease_token=new.lease_token)
        released.set();future.result(timeout=20)
    assert Job.objects.get().state=='succeeded'
    assert FileRecord.objects.count()==1


@pytest.mark.django_db(transaction=True)
def test_crash_after_object_write_recovers_stable_result_id(api,settings,tmp_path,monkeypatch):
    from jobs import worker
    from jobs.models import Job
    from files.models import FileRecord
    settings.PRIVATE_LOCAL_ROOT=tmp_path/'objects'
    enqueue(api,create(api))
    old=worker.claim_job('old',120)
    create_record=FileRecord.objects.create
    def crash(**kwargs):
        raise SystemExit('crash between object and DB commit')
    monkeypatch.setattr(FileRecord.objects,'create',crash)
    with pytest.raises(SystemExit):
        worker.run_job(old.id,lease_token=old.lease_token)
    assert not FileRecord.objects.exists()
    assert len(list(settings.PRIVATE_LOCAL_ROOT.iterdir()))==1
    Job.objects.filter(pk=old.id).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
    monkeypatch.setattr(FileRecord.objects,'create',create_record)
    new=worker.claim_job('new',120)
    worker.run_job(new.id,lease_token=new.lease_token)
    assert Job.objects.get().state=='succeeded'
    assert FileRecord.objects.get().id==old.id
    assert FileRecord.objects.get().object_key!=old.id.hex
    assert len(list(settings.PRIVATE_LOCAL_ROOT.iterdir()))==1


@pytest.mark.django_db(transaction=True)
def test_upload_expiry_reclaimed_winner_survives_old_completion(api, settings, tmp_path, monkeypatch):
    from threading import Event, current_thread
    from jobs import worker
    from jobs.models import Job
    from files.models import FileRecord
    from files.storage import get_store
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    enqueue(api, create(api))
    old = worker.claim_job('old', 1)
    started, release = Event(), Event()
    store = get_store()
    real_put, real_delete = store.put, store.delete
    atomic_flags = []
    def put(key, stream):
        atomic_flags.append(connection.in_atomic_block)
        if current_thread().name.startswith('old-upload'):
            started.set()
            assert release.wait(15)
        real_put(key, stream)
    def delete(key):
        atomic_flags.append(connection.in_atomic_block)
        real_delete(key)
    def storage():
        atomic_flags.append(connection.in_atomic_block)
        return store
    store.put, store.delete = put, delete
    monkeypatch.setattr(worker, 'get_store', storage)
    monkeypatch.setattr(worker, 'build_archive', lambda *a: b'old bytes' if current_thread().name.startswith('old-upload') else b'winner bytes')
    def run_old():
        close_old_connections()
        try:
            worker.run_job(old.id, lease_token=old.lease_token)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(1, thread_name_prefix='old-upload') as pool:
        pending = pool.submit(run_old)
        try:
            assert started.wait(5)
            monkeypatch.setattr(worker.timezone, 'now', lambda: old.lease_expires_at + timedelta(seconds=1))
            new = worker.claim_job('new', 120)
            assert new is not None, atomic_flags
            worker.run_job(new.id, lease_token=new.lease_token)
            winner = FileRecord.objects.get(pk=old.id)
            with store.open(winner.object_key) as source:
                assert source.read() == b'winner bytes'
        finally:
            release.set()
            pending.result(timeout=10)
    assert not any(atomic_flags)
    assert Job.objects.get(pk=old.id).state == 'succeeded'
    assert FileRecord.objects.count() == 1
    with store.open(winner.object_key) as source:
        assert source.read() == b'winner bytes'
    assert [p.name for p in settings.PRIVATE_LOCAL_ROOT.iterdir()] == [winner.object_key]


@pytest.mark.django_db(transaction=True)
def test_upload_expiry_without_reclaimer_cannot_publish(api, settings, tmp_path, monkeypatch):
    from jobs import worker
    from jobs.models import Job
    from files.models import FileRecord
    from files.storage import get_store
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    enqueue(api, create(api))
    job = worker.claim_job('old', 1)
    store = get_store()
    real_put = store.put
    def put(key, stream):
        real_put(key, stream)
        monkeypatch.setattr(worker.timezone, 'now', lambda: job.lease_expires_at + timedelta(seconds=1))
    monkeypatch.setattr(store, 'put', put)
    monkeypatch.setattr(worker, 'get_store', lambda: store)
    monkeypatch.setattr(worker, 'build_archive', lambda *a: b'archive')
    worker.run_job(job.id, lease_token=job.lease_token)
    assert Job.objects.get(pk=job.id).state == 'running'
    assert not FileRecord.objects.exists()
    assert not list(settings.PRIVATE_LOCAL_ROOT.iterdir())


@pytest.mark.django_db(transaction=True)
def test_durable_cleanup_retries_late_orphan_and_preserves_winner(api, settings, tmp_path, monkeypatch):
    import io
    from jobs import worker
    from jobs.models import Job, JobAttempt
    from files.models import FileRecord
    from files.storage import get_store
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    enqueue(api, create(api))
    old = worker.claim_job('old', 1)
    attempt = JobAttempt.objects.create(job=old, lease_token=old.lease_token)
    store = get_store()
    store.put(attempt.id.hex, io.BytesIO(b'orphan'))
    expired = old.lease_expires_at + timedelta(seconds=1)
    monkeypatch.setattr(worker.timezone, 'now', lambda: expired)
    worker.cleanup_attempts()
    assert not list(settings.PRIVATE_LOCAL_ROOT.iterdir())
    assert JobAttempt.objects.filter(pk=attempt.id).exists()
    # Remote write completion may arrive after the collector's first delete.
    store.put(attempt.id.hex, io.BytesIO(b'late orphan'))
    new = worker.claim_job('new', 120)
    monkeypatch.setattr(worker, 'build_archive', lambda *a: b'winner')
    worker.run_job(new.id, lease_token=new.lease_token)
    result = FileRecord.objects.get(pk=old.id)
    assert len(list(settings.PRIVATE_LOCAL_ROOT.iterdir())) == 2
    monkeypatch.setattr(worker.timezone, 'now', lambda: expired + timedelta(seconds=61))
    worker.cleanup_attempts()
    assert [p.name for p in settings.PRIVATE_LOCAL_ROOT.iterdir()] == [result.object_key]
    with store.open(result.object_key) as source:
        assert source.read() == b'winner'
    assert Job.objects.get(pk=old.id).state == 'succeeded'


@pytest.mark.django_db(transaction=True)
def test_same_lease_duplicate_execution_uses_distinct_objects(api, settings, tmp_path, monkeypatch):
    from jobs import worker
    from files.models import FileRecord
    from files.storage import get_store
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    enqueue(api, create(api))
    job = worker.claim_job('same-lease', 120)
    store = get_store()
    real_put = store.put
    barrier = Barrier(2)
    def put(key, stream):
        assert not connection.in_atomic_block
        real_put(key, stream)
        barrier.wait(timeout=10)
    monkeypatch.setattr(store, 'put', put)
    monkeypatch.setattr(worker, 'get_store', lambda: store)
    monkeypatch.setattr(worker, 'build_archive', lambda *a: b'same snapshot')
    def execute(_):
        close_old_connections()
        try:
            worker.run_job(job.id, lease_token=job.lease_token)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(execute, range(2)))
    assert FileRecord.objects.count() == 1
    assert FileRecord.objects.get().id == job.id
    assert len(list(settings.PRIVATE_LOCAL_ROOT.iterdir())) == 1


@pytest.mark.django_db(transaction=True)
def test_cleanup_batch_does_not_starve_orphan_behind_live_attempt(api, settings, tmp_path):
    import io
    import uuid
    from jobs import worker
    from jobs.models import JobAttempt
    from files.storage import get_store
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    enqueue(api, create(api))
    job = worker.claim_job('live', 120)
    live = JobAttempt.objects.create(job=job, lease_token=job.lease_token)
    orphan = JobAttempt.objects.create(job=job, lease_token=uuid.uuid4())
    store = get_store()
    store.put(live.id.hex, io.BytesIO(b'active upload'))
    store.put(orphan.id.hex, io.BytesIO(b'orphan'))
    worker.cleanup_attempts(limit=1)
    assert [p.name for p in settings.PRIVATE_LOCAL_ROOT.iterdir()] == [live.id.hex]


@pytest.mark.django_db(transaction=True)
def test_never_cleaned_orphan_precedes_retained_cleanup_rows(api, settings, tmp_path, monkeypatch):
    import io
    import uuid
    from jobs import worker
    from jobs.models import Job, JobAttempt
    from files.storage import get_store
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    job_id = enqueue(api, create(api)).json()['job_id']
    job = Job.objects.get(pk=job_id)
    job.state = 'failed'
    job.save(update_fields=['state'])
    now = timezone.now()
    for age in (120, 90):
        JobAttempt.objects.create(job=job, lease_token=uuid.uuid4(),
                                  last_cleanup_at=now - timedelta(seconds=age))
    fresh = JobAttempt.objects.create(job=job, lease_token=uuid.uuid4())
    get_store().put(fresh.id.hex, io.BytesIO(b'crash orphan'))
    # Two retained batches must not alternate forever ahead of NULL timestamps.
    for elapsed in (0, 30, 60, 90):
        monkeypatch.setattr(worker.timezone, 'now', lambda: now + timedelta(seconds=elapsed))
        worker.cleanup_attempts(limit=1)
        if elapsed == 0:
            fresh.refresh_from_db()
            assert fresh.last_cleanup_at == now
            assert not (settings.PRIVATE_LOCAL_ROOT / fresh.id.hex).exists()
    fresh.refresh_from_db()
    assert fresh.last_cleanup_at is not None
    assert not (settings.PRIVATE_LOCAL_ROOT / fresh.id.hex).exists()
