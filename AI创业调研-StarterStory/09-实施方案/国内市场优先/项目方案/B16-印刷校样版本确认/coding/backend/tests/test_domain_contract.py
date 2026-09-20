import pytest
from test_stale_confirmation import create, command


@pytest.mark.django_db
def test_publish_requires_ready_asset_and_revision(api):
    order = create(api)
    assert command(api, order['id'], 'publish', order['revision']).status_code == 422


@pytest.mark.django_db
def test_immutable_versions_decisions_and_reviews_reject_update_delete(api, ready_file, customer_api):
    from django.db import DatabaseError, transaction
    from proofing.models import ProofVersion, ConfirmationEvent, PrintReview
    from test_stale_confirmation import publish, decide
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    confirmed = decide(customer_api(first['request_id']), first).json()
    assert command(api, order['id'], 'print-review', confirmed['revision']).status_code == 200
    for model, changes in [(ProofVersion, {'sha256': '0' * 64}), (ConfirmationEvent, {'reason': 'changed'}), (PrintReview, {'verification_note': 'changed'})]:
        with pytest.raises(DatabaseError), transaction.atomic():
            model.objects.update(**changes)
        with pytest.raises(DatabaseError), transaction.atomic():
            # QuerySet delete must be blocked at the database, including audit rows.
            with __import__('django.db', fromlist=['connection']).connection.cursor() as cursor:
                cursor.execute('DELETE FROM ' + model._meta.db_table)
        assert model.objects.count() == 1


@pytest.mark.django_db
def test_duplicate_new_key_and_changed_decision_cannot_add_second_result(api, customer_api, ready_file):
    from proofing.models import ConfirmationEvent
    from test_stale_confirmation import publish, decide
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    result = decide(guest, first, key='original')
    assert result.status_code == 200
    assert decide(guest, first, key='original').json() == result.json()
    assert decide(guest, first, 'returned', '改字', key='original').status_code == 409
    assert decide(guest, first, key='different').status_code == 409
    assert ConfirmationEvent.objects.filter(decision='confirmed').count() == 1


@pytest.mark.django_db
def test_revoked_access_disallows_existing_confirmation_print_review(api, customer_api, ready_file):
    from test_stale_confirmation import publish, decide
    from test_invitations import write
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    confirmed = decide(customer_api(first['request_id']), first).json()
    assert write(api, f"/api/access/{order['id']}/revoke").status_code == 200
    assert command(api, order['id'], 'print-review', confirmed['revision']).status_code == 409


@pytest.mark.django_db
@pytest.mark.parametrize('action', ['decide', 'revoke'])
def test_failed_decide_or_revoke_rolls_back_event_revision_and_grants(api, customer_api, ready_file, monkeypatch, action):
    from proofing.models import Order, ConfirmationRequest, ConfirmationEvent
    from identity.models import ExternalSession, Invitation
    from test_stale_confirmation import publish, decide
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    original = Order.save
    def fail_save(self, *args, **kwargs):
        raise RuntimeError('failure after event and grant writes')
    monkeypatch.setattr(Order, 'save', fail_save)
    with pytest.raises(RuntimeError):
        if action == 'decide':
            decide(guest, first, key='retry-after-rollback')
        else:
            command(api, order['id'], 'revoke', first['revision'], key='retry-after-rollback')
    monkeypatch.setattr(Order, 'save', original)
    current = api.get(f"/api/orders/{order['id']}").json()
    assert current['revision'] == first['revision'] and current['current_version_id'] == first['version_id']
    assert current['status'] == 'awaiting_confirmation' and not ConfirmationEvent.objects.exists()
    assert ConfirmationRequest.objects.get().revoked_at is None
    assert Invitation.objects.get().revoked_at is None and ExternalSession.objects.get().revoked_at is None
    assert guest.get(f"/api/files/{first['asset_id']}/content", HTTP_RANGE='bytes=0-5').status_code == 206
    if action == 'decide':
        assert decide(guest, first, key='retry-after-rollback').status_code == 200
    else:
        assert command(api, order['id'], 'revoke', first['revision'], key='retry-after-rollback').status_code == 200
