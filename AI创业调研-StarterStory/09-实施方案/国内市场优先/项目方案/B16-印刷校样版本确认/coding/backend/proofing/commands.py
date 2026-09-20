from django.db import transaction
from rest_framework.exceptions import NotFound

from common.idempotency import execute_once
from config.exceptions import Conflict
from .models import Order


def serialize_order(order):
    return {"id": str(order.id), "title": order.title, "revision": order.revision,
            "status": order.status, "current_version_id": str(order.current_version_id) if order.current_version_id else None,
            "versions": [serialize_version(v) for v in order.versions.select_related('confirmation_request', 'print_review').prefetch_related('confirmation_request__events')]}


def create_order(tenant, actor, key, payload):
    def command():
        order = Order.objects.create(tenant=tenant, title=payload["title"], created_by=actor)
        return serialize_order(order)

    return execute_once((tenant.id, actor.id, "order.create", ""), key, payload, command)


def update_order(tenant, actor, order_id, key, payload):
    def command():
        try:
            order = Order.objects.select_for_update().get(id=order_id, tenant=tenant)
        except Order.DoesNotExist:
            raise NotFound()
        if order.revision != payload["expected_revision"]:
            raise Conflict(current_revision=order.revision)
        order.title = payload["title"]
        order.revision += 1
        order.save(update_fields=["title", "revision"])
        return serialize_order(order)

    return execute_once((tenant.id, actor.id, "order.update", order_id), key, payload, command)


def serialize_event(event):
    return {'id': str(event.id), 'decision': event.decision, 'reason': event.reason,
            'actor_id': event.actor_id, 'external_session_id': str(event.external_session_id) if event.external_session_id else None,
            'verification_method': event.verification_method, 'created_at': event.created_at.isoformat()}


def serialize_version(version):
    confirmation = version.confirmation_request
    review = getattr(version, 'print_review', None)
    return {'id': str(version.id), 'sequence': version.sequence, 'asset_id': str(version.asset_id),
            'sha256': version.sha256, 'display_name': version.display_name,
            'uploaded_by': version.uploaded_by_id, 'published_by': version.published_by_id,
            'created_at': version.created_at.isoformat(),
            'request': {'id': str(confirmation.id), 'recipient': confirmation.recipient,
                        'expires_at': confirmation.expires_at.isoformat(),
                        'revoked_at': confirmation.revoked_at.isoformat() if confirmation.revoked_at else None,
                        'superseded_at': confirmation.superseded_at.isoformat() if confirmation.superseded_at else None,
                        'events': [serialize_event(e) for e in confirmation.events.all()]},
            'print_review': {'id': str(review.id), 'confirmation_id': str(review.confirmation_id), 'actor_id': review.actor_id, 'verification_note': review.verification_note, 'created_at': review.created_at.isoformat()} if review else None}


def require_recipient(actor, request):
    """An ExternalSession capability must bind this exact request, recipient and bytes."""
    from rest_framework.exceptions import PermissionDenied
    from identity.models import ExternalSession
    if not isinstance(actor, ExternalSession):
        raise PermissionDenied('必须由指定客户核验后操作')
    invite = actor.invitation
    if (invite.id != request.invitation_id or invite.receiver != request.recipient
            or invite.owner_id != request.version.order_id
            or invite.tenant_id != request.version.order.tenant_id
            or invite.file_id != request.version.asset_id):
        raise PermissionDenied('当前客户会话不能操作此请求')


def check_current(order, confirmation):
    from django.utils import timezone
    if (confirmation.version_id != order.current_version_id or confirmation.revoked_at
            or confirmation.superseded_at or confirmation.expires_at <= timezone.now()):
        raise Conflict('校样已更新或请求已撤销/过期', current_revision=order.revision)


def _revision(order, payload):
    if order.revision != payload['expected_revision']:
        raise Conflict(current_revision=order.revision)


def _revoke_grants(order, now):
    from identity.invitations import invalidate_invitation
    for invitation in order.invitations.filter(revoked_at__isnull=True):
        invalidate_invitation(invitation, now)


