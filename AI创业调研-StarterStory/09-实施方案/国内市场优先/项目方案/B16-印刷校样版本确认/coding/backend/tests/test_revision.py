import pytest

from common.models import CommandReceipt
from identity.models import Membership, User
from proofing.models import Order


pytestmark = pytest.mark.django_db


def test_stale_edit_keeps_current_record(api):
    first = api.post(
        "/api/orders", {"title": "甲"}, format="json", HTTP_IDEMPOTENCY_KEY="create-1"
    ).json()
    url = f"/api/orders/{first['id']}"
    payload = {"title": "修改甲", "expected_revision": first["revision"]}

    response = api.patch(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="edit-1")

    assert response.status_code == 200
    assert api.patch(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="edit-1").json() == response.json()
    assert api.patch(
        url, {**payload, "title": "覆盖"}, format="json", HTTP_IDEMPOTENCY_KEY="edit-1"
    ).status_code == 409
    assert api.patch(url, payload, format="json", HTTP_IDEMPOTENCY_KEY="edit-2").status_code == 409
    assert api.get(url).json() == response.json()


def test_patch_only_changes_the_addressed_order(api):
    first = api.post(
        "/api/orders", {"title": "甲"}, format="json", HTTP_IDEMPOTENCY_KEY="create-a"
    ).json()
    second = api.post(
        "/api/orders", {"title": "乙"}, format="json", HTTP_IDEMPOTENCY_KEY="create-b"
    ).json()

    response = api.patch(
        f"/api/orders/{first['id']}",
        {"title": "新甲", "expected_revision": 1},
        format="json",
        HTTP_IDEMPOTENCY_KEY="edit-a",
    )

    assert response.json() == {"id": first["id"], "title": "新甲", "revision": 2, "status": "draft", "current_version_id": None, "versions": []}
    assert api.get(f"/api/orders/{second['id']}").json() == second


def test_create_replays_result_and_rejects_changed_full_payload(api):
    response = api.post(
        "/api/orders", {"title": "同一草稿", "client_note": "第一版"},
        format="json", HTTP_IDEMPOTENCY_KEY="create-replay",
    )

    replay = api.post(
        "/api/orders", {"client_note": "第一版", "title": "同一草稿"},
        format="json", HTTP_IDEMPOTENCY_KEY="create-replay",
    )
    conflict = api.post(
        "/api/orders", {"title": "同一草稿", "client_note": "第二版"},
        format="json", HTTP_IDEMPOTENCY_KEY="create-replay",
    )

    assert response.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == response.json()
    assert conflict.status_code == 409
    assert Order.objects.count() == 1


def test_idempotency_key_is_scoped_by_actor_and_tenant(api, owner, tenant, other_tenant):
    colleague = User.objects.create_user("colleague", password="irrelevant")
    Membership.objects.create(user=colleague, tenant=tenant, roles=["owner"])
    from tests.conftest import verified_client

    colleague_api = verified_client(colleague, tenant)
    first = api.post(
        "/api/orders", {"title": "甲"}, format="json", HTTP_IDEMPOTENCY_KEY="shared"
    )
    second = colleague_api.post(
        "/api/orders", {"title": "乙"}, format="json", HTTP_IDEMPOTENCY_KEY="shared"
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

    Membership.objects.create(user=owner, tenant=other_tenant, roles=["owner"])
    owner_other_tenant_api = verified_client(owner, other_tenant)
    third = owner_other_tenant_api.post(
        "/api/orders", {"title": "丙"}, format="json", HTTP_IDEMPOTENCY_KEY="shared"
    )
    assert third.status_code == 201
    assert third.json()["id"] not in {first.json()["id"], second.json()["id"]}


def test_write_commands_require_idempotency_key(api):
    response = api.post("/api/orders", {"title": "无键"}, format="json")

    assert response.status_code == 422
    assert Order.objects.count() == 0


def test_idempotency_key_over_model_limit_is_structured_validation_without_mutation(api):
    api.raise_request_exception = False

    response = api.post(
        "/api/orders",
        {"title": "过长键"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="k" * 201,
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "validation_error",
        "message": "提交字段无效",
        "current_revision": None,
    }
    assert Order.objects.count() == 0
    assert CommandReceipt.objects.count() == 0


def test_idempotency_key_at_model_limit_is_accepted_and_replayed(api):
    key = "k" * 200
    payload = {"title": "边界键"}

    first = api.post("/api/orders", payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
    replay = api.post("/api/orders", payload, format="json", HTTP_IDEMPOTENCY_KEY=key)

    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert Order.objects.count() == 1
    assert CommandReceipt.objects.count() == 1
