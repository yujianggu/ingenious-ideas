import pytest
from rest_framework.test import APIClient

@pytest.mark.django_db
def test_error_statuses_and_envelope(api):
    anonymous = APIClient().get("/api/session")
    assert anonymous.status_code == 401
    assert anonymous.json() == {"code": "not_authenticated", "message": "Authentication credentials were not provided.", "current_revision": None}
    invalid = api.post("/api/orders", {"title": ""}, format="json", HTTP_IDEMPOTENCY_KEY="invalid-title")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"
    created = api.post("/api/orders", {"title": "初稿"}, format="json", HTTP_IDEMPOTENCY_KEY="contract-create").json()
    conflict = api.patch(f"/api/orders/{created['id']}", {"title": "修改", "expected_revision": 0}, format="json", HTTP_IDEMPOTENCY_KEY="contract-edit")
    assert conflict.status_code == 409
    assert conflict.json() == {"code": "revision_conflict", "message": "记录已被更新", "current_revision": 1}
