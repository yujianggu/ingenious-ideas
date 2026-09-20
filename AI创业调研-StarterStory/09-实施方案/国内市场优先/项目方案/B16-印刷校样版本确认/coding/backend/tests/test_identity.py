import pytest
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied
from identity.models import Membership
from identity.policy import require_member

@pytest.mark.django_db
def test_unauthenticated_and_cross_tenant(api, other_api):
    assert APIClient().get("/api/session").status_code == 401
    own = api.post("/api/orders", {"title": "本机构记录"}, format="json", HTTP_IDEMPOTENCY_KEY="identity-own")
    assert own.status_code == 201
    record_id = own.json()["id"]
    assert other_api.get(f"/api/orders/{record_id}").status_code == 403
    assert other_api.patch(
        f"/api/orders/{record_id}",
        {"title": "跨租户修改", "expected_revision": 1},
        format="json",
        HTTP_IDEMPOTENCY_KEY="cross-tenant-edit",
    ).status_code == 403
    assert other_api.get("/api/orders").json()["results"] == []

@pytest.mark.django_db
def test_session_exposes_actor_tenant_and_roles(api, owner, tenant):
    payload = api.get("/api/session").json()
    assert payload == {"actor": {"id": owner.id, "username": "owner"}, "tenant": {"id": str(tenant.id), "name": "甲印刷店"}, "roles": ["owner"], "otp_verified": True}

@pytest.mark.django_db
def test_inactive_membership_denies_list_and_direct_access(api, owner, tenant):
    created = api.post("/api/orders", {"title": "停用前"}, format="json", HTTP_IDEMPOTENCY_KEY="identity-inactive").json()
    Membership.objects.filter(user=owner, tenant=tenant).update(is_active=False)
    assert api.get("/api/orders").status_code == 403
    assert api.get(f"/api/orders/{created['id']}").status_code == 403

@pytest.mark.django_db
def test_logout_revokes_session(api):
    assert api.post("/api/session/logout").status_code == 204
    assert api.get("/api/session").status_code == 401

@pytest.mark.django_db
def test_require_member_checks_role(owner, tenant):
    assert require_member(owner, tenant.id, "owner").tenant_id == tenant.id
    with pytest.raises(PermissionDenied):
        require_member(owner, tenant.id, "reviewer")

@pytest.mark.django_db
def test_owner_write_requires_verified_second_factor(owner, tenant):
    client = APIClient(); client.force_login(owner)
    session = client.session; session["active_tenant"] = str(tenant.id); session.save()
    assert client.post("/api/orders", {"title": "未验证"}, format="json").status_code == 403

@pytest.mark.django_db
def test_owner_update_requires_verified_second_factor(api, owner, tenant):
    created = api.post("/api/orders", {"title": "原稿"}, format="json", HTTP_IDEMPOTENCY_KEY="identity-update").json()
    client = APIClient(); client.force_login(owner)
    session = client.session; session["active_tenant"] = str(tenant.id); session.save()
    response = client.patch(f"/api/orders/{created['id']}", {"title": "越权修改", "expected_revision": 1}, format="json")
    assert response.status_code == 403

@pytest.mark.django_db
def test_login_rejects_malformed_tenant_as_validation_error(owner):
    client = APIClient()
    response = client.post("/api/session/login", {"username": "owner", "password": "correct-horse", "tenant_id": "not-a-uuid", "otp_token": "000000"}, format="json")
    assert response.status_code == 422
    assert response.json() == {"code": "validation_error", "message": "提交字段无效", "current_revision": None}
