import uuid
import pytest


def command(client, oid, action, revision, **fields):
    key = fields.pop('key', str(uuid.uuid4()))
    return client.post(f'/api/orders/{oid}/commands/{action}', {'expected_revision': revision, **fields}, format='json', HTTP_IDEMPOTENCY_KEY=key)


def create(api):
    response = api.post('/api/orders', {'title': '校样'}, format='json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert response.status_code == 201
    return response.json()


def publish(api, order, fid, **fields):
    response = command(api, order['id'], 'publish', order['revision'], asset_id=fid, recipient=fields.pop('recipient', 'client@example.test'), **fields)
    assert response.status_code == 200, response.content
    return response.json()


def decide(client, published, decision='confirmed', reason='', key=None):
    return client.post(f"/api/requests/{published['request_id']}/decision", {'expected_revision': published['revision'], 'decision': decision, 'reason': reason}, format='json', HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))


@pytest.mark.django_db(transaction=True)
def test_stale_confirmation(api, customer_api, ready_file):
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    second = publish(api, first, ready_file(order['id']))
    response = decide(guest, first)
    assert response.status_code == 409
    current = api.get(f"/api/orders/{order['id']}").json()
    assert current['status'] == 'awaiting_confirmation'
    assert current['current_version_id'] == second['version_id']
    assert len(current['versions']) == 2
    assert guest.get(f"/api/files/{first['asset_id']}/content", HTTP_RANGE='bytes=0-5').status_code == 403
    from proofing.models import ConfirmationEvent
    assert not ConfirmationEvent.objects.filter(decision='confirmed').exists()


@pytest.mark.django_db
def test_return_requires_reason_and_revision_then_preserves_history(api, customer_api, ready_file):
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    assert decide(guest, first, 'returned').status_code == 422
    assert decide(guest, {**first, 'revision': 0}, 'returned', '改字').status_code == 409
    result = decide(guest, first, 'returned', '改字').json()
    assert result['status'] == 'returned' and result['revision'] == first['revision'] + 1
    second = publish(api, result, ready_file(order['id']))
    history = api.get(f"/api/orders/{order['id']}").json()['versions']
    assert history[0]['request']['events'][0]['reason'] == '改字'
    assert history[1]['id'] == second['version_id']


@pytest.mark.django_db
def test_print_review_requires_current_unrevoked_confirmation(api, customer_api, ready_file):
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    assert command(api, order['id'], 'print-review', first['revision']).status_code == 409
    confirmed = decide(customer_api(first['request_id']), first).json()
    reviewed = command(api, order['id'], 'print-review', confirmed['revision'], key='review')
    assert reviewed.status_code == 200
    assert command(api, order['id'], 'print-review', confirmed['revision'], key='review').json() == reviewed.json()
    second = publish(api, reviewed.json(), ready_file(order['id']))
    assert command(api, order['id'], 'print-review', second['revision']).status_code == 409
    from proofing.models import PrintReview, ConfirmationEvent
    assert PrintReview.objects.count() == 1
    assert ConfirmationEvent.objects.filter(decision='confirmed').count() == 1


@pytest.mark.django_db
def test_revoke_records_event_revokes_download_and_blocks_print(api, customer_api, ready_file):
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    confirmed = decide(guest, first).json()
    revoked = command(api, order['id'], 'revoke', confirmed['revision'], reason='客户变更', key='revoke')
    assert revoked.status_code == 200 and revoked.json()['status'] == 'revoked'
    assert command(api, order['id'], 'revoke', confirmed['revision'], reason='客户变更', key='revoke').json() == revoked.json()
    assert guest.get(f"/api/files/{first['asset_id']}/content").status_code == 403
    assert command(api, order['id'], 'print-review', revoked.json()['revision']).status_code == 409
    from proofing.models import ConfirmationEvent
    assert list(ConfirmationEvent.objects.order_by('created_at').values_list('decision', flat=True)) == ['confirmed', 'revoked']


@pytest.mark.django_db
def test_publish_replay_conflict_and_failed_transaction_keep_current_pointer(api, customer_api, ready_file, monkeypatch):
    order = create(api)
    fid = ready_file(order['id'])
    first = publish(api, order, fid, key='first')
    guest = customer_api(first['request_id'])
    assert publish(api, order, fid, key='first') == first
    assert command(api, order['id'], 'publish', order['revision'], asset_id=fid, recipient='different@example.test', key='first').status_code == 409
    from proofing.models import ProofVersion, ConfirmationRequest
    from identity.models import Invitation, ExternalSession
    from proofing.models import Order
    original = Order.save
    def fail_pointer(self, *args, **kwargs):
        if 'current_version' in (kwargs.get('update_fields') or []):
            raise RuntimeError('injected pointer persistence failure')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Order, 'save', fail_pointer)
    with pytest.raises(RuntimeError):
        command(api, order['id'], 'publish', first['revision'], asset_id=fid, recipient='client@example.test', key='retry-publish')
    monkeypatch.setattr(Order, 'save', original)
    reread = api.get(f"/api/orders/{order['id']}").json()
    assert reread['current_version_id'] == first['version_id'] and reread['revision'] == first['revision']
    assert ProofVersion.objects.count() == ConfirmationRequest.objects.count() == Invitation.objects.count() == 1
    assert ExternalSession.objects.get().revoked_at is None
    assert Invitation.objects.get().revoked_at is None
    assert ConfirmationRequest.objects.get().superseded_at is None
    assert reread['versions'][0]['request']['events'] == []
    assert guest.get(f'/api/files/{fid}/content').status_code == 200
    assert command(api, order['id'], 'publish', first['revision'], asset_id=fid, recipient='client@example.test', key='retry-publish').status_code == 200
