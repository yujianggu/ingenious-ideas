from dataclasses import replace

import pytest

from service_b10.inputs import ImportBatch, PeriodImports
from service_b10.reporting import calculate, calculate_batches, ratio


def _batch(category, rows, *, batch_id=None, currency="CNY", places=2,
           period="2026-09"):
    identity = batch_id or f"{category}-batch"
    return ImportBatch(
        batch_id=identity,
        client="client1",
        task="task1",
        category=category,
        period=period,
        digest=f"{identity}-digest",
        source_file=f"{category}.csv",
        source_file_id=f"{identity}-source",
        currency=currency,
        places=places,
        rows=rows,
        supersedes=None,
    )


def _row(**values):
    return {
        **values,
        "source_file": f"{values.get('shop', 'ads')}.csv",
        "sheet": "Sheet1",
        "row_index": values.pop("row_index", 2),
    }


def test_refund_join_and_missing_ads():
    orders = [
        {"shop": "a", "order": "1", "paid": 100000},
        {"shop": "b", "order": "1", "paid": 200000},
    ]
    refunds = [
        {"id": "r1", "shop": "a", "order": "1", "amount": 10000},
        {"id": "r2", "shop": "a", "order": "1", "amount": 5000},
        {"id": "r3", "shop": "c", "order": "9", "amount": 100},
    ]
    result = calculate(orders, refunds, None)
    assert result["paid_less_refund"] == 285000
    assert result["ads"] is None
    assert result["exceptions"] == ["r3"]
    assert result["metrics"]["ads"]["reason"] == "MISSING_ADS"
    assert "net_profit" not in result
    assert calculate(orders, refunds[:2], 0)["ads"] == 0


def test_refunds_are_preaggregated_per_shop_and_order_for_audit():
    result = calculate(
        [{"shop": "a", "order": "1", "paid": 100000}],
        [
            {"id": "r2", "shop": "a", "order": "1", "amount": 5000},
            {"id": "r1", "shop": "a", "order": "1", "amount": 10000},
        ],
        2000,
    )
    assert result["paid_less_refund"] == 85000
    assert result["refund_audit"] == [
        {
            "shop": "a",
            "order": "1",
            "paid": 100000,
            "refund": 15000,
            "refund_ids": ["r1", "r2"],
        }
    ]


def test_ratio_zero_and_integer_validation():
    assert ratio(1, 0) is None
    assert ratio(1, 4) == "0.25"
    assert ratio(19999999, 20000000) == "1"
    for args in ((True, 1), (1, False), (-1, 1), (1, -1), (1.0, 2)):
        with pytest.raises(ValueError, match="^INVALID_INTEGER$"):
            ratio(*args)

    with pytest.raises(ValueError, match="^INVALID_MONEY$"):
        calculate([{"shop": "a", "order": "1", "paid": True}], [], None)
    with pytest.raises(ValueError, match="^INVALID_MONEY$"):
        calculate([], [], -1)


def test_batch_calculation_has_provenance_and_stable_recomputation():
    orders = _batch("orders", [
        _row(shop="b", order="2", paid=20000, row_index=3),
        _row(shop="a", order="1", paid=10000, row_index=2),
    ])
    refunds = _batch("refunds", [
        _row(id="r2", shop="a", order="1", amount=500, row_index=4),
        _row(id="r1", shop="a", order="1", amount=1000, row_index=2),
        _row(id="rx", shop="x", order="9", amount=50, row_index=5),
    ])
    ads = _batch("ads", [_row(total=2500, row_index=2)])
    imports = PeriodImports(orders, refunds, ads)

    first = calculate_batches(imports)
    second = calculate_batches(imports)
    assert first == second
    assert first["paid_less_refund"] == 28500
    assert first["exceptions"] == ["rx"]
    assert first["refund_audit"][0]["refund_ids"] == ["r1", "r2"]
    metric = first["metrics"]["paid_less_refund"]
    assert metric["value"] == 28500
    assert metric["reason"] is None
    assert metric["rule_version"] == "b10-reporting-v1"
    assert metric["currency"] == "CNY"
    assert metric["places"] == 2
    assert metric["period"] == "2026-09"
    assert [(source["category"], source["row_index"]) for source in metric["sources"]] == [
        ("orders", 2), ("orders", 3), ("refunds", 2), ("refunds", 4)
    ]
    assert all("source_file_id" in source and "batch_id" in source
               for source in metric["sources"])
    assert first["metrics"]["ads"]["sources"][0]["source_file_id"] == "ads-batch-source"
    assert [source["category"] for source in first["refund_audit"][0]["sources"]] == [
        "orders", "refunds", "refunds"
    ]
    assert "net_profit" not in first["metrics"]


def test_batch_adapter_rejects_mixed_units_periods_and_scope():
    base = PeriodImports(
        _batch("orders", [_row(shop="a", order="1", paid=100)]),
        _batch("refunds", []),
        _batch("ads", [_row(total=0)]),
    )
    for changed in (
        replace(base.refunds, currency="USD"),
        replace(base.refunds, places=0),
        replace(base.refunds, period="2026-08"),
        replace(base.refunds, client="other"),
        replace(base.refunds, task="other"),
    ):
        with pytest.raises(ValueError, match="^INCOMPATIBLE_BATCHES$"):
            calculate_batches(PeriodImports(base.orders, changed, base.ads))


def test_same_content_batches_and_frozen_rows_recompute_identically():
    rows = [_row(shop="a", order="1", paid=10000)]
    refunds = [_row(id="r1", shop="a", order="1", amount=1000)]
    first = PeriodImports(
        _batch("orders", rows, batch_id="same-orders"),
        _batch("refunds", refunds, batch_id="same-refunds"),
        None,
    )
    renamed = PeriodImports(
        replace(first.orders, source_file="renamed.csv"),
        replace(first.refunds, source_file="refund-copy.csv"),
        None,
    )
    assert calculate_batches(first) == calculate_batches(renamed)
    snapshot = calculate_batches(first)
    assert calculate(snapshot["orders"], snapshot["refunds"], snapshot["ads"]) == calculate(
        snapshot["orders"], snapshot["refunds"], snapshot["ads"]
    )