def publish_version(order, actor, payload):
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from rest_framework.exceptions import PermissionDenied
    from files.models import FileRecord
    from identity.models import Invitation
    from identity.invitations import token_for, hash_token
    from .models import ProofVersion, ConfirmationRequest, ConfirmationEvent
    asset = FileRecord.objects.filter(pk=payload['asset_id']).first()
    if asset is None:
        raise NotFound()
    if asset.owner_id != order.id or asset.tenant_id != order.tenant_id:
        raise PermissionDenied('文件不属于此订单')
    if asset.state != 'ready' or asset.purpose != 'original':
        raise Conflict('只能发布已通过校验的文件', current_revision=order.revision)
    now = timezone.now()
    _revoke_grants(order, now)
    if order.current_version_id:
        old = ConfirmationRequest.objects.get(version_id=order.current_version_id)
        old.superseded_at = now
        old.save(update_fields=['superseded_at'])
        ConfirmationEvent.objects.create(request=old, decision='superseded', actor=actor, verification_method='member_mfa')
    version = ProofVersion.objects.create(order=order, sequence=order.versions.count() + 1, asset=asset, sha256=asset.sha256, display_name=asset.display_name, uploaded_by=asset.uploaded_by, published_by=actor)
    invitation = Invitation(tenant_id=order.tenant_id, owner=order, file=asset, issued_by=actor, receiver=payload['recipient'], expires_at=now + timedelta(seconds=settings.INVITATION_TTL_SECONDS))
    invitation.token_hash = hash_token(token_for(invitation.id))
    invitation.save()
    confirmation = ConfirmationRequest.objects.create(version=version, invitation=invitation, recipient=payload['recipient'], expires_at=invitation.expires_at)
    order.current_version = version
    order.status = 'awaiting_confirmation'
    order.revision += 1
    order.save(update_fields=['current_version', 'status', 'revision'])
    return {**serialize_order(order), 'version_id': str(version.id), 'request_id': str(confirmation.id), 'asset_id': str(asset.id), 'invitation_id': str(invitation.id)}


def staff_command(tenant, actor, order_id, action, key, payload):
    from django.utils import timezone
    from .models import ConfirmationRequest, ConfirmationEvent, PrintReview
    # Recheck current scope before consulting an idempotency receipt. The same
    # Order lock covers state, history, pointer, grant invalidation and receipt.
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id, tenant=tenant)
        def command():
            _revision(order, payload)
            if action == 'publish':
                return publish_version(order, actor, payload)
            if not order.current_version_id:
                raise Conflict('尚未发布校样', current_revision=order.revision)
            confirmation = ConfirmationRequest.objects.select_related('invitation').get(version_id=order.current_version_id)
            check_current(order, confirmation)
            if action == 'revoke':
                now = timezone.now()
                confirmation.revoked_at = now
                confirmation.save(update_fields=['revoked_at'])
                _revoke_grants(order, now)
                ConfirmationEvent.objects.create(request=confirmation, decision='revoked', reason=payload['reason'], actor=actor, verification_method='member_mfa')
                order.status = 'revoked'
            elif action == 'print-review':
                event = confirmation.events.filter(decision='confirmed').first()
                invite = confirmation.invitation
                if order.status != 'confirmed' or event is None or invite.revoked_at or invite.expires_at <= timezone.now():
                    raise Conflict('当前校样尚无有效客户确认', current_revision=order.revision)
                if PrintReview.objects.filter(version_id=order.current_version_id).exists():
                    raise Conflict('当前版本已登记印前复核', current_revision=order.revision)
                PrintReview.objects.create(version_id=order.current_version_id, confirmation=event, actor=actor, verification_note=payload['verification_note'])
            order.revision += 1
            order.save(update_fields=['status', 'revision'])
            return serialize_order(order)
        return execute_once((tenant.id, actor.id, 'order.' + action, order_id), key, payload, command)


def decide_request(http_request, request_id, key, payload, order_id=None):
    from django.utils import timezone
    from rest_framework.exceptions import PermissionDenied
    from identity.models import ExternalSession
    from identity.invitations import check_invitation
    from .models import ConfirmationRequest, ConfirmationEvent
    confirmation = ConfirmationRequest.objects.select_related('version__order').filter(pk=request_id).first()
    if confirmation is None:
        raise NotFound()
    if order_id is not None and confirmation.version.order_id != order_id:
        raise PermissionDenied('请求不属于此订单')
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=confirmation.version.order_id)
        confirmation = ConfirmationRequest.objects.select_related('version__order').get(pk=request_id)
        actor = ExternalSession.objects.select_related('invitation__file', 'invitation__owner').filter(pk=http_request.session.get('external_session_id')).first()
        require_recipient(actor, confirmation)
        check_current(order, confirmation)
        if actor.revoked_at or actor.expires_at <= timezone.now():
            raise PermissionDenied('客户会话已失效')
        check_invitation(actor.invitation)
        def command():
            _revision(order, payload)
            if order.status != 'awaiting_confirmation' or confirmation.events.filter(decision__in=['confirmed', 'returned']).exists():
                raise Conflict('请求已有客户决定', current_revision=order.revision)
            event = ConfirmationEvent.objects.create(request=confirmation, decision=payload['decision'], reason=payload['reason'], external_session=actor, verification_method=actor.verification_method)
            order.status = payload['decision']
            order.revision += 1
            order.save(update_fields=['status', 'revision'])
            # Customer response contains only the authorized request/version,
            # never the employee history of other recipients or attachments.
            return {'id': str(order.id), 'status': order.status, 'revision': order.revision, 'current_version_id': str(order.current_version_id), 'request_id': str(confirmation.id), 'event_id': str(event.id)}
        # issuer is the receipt's FK partition only; the actual audit actor is
        # ExternalSession and the object scope is the exact invitation/request.
        return execute_once((order.tenant_id, actor.invitation.issued_by_id, 'request.decide', request_id), key, payload, command)
