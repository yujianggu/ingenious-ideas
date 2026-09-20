from rest_framework.exceptions import PermissionDenied
from .models import Membership

def require_member(user, tenant_id, role):
    membership = Membership.objects.filter(user_id=user.id, tenant_id=tenant_id, is_active=True).first()
    if membership is None or role not in membership.roles:
        raise PermissionDenied("当前身份无此操作权限")
    return membership
