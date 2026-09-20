import hashlib
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from test_files import order, upload, validate, clean_scanner, private_config


def write(client, url, data=None, key=None):
    return client.post(url, data or {}, format='json', HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

@pytest.fixture
def code_provider(settings):
    from identity.code_providers import DevelopmentCodeProvider
    settings.CODE_PROVIDER = 'identity.code_providers.DevelopmentCodeProvider'
    DevelopmentCodeProvider.messages.clear()
    return DevelopmentCodeProvider


def issue(api, oid, fid, receiver='client@example.test', key=None):
    return write(api, f'/api/access/{oid}/invitations', {'file_id': fid, 'receiver': receiver}, key)


def ready(api):
    oid = order(api)
    fid = upload(api, oid).json()['id']
    assert validate(api, fid).json()['state'] == 'ready'
    return oid, fid


def guest_challenge(token, code_provider, receiver='client@example.test'):
    guest = APIClient(enforce_csrf_checks=True)
    csrf = guest.get('/api/session/csrf').json()['csrf_token']
    guest.credentials(HTTP_X_CSRFTOKEN=csrf)
    r = write(guest, '/api/access/challenge', {'token': token, 'receiver': receiver})
    assert r.status_code == 200
    return guest, code_provider.messages[-1]['code']


def verify(guest, token, code, receiver='client@example.test', key=None):
    return write(guest, '/api/access/verify', {'token': token, 'receiver': receiver, 'code': code}, key)

@pytest.mark.django_db
def test_invitation_hash_receiver_scope_and_revoke_get_range(api, other_api, clean_scanner, code_provider):
    from identity.models import Invitation, ExternalSession
    from common.models import CommandReceipt
    oid, fid = ready(api)
    r = issue(api, oid, fid, key='issue')
    assert r.status_code == 201
    issued = r.json()
    token = issued['token']
    assert issue(api, oid, fid, key='issue').json() == issued
    invitation = Invitation.objects.get(pk=issued['id'])
    assert invitation.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(list(CommandReceipt.objects.values()))
    assert other_api.post(f'/api/access/{oid}/revoke', {}, HTTP_IDEMPOTENCY_KEY='revoke').status_code == 403
    guest, code = guest_challenge(token, code_provider)
    assert verify(guest, token, code, receiver='wrong@example.test').status_code == 403
    verified = verify(guest, token, code, key='verify')
    assert verified.status_code == 200
    assert verified.json()['principal']['kind'] == 'external_recipient'
    assert verified.json()['principal']['invitation_id'] == issued['id']
    assert verify(guest, token, code, key='verify').json() == verified.json()
    assert ExternalSession.objects.count() == 1
    url = f'/api/files/{fid}/content'
    assert guest.get(url).status_code == 200
    ranged = guest.get(url, HTTP_RANGE='bytes=0-5')
    assert ranged.status_code == 206
    other_file = upload(api, oid).json()['id']
    assert validate(api, other_file).json()['state'] == 'ready'
    assert guest.get(f'/api/files/{other_file}/content').status_code == 403
    assert guest.get(f'/api/orders/{oid}').status_code == 401
    assert write(api, f'/api/access/{oid}/revoke', key='revoke').status_code == 200
    assert guest.get(url).status_code == 403
    assert guest.get(url, HTTP_RANGE='bytes=0-5').status_code == 403
    assert verify(guest, token, code, key='verify').status_code == 403

@pytest.mark.django_db
def test_invitation_and_external_session_expiry(api, clean_scanner, code_provider):
    from identity.models import Invitation, ExternalSession
    oid, fid = ready(api)
    invite = issue(api, oid, fid).json()
    guest, code = guest_challenge(invite['token'], code_provider)
    assert verify(guest, invite['token'], code).status_code == 200
    ExternalSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert guest.get(f'/api/files/{fid}/content').status_code == 403
    Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert verify(guest, invite['token'], code).status_code == 403

@pytest.mark.django_db
def test_wrong_code_attempts_lock_and_token_alone_denied(api, clean_scanner, code_provider):
    from identity.models import ExternalSession
    oid, fid = ready(api)
    invite = issue(api, oid, fid).json()
    assert APIClient().get(f'/api/files/{fid}/content', HTTP_AUTHORIZATION='Bearer ' + invite['token']).status_code == 403
    guest, code = guest_challenge(invite['token'], code_provider)
    for _ in range(5):
        assert verify(guest, invite['token'], 'invalid').status_code == 403
    assert verify(guest, invite['token'], code).status_code == 403
    assert not ExternalSession.objects.exists()

@pytest.mark.django_db
def test_invitation_rejects_quarantine_cross_owner_and_member_impersonation(api, other_api, clean_scanner, code_provider):
    oid = order(api)
    fid = upload(api, oid).json()['id']
    assert issue(api, oid, fid).status_code == 409
    assert validate(api, fid).json()['state'] == 'ready'
    assert issue(api, order(api), fid).status_code == 403
    assert issue(other_api, oid, fid).status_code == 403
    invite = issue(api, oid, fid).json()
    assert write(api, '/api/access/challenge', {'token': invite['token'], 'receiver': 'client@example.test'}).status_code == 403

@pytest.mark.django_db
def test_guest_mutations_require_csrf_and_code_bound_browser(api, clean_scanner, code_provider):
    oid, fid = ready(api)
    invite = issue(api, oid, fid).json()
    guest = APIClient(enforce_csrf_checks=True)
    assert write(guest, '/api/access/challenge', {'token': invite['token'], 'receiver': 'client@example.test'}).status_code == 403
    guest, code = guest_challenge(invite['token'], code_provider)
    different = APIClient()
    assert verify(different, invite['token'], code).status_code == 403
    assert verify(guest, invite['token'], code).status_code == 200
    assert verify(guest, invite['token'], code).status_code == 403  # new command cannot reuse consumed code

@pytest.mark.django_db
def test_code_expiry_and_provider_unavailable(api, clean_scanner, code_provider, settings):
    from identity.models import Invitation
    oid, fid = ready(api)
    invite = issue(api, oid, fid).json()
    guest, code = guest_challenge(invite['token'], code_provider)
    Invitation.objects.update(code_expires_at=timezone.now() - timedelta(seconds=1))
    assert verify(guest, invite['token'], code).status_code == 403
    settings.CODE_PROVIDER = ''
    guest = APIClient()
    assert write(guest, '/api/access/challenge', {'token': invite['token'], 'receiver': 'client@example.test'}).status_code == 503
