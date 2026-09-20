"""No employee impersonation: public verification creates a distinct external principal."""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.serializers import CharField, UUIDField
from rest_framework.views import APIView

from common.idempotency import execute_once
from config.exceptions import Conflict
from files.api import member_order
from files.models import FileRecord
from proofing.api import require_management_write
from proofing.models import Order
from .api import enforce_login_csrf
from .code_providers import get_code_provider
from .models import CodeDelivery, ExternalSession, Invitation
from .browser_binding import browser_nonce, set_browser_cookie


def token_for(invitation_id):
    # Stable replayable opaque token, derived from 122 random UUID bits + secret HMAC.
    # Neither the token nor its derivation key is stored in application rows/receipts.
    return salted_hmac('b16.invitation.token.v1', str(invitation_id), algorithm='sha256').hexdigest()


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def secret_hash(purpose, value):
    return salted_hmac('b16.invitation.' + purpose, value, algorithm='sha256').hexdigest()


def receiver_value(data):
    return CharField(max_length=254, allow_blank=False).run_validation(data.get('receiver')).strip()


def check_invitation(invitation):
    if invitation.revoked_at is not None or invitation.expires_at <= timezone.now():
        raise PermissionDenied('邀请已失效')
    if invitation.file.owner_id != invitation.owner_id or invitation.file.tenant_id != invitation.tenant_id or invitation.owner.tenant_id != invitation.tenant_id or invitation.file.state != 'ready':
        raise PermissionDenied('邀请范围无效')


def lookup_invitation(request):
    token = CharField(max_length=128, allow_blank=False).run_validation(request.data.get('token'))
    invitation = Invitation.objects.select_related('file', 'owner').filter(token_hash=hash_token(token)).first()
    if invitation is None or not constant_time_compare(invitation.receiver, receiver_value(request.data)):
        raise PermissionDenied('接收人核验失败')
    check_invitation(invitation)
    return invitation


def require_external_access(request, record=None):
    sid = request.session.get('external_session_id')
    if not sid:
        raise PermissionDenied('请先完成接收人核验')
    grant = ExternalSession.objects.select_related('invitation__file', 'invitation__owner').filter(pk=sid).first()
    if grant is None or grant.revoked_at is not None or grant.expires_at <= timezone.now():
        raise PermissionDenied('客户会话已失效')
    check_invitation(grant.invitation)
    if record is not None and (grant.invitation.file_id != record.id or grant.invitation.owner_id != record.owner_id or grant.invitation.tenant_id != record.tenant_id):
        raise PermissionDenied('当前客户会话不能访问此文件')
    return grant


def invalidate_invitation(invitation, at=None):
    """Call inside an Order-locked transaction; Task4 can revoke one superseded grant."""
    at = at or timezone.now()
    Invitation.objects.filter(pk=invitation.pk, revoked_at__isnull=True).update(revoked_at=at)
    ExternalSession.objects.filter(invitation=invitation, revoked_at__isnull=True).update(revoked_at=at)


class IssueInvitationView(APIView):
    def post(self, request, owner_id):
        require_management_write(request)
        order = member_order(request, owner_id)
        file_id = UUIDField().run_validation(request.data.get('file_id'))
        receiver = receiver_value(request.data)
        file = FileRecord.objects.filter(pk=file_id).first()
        if file is None:
            raise NotFound()
        if file.owner_id != order.id or file.tenant_id != order.tenant_id:
            raise PermissionDenied()
        if file.state != 'ready':
            raise Conflict('只能邀请查看已通过校验的文件')
        payload = dict(request.data)
        payload.update(file_id=str(file.id), receiver=receiver, owner_id=str(order.id))
        def command():
            Order.objects.select_for_update().get(pk=order.id)
            invitation = Invitation(tenant=order.tenant, owner=order, file=file, issued_by=request.user, receiver=receiver, expires_at=timezone.now() + timedelta(seconds=settings.INVITATION_TTL_SECONDS))
            invitation.token_hash = hash_token(token_for(invitation.id))
            invitation.save()
            return {'id': str(invitation.id), 'owner_id': str(order.id), 'file_id': str(file.id), 'receiver': receiver, 'expires_at': invitation.expires_at.isoformat()}
        result = execute_once((order.tenant_id, request.user.id, 'invitation.issue', order.id), request.headers.get('Idempotency-Key'), payload, command)
        invitation = Invitation.objects.select_related('file', 'owner').get(pk=result['id'])
        check_invitation(invitation)  # Even a persisted command receipt cannot revive a revoked grant.
        return Response({**result, 'token': token_for(result['id'])}, status=201)


