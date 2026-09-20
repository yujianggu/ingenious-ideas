import uuid
from django.conf import settings
from django.db import models


class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('identity.Tenant', on_delete=models.PROTECT)
    owner = models.ForeignKey('proofing.Order', on_delete=models.PROTECT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    snapshot = models.JSONField()
    snapshot_sha256 = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default='queued')
    available_at = models.DateTimeField()
    lease_owner = models.CharField(max_length=200, blank=True)
    lease_token = models.UUIDField(null=True)
    lease_expires_at = models.DateTimeField(null=True)
    attempts = models.PositiveIntegerField(default=0)
    file = models.OneToOneField('files.FileRecord', null=True, on_delete=models.PROTECT)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['state', 'available_at'])]
        constraints = [models.CheckConstraint(condition=models.Q(state__in=['queued', 'running', 'succeeded', 'failed']), name='export_job_valid_state')]


class JobAttempt(models.Model):
    """Durable upload intent; retained after cleanup to catch late object writes."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name='object_attempts')
    lease_token = models.UUIDField()
    published = models.BooleanField(default=False)
    last_cleanup_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
