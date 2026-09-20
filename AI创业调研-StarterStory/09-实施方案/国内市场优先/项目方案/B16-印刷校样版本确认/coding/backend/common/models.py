from django.conf import settings
from django.db import models

from identity.models import Tenant


class CommandReceipt(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="command_receipts")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="command_receipts")
    operation = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64, default="")
    key = models.CharField(max_length=200)
    request_hash = models.CharField(max_length=64)
    result_json = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "actor", "operation", "object_id", "key"],
                name="unique_command_receipt_scope_key",
            )
        ]