class ExternalMutationView(APIView):
    permission_classes = [AllowAny]
    def guard(self, request):
        enforce_login_csrf(request._request)
        if request.user.is_authenticated:
            raise PermissionDenied('员工会话不能代替客户核验')


def delivery_code(delivery_id):
    # The random delivery UUID plus a domain-separated server secret make this
    # stable across uncertain acknowledgements without persisting plaintext OTPs.
    number = int(secret_hash('delivery-code.v1', str(delivery_id)), 16) % 1_000_000
    return f'{number:06d}'


def dispatch_code_delivery(invitation, delivery_id, browser_hash, provider):
    def command():
        Order.objects.select_for_update().get(pk=invitation.owner_id)
        locked = Invitation.objects.select_for_update().select_related('file', 'owner').get(pk=invitation.id)
        check_invitation(locked)
        delivery = CodeDelivery.objects.select_for_update().get(pk=delivery_id, invitation=locked)
        if locked.used_at or locked.code_attempts >= 5 or locked.current_delivery_id != delivery.id or delivery.expires_at <= timezone.now() or not constant_time_compare(delivery.browser_hash, browser_hash):
            raise PermissionDenied('验证码投递已失效')
        provider.send_code(locked.receiver, delivery_code(delivery.id), str(delivery.id))
        delivery.accepted_at = timezone.now()
        delivery.save(update_fields=['accepted_at'])
        return {'sent': True, 'invitation_id': str(locked.id)}
    # Reservation has already committed. A provider timeout only rolls back this
    # acknowledgement; its next attempt uses the same delivery ID and same code.
    return execute_once((invitation.tenant_id, invitation.issued_by_id, 'external.delivery', delivery_id), 'dispatch', {'invitation_id': str(invitation.id), 'delivery_id': str(delivery_id), 'browser_hash': browser_hash}, command)


