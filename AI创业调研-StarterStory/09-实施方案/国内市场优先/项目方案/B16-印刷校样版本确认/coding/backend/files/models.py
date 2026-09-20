import uuid
from django.conf import settings
from django.db import models


class FileRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey('identity.Tenant', on_delete=models.PROTECT)
    owner = models.ForeignKey('proofing.Order', on_delete=models.PROTECT, related_name='files')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    object_key = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=20, default='quarantined', choices=[(s, s) for s in ['quarantined', 'ready', 'rejected']])
    purpose = models.CharField(max_length=16, default='original')
    failure_reason = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
