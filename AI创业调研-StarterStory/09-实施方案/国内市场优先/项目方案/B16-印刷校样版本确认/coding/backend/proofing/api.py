from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from identity.models import Membership
from .commands import create_order, serialize_order, update_order
from .models import Order

def active_scope(request):
    tenant_id = request.session.get("active_tenant")
    if not tenant_id:
        raise PermissionDenied("未选择当前机构")
    membership = Membership.objects.filter(user_id=request.user.id, tenant_id=tenant_id, is_active=True).first()
    if membership is None or not {'owner', 'employee', 'designer'}.intersection(membership.roles):
        raise PermissionDenied("当前身份无此操作权限")
    return membership

def serialize(order):
    return serialize_order(order)

def require_management_write(request):
    if request.session.get("otp_verified_user_id") != request.user.id:
        raise PermissionDenied("管理员操作需要二次验证")

class OrderListView(APIView):
    def get(self, request):
        member = active_scope(request)
        return Response({"results": [serialize(o) for o in Order.objects.filter(tenant=member.tenant).order_by("created_at")]})
    def post(self, request):
        member = active_scope(request)
        require_management_write(request)
        title = str(request.data.get("title", "")).strip()
        if not title:
            raise ValidationError({"title": ["不能为空"]})
        payload = dict(request.data)
        payload["title"] = title
        result = create_order(member.tenant, request.user, request.headers.get("Idempotency-Key"), payload)
        return Response(result, status=201)

class OrderDetailView(APIView):
    def _order(self, request, order_id):
        member = active_scope(request)
        order = Order.objects.filter(id=order_id).first()
        if order is None:
            from rest_framework.exceptions import NotFound
            raise NotFound()
        if order.tenant_id != member.tenant_id:
            raise PermissionDenied("当前身份无此操作权限")
        return order
    def get(self, request, order_id):
        return Response(serialize(self._order(request, order_id)))
    def patch(self, request, order_id):
        require_management_write(request)
        authorized_order = self._order(request, order_id)
        title = str(request.data.get("title", "")).strip()
        if not title:
            raise ValidationError({"title": ["不能为空"]})
        expected_revision = request.data.get("expected_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValidationError({"expected_revision": ["必须为整数"]})
        payload = dict(request.data)
        payload.update(title=title, expected_revision=expected_revision)
        result = update_order(authorized_order.tenant, request.user, order_id, request.headers.get("Idempotency-Key"), payload)
        return Response(result)


def command_payload(data, action):
    from rest_framework.serializers import CharField, ChoiceField, UUIDField
    revision = data.get('expected_revision')
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise ValidationError({'expected_revision': ['必须为整数']})
    payload = {**dict(data), 'expected_revision': revision}
    if action == 'publish':
        payload['asset_id'] = str(UUIDField().run_validation(data.get('asset_id')))
        payload['recipient'] = CharField(max_length=254).run_validation(data.get('recipient'))
    elif action == 'decide':
        payload['decision'] = ChoiceField(choices=['confirmed', 'returned']).run_validation(data.get('decision'))
        payload['reason'] = CharField(max_length=4000, allow_blank=True).run_validation(data.get('reason', ''))
        if payload['decision'] == 'returned' and not payload['reason']:
            raise ValidationError({'reason': ['退回必须填写原因']})
    elif action == 'revoke':
        payload['reason'] = CharField(max_length=4000, allow_blank=True).run_validation(data.get('reason', ''))
    elif action == 'print-review':
        payload['verification_note'] = CharField(max_length=4000, allow_blank=True).run_validation(data.get('verification_note', ''))
    else:
        raise ValidationError({'action': ['未知操作']})
    return payload


def customer_guard(request, mutation=True):
    from identity.api import enforce_login_csrf
    if request.user.is_authenticated:
        raise PermissionDenied('员工会话不能代替客户决定')
    if mutation:
        enforce_login_csrf(request._request)


from rest_framework.permissions import AllowAny


class OrderCommandView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, order_id, action):
        from rest_framework.exceptions import NotAuthenticated
        from rest_framework.serializers import UUIDField
        from .commands import staff_command, decide_request
        if action == 'decide':
            customer_guard(request)
            request_id = UUIDField().run_validation(request.data.get('request_id'))
            return Response(decide_request(request, request_id, request.headers.get('Idempotency-Key'), command_payload(request.data, action), order_id=order_id))
        if not request.user.is_authenticated:
            raise NotAuthenticated()
        order = OrderDetailView()._order(request, order_id)
        require_management_write(request)
        payload = command_payload(request.data, action)
        result = staff_command(order.tenant, request.user, order.id, action, request.headers.get('Idempotency-Key'), payload)
        if action == 'publish':
            from identity.invitations import token_for
            from .models import ConfirmationRequest
            from .commands import check_current
            from django.db import transaction
            # A replay can never revive an old invitation or advertise its token.
            with transaction.atomic():
                current = Order.objects.select_for_update().get(pk=order.id)
                confirmation = ConfirmationRequest.objects.get(pk=result['request_id'])
                check_current(current, confirmation)
                result = {**result, 'invitation_token': token_for(result['invitation_id'])}
        return Response(result)


class DecisionView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, request_id):
        from .commands import decide_request
        customer_guard(request)
        return Response(decide_request(request, request_id, request.headers.get('Idempotency-Key'), command_payload(request.data, 'decide')))


class CustomerRequestView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, request_id):
        from django.db import transaction
        from rest_framework.exceptions import NotFound
        from identity.invitations import require_external_access
        from .models import ConfirmationRequest
        from .commands import require_recipient, check_current
        customer_guard(request, mutation=False)
        confirmation = ConfirmationRequest.objects.select_related('version__order').filter(pk=request_id).first()
        if confirmation is None:
            raise NotFound()
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=confirmation.version.order_id)
            confirmation.refresh_from_db()
            actor = require_external_access(request)
            require_recipient(actor, confirmation)
            check_current(order, confirmation)
            version = confirmation.version
            return Response({'id': str(confirmation.id), 'order_id': str(order.id), 'revision': order.revision, 'status': order.status, 'recipient': confirmation.recipient, 'expires_at': confirmation.expires_at.isoformat(), 'version': {'id': str(version.id), 'sequence': version.sequence, 'asset_id': str(version.asset_id), 'sha256': version.sha256, 'display_name': version.display_name}})
