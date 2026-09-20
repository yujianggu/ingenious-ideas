"""Real PostgreSQL regressions for delivery/session acknowledgement loss."""
from datetime import timedelta

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.utils import timezone
from rest_framework.test import APIClient

from identity.code_providers import DeliveryUnavailable
from identity.models import ExternalSession, Invitation
from test_files import private_config, clean_scanner
from test_invitations import code_provider, guest_challenge, issue, ready, verify, write


def csrf_guest():
    guest = APIClient(enforce_csrf_checks=True)
    csrf = guest.get('/api/session/csrf').json()['csrf_token']
    guest.credentials(HTTP_X_CSRFTOKEN=csrf)
    return guest


def cookies_snapshot(guest):
    return {name: morsel.value for name, morsel in guest.cookies.items()}


def restore_cookies(guest, cookies):
    guest.cookies.clear()
    for name, value in cookies.items():
        guest.cookies[name] = value


def uncertain_delivery(monkeypatch):
    import identity.invitations as invitations
    delivered = {}
    class Bridge:
        def send_code(self, receiver, code, request_id):
            if request_id not in delivered:
                delivered[request_id] = (receiver, code)
                raise DeliveryUnavailable()  # Accepted exactly once; response was lost.
            # A real idempotent provider acknowledges its original accepted payload.
    monkeypatch.setattr(invitations, 'get_code_provider', lambda: Bridge())
    numbers = iter([123456, 654321, 987654])
    monkeypatch.setattr(invitations.secrets, 'randbelow', lambda _: next(numbers))
    return delivered


@pytest.mark.django_db(transaction=True)
def test_uncertain_delivery_reserves_budget_before_external_effect(api, clean_scanner, monkeypatch):
    oid, fid = ready(api)
    invitation = issue(api, oid, fid).json()
    delivered = uncertain_delivery(monkeypatch)
    guest = csrf_guest()
    body = {'token': invitation['token'], 'receiver': 'client@example.test'}
    assert write(guest, '/api/access/challenge', body, key='send').status_code == 503
    stored = Invitation.objects.get(pk=invitation['id'])
    assert stored.code_sends == 1
    assert stored.code_hash and stored.code_expires_at > timezone.now()
    assert len(delivered) == 1
    assert write(guest, '/api/access/challenge', body, key='new-command').status_code == 429


@pytest.mark.django_db(transaction=True)
def test_delivery_accepted_ack_lost_retry_recovers_original_code(api, clean_scanner, monkeypatch):
    oid, fid = ready(api)
    invitation = issue(api, oid, fid).json()
    delivered = uncertain_delivery(monkeypatch)
    guest = csrf_guest()
    # Isolate provider acknowledgement loss from Django session persistence.
    session = guest.session
    session['invitation_browser_nonce'] = 'preexisting-browser-nonce'
    session.save()
    body = {'token': invitation['token'], 'receiver': 'client@example.test'}
    assert write(guest, '/api/access/challenge', body, key='send').status_code == 503
    assert write(guest, '/api/access/challenge', body, key='send').status_code == 200
    assert len(delivered) == 1
    actual_code = next(iter(delivered.values()))[1]
    assert verify(guest, invitation['token'], actual_code).status_code == 200
    assert Invitation.objects.get(pk=invitation['id']).code_sends == 1
    from common.models import CommandReceipt
    assert all(actual_code not in str(row.result_json) for row in CommandReceipt.objects.all())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('loss', ['response', 'before_cycle', 'after_cycle'])
def test_verify_exact_retry_recovers_grant_after_session_loss(api, clean_scanner, code_provider, monkeypatch, loss):
    oid, fid = ready(api)
    invitation = issue(api, oid, fid).json()
    guest, code = guest_challenge(invitation['token'], code_provider)
    old_cookies = cookies_snapshot(guest)
    if loss == 'response':
        assert verify(guest, invitation['token'], code, key='verify').status_code == 200
    else:
        with monkeypatch.context() as patch:
            if loss == 'before_cycle':
                def fail_cycle(_session):
                    raise RuntimeError('session binding breakpoint')
                patch.setattr(SessionStore, 'cycle_key', fail_cycle)
            else:
                save = SessionStore.save
                def fail_bound_save(session, *args, **kwargs):
                    if session.get('external_session_id'):
                        raise RuntimeError('session binding breakpoint')
                    return save(session, *args, **kwargs)
                patch.setattr(SessionStore, 'save', fail_bound_save)
            with pytest.raises(RuntimeError, match='session binding breakpoint'):
                verify(guest, invitation['token'], code, key='verify')
    assert ExternalSession.objects.count() == 1
    restore_cookies(guest, old_cookies)
    recovered = verify(guest, invitation['token'], code, key='verify')
    assert recovered.status_code == 200
    assert recovered.json()['principal']['invitation_id'] == invitation['id']
    assert ExternalSession.objects.count() == 1
    response = guest.get(f'/api/files/{fid}/content', HTTP_RANGE='bytes=0-4')
    assert response.status_code == 206
    assert b''.join(response.streaming_content) == b'%PDF-'
    # Recovery cannot make a consumed code work for a new command or browser.
    assert verify(guest, invitation['token'], code, key='new-verify').status_code == 403
    outsider = csrf_guest()
    assert verify(outsider, invitation['token'], code, key='verify').status_code == 403
    assert verify(guest, invitation['token'], 'different-code', key='verify').status_code == 409
    assert ExternalSession.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('invalidity', ['invite_expired', 'grant_expired', 'revoked'])
def test_recovery_never_revives_expired_or_revoked_grant(api, clean_scanner, code_provider, invalidity):
    oid, fid = ready(api)
    invitation = issue(api, oid, fid).json()
    guest, code = guest_challenge(invitation['token'], code_provider)
    old_cookies = cookies_snapshot(guest)
    assert verify(guest, invitation['token'], code, key='verify').status_code == 200
    if invalidity == 'invite_expired':
        Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    elif invalidity == 'grant_expired':
        ExternalSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    else:
        assert write(api, f'/api/access/{oid}/revoke').status_code == 200
    restore_cookies(guest, old_cookies)
    assert verify(guest, invitation['token'], code, key='verify').status_code == 403
    assert guest.get(f'/api/files/{fid}/content').status_code == 403


def test_browser_binding_survives_csrf_refresh_at_real_cookie_path():
    guest = csrf_guest()
    cookie = guest.cookies['b16_recipient_browser']
    original = cookie.value.split(':', 1)[0]
    assert cookie['httponly']
    # Mirror browser path matching: /api/access cookies are not sent to /api/session.
    if not '/api/session/csrf'.startswith(cookie['path']):
        del guest.cookies['b16_recipient_browser']
    guest.get('/api/session/csrf')
    assert guest.cookies['b16_recipient_browser'].value.split(':', 1)[0] == original
