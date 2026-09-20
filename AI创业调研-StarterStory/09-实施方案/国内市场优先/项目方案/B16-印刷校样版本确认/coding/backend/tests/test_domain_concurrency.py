from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from django.db import close_old_connections, connection, connections
from rest_framework.test import APIClient
from test_stale_confirmation import command, create, publish, decide


def concurrent(clients_and_calls):
    barrier = Barrier(len(clients_and_calls))
    def run(item):
        client, call = item
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            barrier.wait(timeout=15)  # Before API/execute_once/Order lock, never inside lock.
            result = call(client)
            return pid, result.status_code, result.json()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=len(clients_and_calls)) as pool:
        return list(pool.map(run, clients_and_calls))


def clone(client):
    result = APIClient(enforce_csrf_checks=True)
    result.cookies = client.cookies.copy()
    result.credentials(**client._credentials)
    return result


@pytest.mark.django_db(transaction=True)
def test_twenty_simultaneous_duplicate_decisions_one_event_and_receipt(api, customer_api, ready_file):
    from proofing.models import ConfirmationEvent, Order
    from common.models import CommandReceipt
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    results = concurrent([(clone(guest), lambda c: decide(c, first, key='same-decision')) for _ in range(20)])
    assert len({r[0] for r in results}) == 20
    assert {r[1] for r in results} == {200}
    assert all(r[2] == results[0][2] for r in results)
    assert ConfirmationEvent.objects.filter(decision='confirmed').count() == 1
    assert CommandReceipt.objects.filter(operation='request.decide', key='same-decision').count() == 1
    assert Order.objects.get(pk=order['id']).revision == first['revision'] + 1


@pytest.mark.django_db(transaction=True)
def test_two_employees_publish_one_current_and_losing_ready_asset_survives(api, tenant, ready_file):
    from conftest import verified_client
    from identity.models import Membership, User
    from proofing.models import Order, ProofVersion
    from files.models import FileRecord
    order = create(api)
    assets = [ready_file(order['id']), ready_file(order['id'])]
    clients = []
    for n in range(2):
        user = User.objects.create_user(f'publisher{n}')
        Membership.objects.create(user=user, tenant=tenant, roles=['employee'])
        clients.append(verified_client(user, tenant))
    results = concurrent([(client, lambda c, fid=fid: command(c, order['id'], 'publish', order['revision'], asset_id=fid, recipient='client@example.test')) for client, fid in zip(clients, assets)])
    assert len({r[0] for r in results}) == 2
    assert sorted(r[1] for r in results) == [200, 409]
    current = Order.objects.get(pk=order['id'])
    assert ProofVersion.objects.count() == 1
    assert str(current.current_version_id) == next(r[2]['version_id'] for r in results if r[1] == 200)
    assert FileRecord.objects.filter(id__in=assets, state='ready').count() == 2


@pytest.mark.django_db(transaction=True)
def test_publish_decide_race_never_confirms_new_file(api, customer_api, ready_file):
    from proofing.models import Order, ConfirmationEvent, ConfirmationRequest
    from identity.models import ExternalSession
    order = create(api)
    first = publish(api, order, ready_file(order['id']))
    guest = customer_api(first['request_id'])
    next_asset = ready_file(order['id'])
    results = concurrent([(api, lambda c: command(c, order['id'], 'publish', first['revision'], asset_id=next_asset, recipient='client@example.test')), (clone(guest), lambda c: decide(c, first))])
    assert len({r[0] for r in results}) == 2
    assert sorted(r[1] for r in results) == [200, 409]
    current = Order.objects.get(pk=order['id'])
    if results[0][1] == 200:
        assert current.status == 'awaiting_confirmation'
        assert current.current_version.asset_id.hex == next_asset.replace('-', '')
        assert not ConfirmationEvent.objects.filter(decision='confirmed').exists()
        assert ExternalSession.objects.get().revoked_at is not None
    else:
        assert current.status == 'confirmed' and str(current.current_version_id) == first['version_id']
        assert ConfirmationRequest.objects.count() == 1
        assert ConfirmationEvent.objects.filter(decision='confirmed', request_id=first['request_id']).count() == 1
        assert ExternalSession.objects.get().revoked_at is None
