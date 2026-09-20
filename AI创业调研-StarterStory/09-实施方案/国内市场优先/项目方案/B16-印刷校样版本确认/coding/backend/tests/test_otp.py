import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from django.utils import timezone
from django.db import connections
from django_otp.oath import totp
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

def csrf_client():
    client = APIClient(enforce_csrf_checks=True)
    response = client.get("/api/session/csrf")
    return client, response.json()["csrf_token"]

@pytest.mark.django_db
def test_totp_login_establishes_verified_owner_session(owner, tenant):
    device = TOTPDevice.objects.create(user=owner, confirmed=True)
    client, csrf = csrf_client()
    response = client.post("/api/session/login", {"username": "owner", "password": "correct-horse", "tenant_id": str(tenant.id), "otp_token": str(totp(device.bin_key)).zfill(6)}, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 200
    assert client.post("/api/orders", {"title": "已验证"}, format="json", HTTP_X_CSRFTOKEN=response.json()["csrf_token"], HTTP_IDEMPOTENCY_KEY="otp-create").status_code == 201

@pytest.mark.django_db
def test_recovery_code_is_consumed_once_and_not_logged(owner, tenant, caplog):
    device = StaticDevice.objects.create(user=owner, confirmed=True)
    StaticToken.objects.create(device=device, token="recovery-secret")
    caplog.set_level(logging.INFO)
    client, csrf = csrf_client()
    payload = {"username": "owner", "password": "correct-horse", "tenant_id": str(tenant.id), "recovery_token": "recovery-secret"}
    login_response = client.post("/api/session/login", payload, format="json", HTTP_X_CSRFTOKEN=csrf)
    assert login_response.status_code == 200
    assert client.post("/api/session/logout", format="json", HTTP_X_CSRFTOKEN=login_response.json()["csrf_token"]).status_code == 204
    csrf = client.get("/api/session/csrf").json()["csrf_token"]
    assert client.post("/api/session/login", payload, format="json", HTTP_X_CSRFTOKEN=csrf).status_code == 403
    assert "recovery-secret" not in caplog.text

@pytest.mark.django_db
def test_otp_failures_are_rate_limited(owner, tenant):
    TOTPDevice.objects.create(user=owner, confirmed=True)
    client, csrf = csrf_client()
    payload = {"username": "owner", "password": "correct-horse", "tenant_id": str(tenant.id), "otp_token": "000000"}
    for _ in range(5):
        assert client.post("/api/session/login", payload, format="json", HTTP_X_CSRFTOKEN=csrf).status_code == 403
    assert client.post("/api/session/login", payload, format="json", HTTP_X_CSRFTOKEN=csrf).status_code == 429

@pytest.mark.django_db
def test_otp_rate_limit_expires_and_valid_recovery_code_succeeds(owner, tenant, monkeypatch):
    device = StaticDevice.objects.create(user=owner, confirmed=True)
    StaticToken.objects.create(device=device, token="after-window")
    now = timezone.now()
    monkeypatch.setattr("identity.api.timezone.now", lambda: now)
    monkeypatch.setattr("django_otp.models.timezone.now", lambda: now)
    client = APIClient()
    invalid = {"username": "owner", "password": "correct-horse", "tenant_id": str(tenant.id), "recovery_token": "wrong"}
    for _ in range(5):
        assert client.post("/api/session/login", invalid, format="json").status_code == 403
    limited = client.post("/api/session/login", invalid, format="json")
    assert limited.status_code == 429
    assert limited["Retry-After"] == "60"
    later = now + timedelta(seconds=61)
    monkeypatch.setattr("identity.api.timezone.now", lambda: later)
    monkeypatch.setattr("django_otp.models.timezone.now", lambda: later)
    valid = invalid | {"recovery_token": "after-window"}
    assert client.post("/api/session/login", valid, format="json").status_code == 200

def concurrent_login_statuses(payload, workers=2):
    barrier = threading.Barrier(workers)
    def login_once():
        client = APIClient()
        try:
            barrier.wait()
            return client.post("/api/session/login", payload, format="json").status_code
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda _: login_once(), range(workers)))

@pytest.mark.django_db(transaction=True)
def test_recovery_code_cannot_be_consumed_by_two_postgresql_connections(owner, tenant):
    device = StaticDevice.objects.create(user=owner, confirmed=True)
    StaticToken.objects.create(device=device, token="one-use-race")
    payload = {"username": "owner", "password": "correct-horse", "tenant_id": str(tenant.id), "recovery_token": "one-use-race"}
    assert sorted(concurrent_login_statuses(payload)) == [200, 403]

@pytest.mark.django_db(transaction=True)
def test_totp_timestep_cannot_verify_on_two_postgresql_connections(owner, tenant):
    device = TOTPDevice.objects.create(user=owner, confirmed=True)
    token = str(totp(device.bin_key)).zfill(6)
    payload = {"username": "owner", "password": "correct-horse", "tenant_id": str(tenant.id), "otp_token": token}
    assert sorted(concurrent_login_statuses(payload)) == [200, 403]