class ChallengeView(ExternalMutationView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        return set_browser_cookie(response, getattr(request, '_recipient_browser_nonce', None))

    def post(self, request):
        self.guard(request)
        invitation = lookup_invitation(request)
        provider = get_code_provider()
        nonce = browser_nonce(request, create=True)
        request._recipient_browser_nonce = nonce
        request.session['invitation_browser_nonce'] = nonce
        browser_hash = secret_hash('browser', nonce)
        payload = {**dict(request.data), 'token': invitation.token_hash, 'browser_hash': browser_hash}
        def command():
            Order.objects.select_for_update().get(pk=invitation.owner_id)
            locked = Invitation.objects.select_for_update().select_related('file', 'owner').get(pk=invitation.pk)
            check_invitation(locked)
            if locked.used_at or locked.code_attempts >= 5:
                raise PermissionDenied('邀请已核验或已锁定')
            now = timezone.now()
            if locked.code_sends >= 5 or (locked.code_sent_at and (now - locked.code_sent_at).total_seconds() < 60):
                raise Throttled(wait=60)
            expires_at = min(locked.expires_at, now + timedelta(seconds=300))
            delivery = CodeDelivery.objects.create(invitation=locked, browser_hash=browser_hash, expires_at=expires_at)
            code = delivery_code(delivery.id)
            locked.current_delivery = delivery
            locked.code_hash = secret_hash('code', str(locked.id) + ':' + code)
            locked.code_browser_hash = browser_hash
            locked.code_expires_at = expires_at
            locked.code_sent_at = now
            locked.code_sends += 1
            locked.save(update_fields=['current_delivery', 'code_hash', 'code_browser_hash', 'code_expires_at', 'code_sent_at', 'code_sends'])
            return {'delivery_id': str(delivery.id), 'invitation_id': str(locked.id)}
        reservation = execute_once((invitation.tenant_id, invitation.issued_by_id, 'external.challenge', invitation.id), request.headers.get('Idempotency-Key'), payload, command)
        # Even an acknowledged dispatch receipt must not bypass current scope.
        invitation.refresh_from_db()
        check_invitation(invitation)
        delivery = CodeDelivery.objects.get(pk=reservation['delivery_id'], invitation=invitation)
        if invitation.used_at or invitation.code_attempts >= 5 or invitation.current_delivery_id != delivery.id or delivery.expires_at <= timezone.now() or not constant_time_compare(delivery.browser_hash, browser_hash):
            raise PermissionDenied('验证码投递已失效')
        result = dispatch_code_delivery(invitation, delivery.id, browser_hash, provider)
        request.session.save()
        return Response(result)


class VerifyView(ExternalMutationView):
    def post(self, request):
        self.guard(request)
        invitation = lookup_invitation(request)
        nonce = browser_nonce(request)
        if not nonce:
            raise PermissionDenied('请在接收验证码的浏览器完成核验')
        browser_hash = secret_hash('browser', nonce)
        if not constant_time_compare(invitation.code_browser_hash, browser_hash):
            raise PermissionDenied('请在接收验证码的浏览器完成核验')
        code = CharField(max_length=128, allow_blank=False).run_validation(request.data.get('code'))
        payload = {**dict(request.data), 'token': invitation.token_hash, 'code': secret_hash('code', str(invitation.id) + ':' + code), 'browser_hash': browser_hash}
        def command():
            Order.objects.select_for_update().get(pk=invitation.owner_id)
            locked = Invitation.objects.select_for_update().select_related('file', 'owner').get(pk=invitation.pk)
            check_invitation(locked)
            now = timezone.now()
            if locked.used_at or locked.code_attempts >= 5 or not locked.code_expires_at or locked.code_expires_at <= now or not constant_time_compare(locked.code_browser_hash, browser_hash):
                return {'denied': True}
            if not constant_time_compare(locked.code_hash, payload['code']):
                locked.code_attempts += 1
                locked.save(update_fields=['code_attempts'])
                return {'denied': True}  # Commit attempts, do not roll them back on a 403.
            grant = ExternalSession.objects.create(invitation=locked, expires_at=min(locked.expires_at, now + timedelta(seconds=settings.EXTERNAL_SESSION_TTL_SECONDS)))
            locked.used_at = now
            locked.code_hash = ''
            locked.save(update_fields=['used_at', 'code_hash'])
            return {'session_id': str(grant.id), 'principal': {'kind': 'external_recipient', 'invitation_id': str(locked.id), 'owner_id': str(locked.owner_id), 'file_id': str(locked.file_id), 'receiver': locked.receiver, 'verification_method': 'receiver_code'}}
        result = execute_once((invitation.tenant_id, invitation.issued_by_id, 'external.verify', invitation.id), request.headers.get('Idempotency-Key'), payload, command)
        if result.get('denied'):
            raise PermissionDenied('接收人验证码无效')
        # A successful receipt may precede session binding or a lost Set-Cookie.
        # Exact replay is safe only for this browser and a still-live same grant.
        grant = ExternalSession.objects.select_related('invitation__file', 'invitation__owner').get(pk=result['session_id'], invitation=invitation)
        if grant.revoked_at is not None or grant.expires_at <= timezone.now():
            raise PermissionDenied('客户会话已失效')
        check_invitation(grant.invitation)
        # Session identifier is only in the HttpOnly Django session, never the API body.
        request.session.cycle_key()
        request.session['external_session_id'] = result['session_id']
        request.session.save()
        require_external_access(request)
        return Response({'principal': result['principal']})


class RevokeAccessView(APIView):
    def post(self, request, owner_id):
        require_management_write(request)
        order = member_order(request, owner_id)
        payload = {**dict(request.data), 'owner_id': str(order.id)}
        def command():
            Order.objects.select_for_update().get(pk=order.id)
            now = timezone.now()
            for invite in Invitation.objects.filter(owner=order, revoked_at__isnull=True):
                invalidate_invitation(invite, now)
            return {'owner_id': str(order.id), 'revoked': True}
        return Response(execute_once((order.tenant_id, request.user.id, 'access.revoke', order.id), request.headers.get('Idempotency-Key'), payload, command))
