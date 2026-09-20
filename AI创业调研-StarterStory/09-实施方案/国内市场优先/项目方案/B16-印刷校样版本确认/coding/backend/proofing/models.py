import uuid
from django.db import models
from identity.models import Tenant, User

class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="orders")
    title = models.CharField(max_length=200)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=30, default='draft', choices=[(s, s) for s in ['draft', 'awaiting_confirmation', 'confirmed', 'returned', 'revoked']])
    current_version = models.ForeignKey('ProofVersion', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_orders")
    created_at = models.DateTimeField(auto_now_add=True)


class ProofVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='versions')
    sequence = models.PositiveIntegerField()
    asset = models.ForeignKey('files.FileRecord', on_delete=models.PROTECT)
    sha256 = models.CharField(max_length=64)
    display_name = models.CharField(max_length=200)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')
    published_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'sequence'], name='unique_order_version_sequence')]
        ordering = ['sequence']


class ConfirmationRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.OneToOneField(ProofVersion, on_delete=models.PROTECT, related_name='confirmation_request')
    invitation = models.OneToOneField('identity.Invitation', on_delete=models.PROTECT, related_name='confirmation_request')
    recipient = models.CharField(max_length=254)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ConfirmationEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(ConfirmationRequest, on_delete=models.PROTECT, related_name='events')
    decision = models.CharField(max_length=20, choices=[(s, s) for s in ['confirmed', 'returned', 'revoked', 'superseded']])
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    external_session = models.ForeignKey('identity.ExternalSession', null=True, on_delete=models.PROTECT)
    verification_method = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['request'], condition=models.Q(decision__in=['confirmed', 'returned']), name='unique_request_decision'),
            models.CheckConstraint(condition=(models.Q(decision__in=['confirmed', 'returned'], actor__isnull=True, external_session__isnull=False) | models.Q(decision__in=['revoked', 'superseded'], actor__isnull=False, external_session__isnull=True)), name='event_actual_actor_kind'),
            models.CheckConstraint(condition=~models.Q(decision='returned') | ~models.Q(reason=''), name='returned_requires_reason'),
        ]


class PrintReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.OneToOneField(ProofVersion, on_delete=models.PROTECT, related_name='print_review')
    confirmation = models.ForeignKey(ConfirmationEvent, on_delete=models.PROTECT)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    verification_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
