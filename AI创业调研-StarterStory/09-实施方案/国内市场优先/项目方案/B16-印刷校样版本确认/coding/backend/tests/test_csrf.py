import pytest
from rest_framework.test import APIClient

@pytest.mark.django_db
def test_authenticated_write_without_csrf_is_forbidden(owner, tenant):
    client = APIClient(enforce_csrf_checks=True); client.force_login(owner)
    session = client.session
    session["active_tenant"] = str(tenant.id); session["otp_verified_user_id"] = owner.id; session.save()
    assert client.post("/api/orders", {"title": "不得写入"}, format="json").status_code == 403

@pytest.mark.django_db
def test_login_requires_csrf(owner):
    client = APIClient(enforce_csrf_checks=True)
    response = client.post("/api/session/login", {"username": "owner", "password": "correct-horse"}, format="json")
    assert response.status_code == 403
    assert response.json() == {"code": "permission_denied", "message": "CSRF Failed", "current_revision": None}
