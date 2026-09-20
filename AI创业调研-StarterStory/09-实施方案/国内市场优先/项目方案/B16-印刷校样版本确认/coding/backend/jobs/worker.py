"""Database leases; all heavy work outside transactions, fenced publication."""
from datetime import timedelta
import hashlib
import io
import uuid
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from common.idempotency import request_digest
from exports.service import build_archive, ExportFailure
from files.models import FileRecord
from files.storage import get_store
from .models import Job, JobAttempt


def claim_job(worker_id: str, lease_seconds: int = 120):
    if not worker_id or len(worker_id) > 200 or not 1 <= lease_seconds <= 3600:
        raise ValueError('Invalid worker/lease')
    with transaction.atomic():
        now = timezone.now()
        job = Job.objects.select_for_update(skip_locked=True).filter(
            Q(state='queued', available_at__lte=now) | Q(state='running', lease_expires_at__lte=now)
        ).order_by('created_at').first()
        if job is None:
            return None
        job.state = 'running'
        job.lease_owner = worker_id
        job.lease_token = uuid.uuid4()
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.attempts += 1
        job.save()
        return job


def _owns(job, token):
    return job.state == 'running' and job.lease_token == token and job.lease_expires_at > timezone.now()


def cleanup_attempt(attempt_id):
    """Authorize deletion in a short transaction; perform all storage I/O after it.

    An invalid lease can never publish this attempt again. Every execution has a
    distinct key, so the deletion cannot touch a replacement worker's object.
    Keep the intent row: a remote put may finish after an earlier cleanup pass.
    """
    attempt = JobAttempt.objects.filter(pk=attempt_id).first()
    if attempt is None:
        return
    with transaction.atomic():
        job = Job.objects.select_for_update(skip_locked=True).filter(pk=attempt.job_id).first()
        if job is None:
            return
        attempt.refresh_from_db()
        if attempt.published or _owns(job, attempt.lease_token):
            return
        # Defensive reference check also protects results written by older code.
        if FileRecord.objects.filter(object_key=attempt.id.hex).exists():
            return
        attempt.last_cleanup_at = timezone.now()
        attempt.save(update_fields=['last_cleanup_at'])
    try:
        get_store().delete(attempt.id.hex)
    except Exception:
        # Storage outages cannot undo a committed winner. The durable intent is
        # retried by the periodic collector after the cooldown.
        return


def cleanup_attempts(job_id=None, limit=100):
    cutoff = timezone.now() - timedelta(seconds=60)
    pending = JobAttempt.objects.filter(published=False).filter(
        Q(last_cleanup_at__isnull=True) | Q(last_cleanup_at__lte=cutoff)).exclude(
        job__state='running', job__lease_token=F('lease_token'),
        job__lease_expires_at__gt=timezone.now())
    if job_id is not None:
        pending = pending.filter(job_id=job_id)
    for attempt_id in pending.order_by(F('last_cleanup_at').asc(nulls_first=True), 'created_at').values_list('id', flat=True)[:limit]:
        cleanup_attempt(attempt_id)


def run_job(job_id, *, lease_token=None):
    # A bare ID may start an unclaimed job, never steal a running worker's token.
    if lease_token is None:
        with transaction.atomic():
            job = Job.objects.select_for_update().get(pk=job_id)
            if job.state != 'queued':
                return
            job.state = 'running'
            job.lease_owner = 'direct'
            job.lease_token = uuid.uuid4()
            job.lease_expires_at = timezone.now() + timedelta(seconds=120)
            job.attempts += 1
            job.save()
            lease_token = job.lease_token
    job = Job.objects.get(pk=job_id)
    if not _owns(job, lease_token):
        return
    attempt = None
    try:
        if request_digest(job.snapshot) != job.snapshot_sha256:
            raise ExportFailure('snapshot_corrupt')
        archive = build_archive(job.snapshot, job.tenant_id, job.owner_id)
        digest = hashlib.sha256(archive).hexdigest()
        with transaction.atomic():
            current = Job.objects.select_for_update().get(pk=job_id)
            if not _owns(current, lease_token):
                return
            # Even two invocations using the same lease get independent keys.
            # Persist intent before the external write so a crash is collectible.
            attempt = JobAttempt.objects.create(job=current, lease_token=lease_token)
        get_store().put(attempt.id.hex, io.BytesIO(archive))
        with transaction.atomic():
            current = Job.objects.select_for_update().get(pk=job_id)
            if _owns(current, lease_token):
                record = FileRecord.objects.create(id=current.id, tenant_id=current.tenant_id,
                    owner_id=current.owner_id, uploaded_by_id=current.requested_by_id,
                    object_key=attempt.id.hex, display_name=f'校样记录-{current.id}.zip', sha256=digest,
                    size=len(archive), content_type='application/zip', state='ready', purpose='export')
                attempt.published = True
                attempt.save(update_fields=['published'])
                current.file = record
                current.state = 'succeeded'
                current.error_code = ''
                current.save()
    except Exception as exc:
        code = exc.code if isinstance(exc, ExportFailure) else 'export_failed'
        with transaction.atomic():
            current = Job.objects.select_for_update().get(pk=job_id)
            if _owns(current, lease_token):
                current.state = 'failed'
                current.error_code = code
                current.save(update_fields=['state', 'error_code'])
    # No finally: process termination is deliberately handled by durable cleanup.
    # An ordinary failure or a stale worker only cleans its own unpublished key.
    if attempt is not None:
        cleanup_attempt(attempt.id)
    cleanup_attempts(job_id=job_id)
