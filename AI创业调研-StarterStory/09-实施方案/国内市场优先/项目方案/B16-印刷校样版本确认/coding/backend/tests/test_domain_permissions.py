import pytest
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APIClient
from identity.models import Membership, User
from conftest import verified_client
from test_stale_confirmation import command, create, publish, decide


@pytest.mark.django_db
@pytest.mark.parametrize('role', ['owner', 'employee', 'designer'])
def test_staff_can_publish_revoke_review_but_never_decide(api, tenant, ready_file, customer_api, role):
    order = create(api)
    user = User.objects.create_user('worker-' + role)
    Membership.objects.create(user=user, tenant=tenant, roles=[role])
    staff = verified_client(user, tenant)
    first = publish(staff, order, ready_file(order['id']))
    assert decide(staff, first).status_code == 403
    assert command(staff, order['id'], 'decide', first['revision'], request_id=first['request_id'], decision='confirmed').status_code == 403
    confirmed = decide(customer_api(first['request_id']), first).json()
    reviewed = command(staff, order['id'], 'print-review', confirmed['revision'])
    assert reviewed.status_code == 200
    assert command(staff, order['id'], 'revoke', reviewed.json()['revision']).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize('role', ['courier', 'customer'])
def test_unprivileged_members_cannot_issue_any_domain_command(api, tenant, ready_file, role):
    order = create(api)
    fid = ready_file(order['id'])
    first = publish(api, order, fid)
    user = User.objects.create_user('limited-' + role)
    Membership.objects.create(user=user, tenant=tenant, roles=[role])
    client = verified_client(user, tenant)
    for action in ['publish', 'revoke', 'print-review']:
        assert command(client, order['id'], action, first['revision'], asset_id=fid, recipient='client@example.test').status_code == 403
    assert decide(client, first).status_code == 403
    current = api.get(f"/api/orders/{order['id']}").json()
    assert current['revision'] == first['revision'] and current['current_version_id'] == first['version_id']


@pytest.mark.django_db
def test_exact_request_recipient_scope_and_external_management_denial(api, other_api, customer_api, ready_file):
    one, two = create(api), create(api)
    first = publish(api, one, ready_file(one['id']))
    second = publish(api, two, ready_file(two['id']), recipient='other@example.test')
    guest = customer_api(first['request_id'])
    assert decide(guest, second).status_code == 403
    for action in ['publish', 'revoke', 'print-review']:
        assert command(guest, one['id'], action, first['revision']).status_code in (401, 403)
        assert command(other_api, one['id'], action, first['revision']).status_code == 403
    assert decide(APIClient(), first).status_code == 403
    assert command(guest, one['id'], 'decide', first['revision'], request_id=second['request_id'], decision='confirmed').status_code == 403
    good = command(guest, one['id'], 'decide', first['revision'], request_id=first['request_id'], decision='confirmed')
    assert good.status_code == 200
    from proofing.models import ConfirmationEvent
    event = ConfirmationEvent.objects.get(decision='confirmed')
    assert event.actor_id is None and event.external_session_id is not None
    assert event.verification_method == 'receiver_code'


@pytest.mark.django_db
def test_expired_request_no_decision_or_print_review(api, customer_api, ready_file):
    from proofing.models import ConfirmationRequest, ConfirmationEvent
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    ConfirmationRequest.objects.filter(pk=first['request_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert decide(guest, first).status_code == 409
    assert command(api, order['id'], 'print-review', first['revision']).status_code == 409
    assert not ConfirmationEvent.objects.exists()
    current = api.get(f"/api/orders/{order['id']}").json()
    assert current['revision'] == first['revision'] and current['current_version_id'] == first['version_id']


@pytest.mark.django_db
def test_ready_file_parent_scope_and_required_command_fields(api, other_api, ready_file):
    from test_files import upload
    order, other = create(api), create(api)
    fid = ready_file(other['id'])
    assert command(api, order['id'], 'publish', order['revision'], asset_id=fid, recipient='client@example.test').status_code == 403
    quarantine = upload(api, order['id']).json()['id']
    assert command(api, order['id'], 'publish', order['revision'], asset_id=quarantine, recipient='client@example.test').status_code == 409
    valid = ready_file(order['id'])
    for revision in [None, True, '1']:
        assert command(api, order['id'], 'publish', revision, asset_id=valid, recipient='client@example.test').status_code == 422
    assert command(api, order['id'], 'publish', order['revision'], asset_id=valid, recipient=' ').status_code == 422
    current = api.get(f"/api/orders/{order['id']}").json()
    assert current['current_version_id'] is None and current['versions'] == []


@pytest.mark.django_db
def test_decision_requires_csrf_and_staff_mfa(api, customer_api, ready_file):
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    guest.credentials()
    assert decide(guest, first).status_code == 403
    session = api.session
    del session['otp_verified_user_id']
    session.save()
    assert command(api, order['id'], 'revoke', first['revision']).status_code == 403


@pytest.mark.django_db
def test_customer_request_read_only_exposes_own_version(api, customer_api, ready_file):
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    response = guest.get(f"/api/requests/{first['request_id']}")
    assert response.status_code == 200
    assert response.json()['revision'] == first['revision']
    assert response.json()['version']['asset_id'] == first['asset_id']
    assert response.json()['recipient'] == 'client@example.test'
    assert api.get(f"/api/requests/{first['request_id']}").status_code == 403


@pytest.mark.django_db
def test_same_file_unbound_invitation_cannot_decide_bound_request(api, customer_api, ready_file, settings):
    from domain_support import TestCodeProvider
    from test_invitations import issue, guest_challenge, verify
    settings.CODE_PROVIDER = 'domain_support.TestCodeProvider'
    order = create(api)
    fid = ready_file(order['id'])
    first = publish(api, order, fid)
    # Same order, file and recipient is still not the exact request capability.
    unbound = issue(api, order['id'], fid).json()
    guest, code = guest_challenge(unbound['token'], TestCodeProvider)
    assert verify(guest, unbound['token'], code).status_code == 200
    assert decide(guest, first).status_code == 403
    current = api.get(f"/api/orders/{order['id']}").json()
    assert current['status'] == 'awaiting_confirmation' and current['revision'] == first['revision']
    assert decide(customer_api(first['request_id']), first).status_code == 200
