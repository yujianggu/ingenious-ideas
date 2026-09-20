import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    pass

class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)

class Membership(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    roles = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "tenant"], name="unique_tenant_member")]


class Invitation(models.Model):
    """Recipient grant is scoped to exactly one immutable artifact, never tenant membership."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    owner = models.ForeignKey('proofing.Order', on_delete=models.PROTECT, related_name='invitations')
    file = models.ForeignKey('files.FileRecord', on_delete=models.PROTECT, related_name='invitations')
    issued_by = models.ForeignKey(User, on_delete=models.PROTECT)
    receiver = models.CharField(max_length=254)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    current_delivery = models.ForeignKey('CodeDelivery', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    code_hash = models.CharField(max_length=64, blank=True)
    code_expires_at = models.DateTimeField(null=True, blank=True)
    code_browser_hash = models.CharField(max_length=64, blank=True)
    code_sent_at = models.DateTimeField(null=True, blank=True)
    code_sends = models.PositiveSmallIntegerField(default=0)
    code_attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class ExternalSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invitation = models.ForeignKey(Invitation, on_delete=models.PROTECT, related_name='external_sessions')
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    verification_method = models.CharField(max_length=30, default='receiver_code')
    created_at = models.DateTimeField(auto_now_add=True)


class CodeDelivery(models.Model):
    """Durable delivery identity: code is secret-derived, never stored as plaintext."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invitation = models.ForeignKey(Invitation, on_delete=models.PROTECT, related_name='code_deliveries')
    browser_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
