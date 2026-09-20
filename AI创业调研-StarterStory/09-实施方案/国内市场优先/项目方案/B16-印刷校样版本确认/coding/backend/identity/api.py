from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.serializers import UUIDField
from rest_framework.views import APIView
from .models import Membership

OTP_LIMIT = 5
OTP_LIMIT_SECONDS = 60

def enforce_login_csrf(request):
    check = CSRFCheck(lambda _request: None)
    check.process_request(request)
    if check.process_view(request, None, (), {}):
        raise PermissionDenied("CSRF Failed")

def otp_limit_state(request, username):
    key = f"otp_failures:{username}"
    state = request.session.get(key, {"count": 0, "blocked_until": None})
    now = timezone.now().timestamp()
    blocked_until = state.get("blocked_until")
    if blocked_until and now >= blocked_until:
        state = {"count": 0, "blocked_until": None}
        request.session.pop(key, None)
    if state["count"] >= OTP_LIMIT:
        raise Throttled(wait=max(1, int(blocked_until - now)))
    return key, state

def record_otp_failure(request, key, state):
    count = state["count"] + 1
    request.session[key] = {"count": count, "blocked_until": timezone.now().timestamp() + OTP_LIMIT_SECONDS if count >= OTP_LIMIT else None}
    request.session.save()

def verify_second_factor(user, token=None, recovery=None):
    with transaction.atomic():
        if token:
            for device_id in TOTPDevice.objects.filter(user=user, confirmed=True).values_list("id", flat=True):
                device = TOTPDevice.objects.select_for_update().get(id=device_id)
                if device.verify_token(token):
                    return True
        if recovery:
            for device_id in StaticDevice.objects.filter(user=user, confirmed=True).values_list("id", flat=True):
                device = StaticDevice.objects.select_for_update().get(id=device_id)
                if device.verify_token(recovery):
                    return True
    return False

class CsrfView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    def get(self, request):
        from .browser_binding import browser_nonce, set_browser_cookie
        response = Response({"csrf_token": get_token(request)})
        return set_browser_cookie(response, browser_nonce(request, create=True))

class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    def post(self, request):
        enforce_login_csrf(request._request)
        username = request.data.get("username", "")
        failure_key, failure_state = otp_limit_state(request, username)
        user = authenticate(request, username=username, password=request.data.get("password"))
        if user is None:
            raise PermissionDenied("用户名或密码错误")
        tenant_id = UUIDField().run_validation(request.data.get("tenant_id"))
        membership = Membership.objects.filter(user=user, tenant_id=tenant_id, is_active=True).first()
        if membership is None:
            raise PermissionDenied("当前身份无此操作权限")
        token = request.data.get("otp_token")
        recovery = request.data.get("recovery_token")
        if not verify_second_factor(user, token, recovery):
            record_otp_failure(request, failure_key, failure_state)
            raise PermissionDenied("二次验证码无效")
        login(request, user)
        request.session["active_tenant"] = str(membership.tenant_id)
        request.session["otp_verified_user_id"] = user.id
        request.session.pop(failure_key, None)
        request.session.save()
        return Response({"authenticated": True, "csrf_token": get_token(request)})

class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=204)

class SessionView(APIView):
    def get(self, request):
        tenant_id = request.session.get("active_tenant")
        membership = Membership.objects.select_related("tenant").filter(user=request.user, tenant_id=tenant_id, is_active=True).first()
        if membership is None:
            raise PermissionDenied("当前身份无此操作权限")
        return Response({"actor": {"id": request.user.id, "username": request.user.username},
                         "tenant": {"id": str(membership.tenant_id), "name": membership.tenant.name},
                         "roles": membership.roles,
                         "otp_verified": request.session.get("otp_verified_user_id") == request.user.id})
