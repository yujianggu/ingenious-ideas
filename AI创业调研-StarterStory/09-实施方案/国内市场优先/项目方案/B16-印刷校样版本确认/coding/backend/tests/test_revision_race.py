from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections

from config.exceptions import Conflict
from proofing.commands import create_order, update_order
from proofing.models import Order


@pytest.mark.django_db(transaction=True)
def test_same_key_concurrent_create_uses_database_receipt_arbitration(owner, tenant):
    barrier = Barrier(2)

    def worker():
        close_old_connections()
        try:
            barrier.wait()  # synchronize before either transaction acquires a lock
            return create_order(tenant, owner, "concurrent", {"title": "并发草稿"})
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))

    assert results[0] == results[1]
    assert Order.objects.filter(tenant=tenant, title="并发草稿").count() == 1


@pytest.mark.django_db(transaction=True)
def test_competing_revisions_use_real_connections_and_one_becomes_stale(owner, tenant):
    order = Order.objects.create(tenant=tenant, title="原稿", created_by=owner)
    barrier = Barrier(2)

    def worker(number):
        close_old_connections()
        try:
            barrier.wait()  # never wait while holding the order row lock
            try:
                return ("ok", update_order(
                    tenant, owner, str(order.id), f"edit-{number}",
                    {"title": f"版本{number}", "expected_revision": 1},
                ))
            except Conflict as exc:
                return ("conflict", exc.current_revision)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(worker, (1, 2)))

    assert sorted(kind for kind, _ in outcomes) == ["conflict", "ok"]
    order.refresh_from_db()
    assert order.revision == 2
    assert order.title in {"版本1", "版本2"}
