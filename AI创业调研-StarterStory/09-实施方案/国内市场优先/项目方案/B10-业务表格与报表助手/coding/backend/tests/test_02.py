import io
import sqlite3
from decimal import localcontext

import pandas as pd
import pytest

from service_b10.inputs import (
    import_file,
    import_period,
    normalize_rows,
    parse_amount,
    read_rows,
)
from service_b10.store import Store


ORDERS = {
    "columns": {"shop": "店铺", "order": "订单号", "paid": "实收"},
    "required": ["shop", "order", "paid"],
    "money": ["paid"],
    "currency": "CNY",
    "places": 2,
    "encoding": "utf-8",
}
REFUNDS = {
    "columns": {"id": "退款号", "shop": "店铺", "order": "订单号", "amount": "退款"},
    "required": ["id", "shop", "order", "amount"],
    "money": ["amount"],
    "currency": "CNY",
    "places": 2,
    "encoding": "utf-8",
}
ADS = {
    "columns": {"total": "广告费"},
    "required": ["total"],
    "money": ["total"],
    "currency": "CNY",
    "places": 2,
    "encoding": "utf-8",
}


def test_money_and_schema():
    assert parse_amount("1,234.50", thousands=",", places=2) == 123450
    for value in ("NaN", "Infinity", "12,34.50", ""):
        with pytest.raises(ValueError):
            parse_amount(value, thousands=",", places=2)
    schema = {
        "columns": {"order_id": "订单号", "paid": "实收"},
        "required": ["order_id", "paid"],
    }
    with pytest.raises(ValueError, match="SCHEMA_CHANGED"):
        normalize_rows([{"订单号": "a", "金额": "1"}], schema)


@pytest.mark.parametrize(
    ("value", "places", "code"),
    [
        ("1.001", 2, "INVALID_PRECISION"),
        (" 1.00", 2, "INVALID_AMOUNT"),
        ("1.00", -1, "INVALID_CONFIG"),
        ("1.00", True, "INVALID_CONFIG"),
    ],
)
def test_amount_errors_are_exact(value, places, code):
    with pytest.raises(ValueError, match=f"^{code}$"):
        parse_amount(value, thousands=",", places=places)


def test_amount_conversion_is_exact_independent_of_decimal_context():
    assert parse_amount(
        "1234567890123456789012345678.91", thousands=",", places=2
    ) == 123456789012345678901234567891
    with pytest.raises(ValueError, match="^INVALID_PRECISION$"):
        parse_amount(
            "1.000000000000000000000000000001", thousands=",", places=2
        )
    with localcontext() as context:
        context.prec = 4
        assert parse_amount("123.45", thousands=",", places=2) == 12345


def test_normalize_preserves_lineage_and_rejects_ambiguous_schema():
    row = {
        "订单号": "A1",
        "实收": "2.50",
        "source_file": "orders.csv",
        "sheet": "orders",
        "row_index": 2,
    }
    schema = {
        "columns": {"order_id": "订单号", "paid": "实收"},
        "required": ["order_id", "paid"],
    }
    assert normalize_rows([row], schema) == [{
        "order_id": "A1",
        "paid": "2.50",
        "source_file": "orders.csv",
        "sheet": "orders",
        "row_index": 2,
    }]
    with pytest.raises(ValueError, match="^DUPLICATE_COLUMN$"):
        normalize_rows([row], {
            "columns": {"order_id": "订单号", "alias": "订单号"},
            "required": ["order_id"],
        })


def test_real_csv_and_xlsx_are_read_with_lineage_and_integer_money(tmp_path):
    csv_data = "店铺,订单号,实收\n北店,A1,\"1,234.50\"\n".encode()
    rows = read_rows(csv_data, filename="orders.csv", schema=ORDERS)
    assert rows == [{
        "shop": "北店", "order": "A1", "paid": 123450,
        "source_file": "orders.csv", "sheet": "orders.csv", "row_index": 2,
    }]

    buffer = io.BytesIO()
    pd.DataFrame([{"店铺": "南店", "订单号": "A2", "实收": "0"}]).to_excel(
        buffer, index=False, sheet_name="订单"
    )
    xlsx_schema = {**ORDERS, "sheet": "订单"}
    assert read_rows(buffer.getvalue(), filename="orders.xlsx", schema=xlsx_schema)[0] == {
        "shop": "南店", "order": "A2", "paid": 0,
        "source_file": "orders.xlsx", "sheet": "订单", "row_index": 2,
    }


def test_csv_lineage_uses_physical_record_start_with_blanks_and_multiline_quotes():
    csv_data = (
        '店铺,订单号,实收\n'
        '"北\n店",A1,1.00\n'
        '\n'
        '南店,A2,2.00\n'
    ).encode()
    rows = read_rows(csv_data, filename="orders.csv", schema=ORDERS)
    assert [(row["order"], row["row_index"]) for row in rows] == [
        ("A1", 2),
        ("A2", 5),
    ]


def test_reader_rejects_duplicate_headers_empty_order_and_missing_protocol_config():
    duplicate = "店铺,订单号,订单号,实收\n北店,A1,A1,1.00\n".encode()
    with pytest.raises(ValueError, match="^DUPLICATE_COLUMN$"):
        read_rows(duplicate, filename="orders.csv", schema=ORDERS)
    with pytest.raises(ValueError, match="^SCHEMA_CHANGED$"):
        read_rows("店铺,订单号,金额\n".encode(), filename="orders.csv", schema=ORDERS)
    empty_order = "店铺,订单号,实收\n北店,,1.00\n".encode()
    with pytest.raises(ValueError, match="^EMPTY_ORDER_ID$"):
        read_rows(empty_order, filename="orders.csv", schema=ORDERS)
    for key in ("currency", "places"):
        invalid = dict(ORDERS)
        invalid.pop(key)
        with pytest.raises(ValueError, match="^INVALID_CONFIG$"):
            read_rows("店铺,订单号,实收\n北店,A1,1.00\n".encode(), filename="orders.csv", schema=invalid)
    malformed = {**ORDERS, "currency": "yuan"}
    with pytest.raises(ValueError, match="^INVALID_CONFIG$"):
        read_rows("店铺,订单号,实收\n北店,A1,1.00\n".encode(),
                  filename="orders.csv", schema=malformed)


def test_batch_is_content_idempotent_and_replacement_is_explicit(tmp_path):
    store = Store(tmp_path)
    store.create("client1", "task1")
    first_data = "店铺,订单号,实收\n北店,A1,1.00\n".encode()
    first = import_file(store, "client1", "task1", "orders", "2026-08",
                        "first.csv", first_data, ORDERS)
    renamed = import_file(store, "client1", "task1", "orders", "2026-08",
                          "renamed.csv", first_data, ORDERS)
    assert renamed.batch_id == first.batch_id
    assert renamed.rows == first.rows

    changed = "店铺,订单号,实收\n北店,A1,2.00\n".encode()
    with pytest.raises(ValueError, match="^REPLACEMENT_REQUIRED$"):
        import_file(store, "client1", "task1", "orders", "2026-08",
                    "changed.csv", changed, ORDERS)
    second = import_file(store, "client1", "task1", "orders", "2026-08",
                         "changed.csv", changed, ORDERS, supersedes=first.batch_id)
    assert second.supersedes == first.batch_id
    assert second.rows[0]["paid"] == 200
    with pytest.raises(ValueError, match="^INVALID_SUPERSEDES$"):
        import_file(store, "client1", "task1", "orders", "2026-08",
                    "first.csv", first_data, ORDERS, supersedes=second.batch_id)


def test_import_boundary_requires_canonical_category_schema(tmp_path):
    store = Store(tmp_path)
    store.create("client1", "task1")
    noncanonical = {
        **ORDERS,
        "columns": {"order_id": "订单号", "paid": "实收"},
        "required": ["order_id", "paid"],
    }
    with pytest.raises(ValueError, match="^INVALID_CONFIG$"):
        import_file(
            store, "client1", "task1", "orders", "2026-08", "orders.csv",
            "订单号,实收\nA1,1.00\n".encode(), noncanonical,
        )


def test_supersedes_cannot_cross_client_scope_and_missing_ads_differs_from_zero(tmp_path):
    store = Store(tmp_path)
    store.create("a", "t")
    store.create("b", "t")
    orders = "店铺,订单号,实收\n北店,A1,1.00\n".encode()
    refunds = "退款号,店铺,订单号,退款\nR1,北店,A1,0.00\n".encode()
    other = import_file(store, "a", "t", "orders", "2026-08", "o.csv", orders, ORDERS)
    with pytest.raises(ValueError, match="^INVALID_SUPERSEDES$"):
        import_file(store, "b", "t", "orders", "2026-08", "o.csv", orders,
                    ORDERS, supersedes=other.batch_id)

    missing = import_period(
        store, "b", "t", "2026-09",
        orders=("o.csv", orders, ORDERS),
        refunds=("r.csv", refunds, REFUNDS),
    )
    assert missing.ads is None
    zero = import_period(
        store, "b", "t", "2026-10",
        orders=("o.csv", orders, ORDERS),
        refunds=("r.csv", refunds, REFUNDS),
        ads=("a.csv", "广告费\n0.00\n".encode(), ADS),
    )
    assert zero.ads is not None
    assert zero.ads.rows[0]["total"] == 0


def test_chinese_source_names_use_safe_blob_labels_and_remain_idempotent(tmp_path):
    store = Store(tmp_path)
    store.create("a", "t")
    data = "店铺,订单号,实收\n北店,A1,1.00\n".encode()
    first = import_file(
        store, "a", "t", "orders", "2026-08", "八月订单.csv", data, ORDERS
    )
    renamed = import_file(
        store, "a", "t", "orders", "2026-08", "订单副本.csv", data, ORDERS
    )
    assert renamed.batch_id == first.batch_id
    assert first.source_file == "八月订单.csv"
    assert first.source_file_id == first.digest
    assert first.rows[0]["source_file"] == "八月订单.csv"
    with sqlite3.connect(store.db_path) as connection:
        stored_name = connection.execute(
            "SELECT name FROM blobs WHERE client='a' AND task='t' AND digest=?",
            (first.digest,),
        ).fetchone()[0]
    assert stored_name == f"source-{first.digest}.csv"

    buffer = io.BytesIO()
    pd.DataFrame([{"店铺": "南店", "订单号": "A2", "实收": "2.00"}]).to_excel(
        buffer, index=False, sheet_name="订单"
    )
    imported_xlsx = import_file(
        store, "a", "t", "orders", "2026-09", "九月订单.xlsx",
        buffer.getvalue(), {**ORDERS, "sheet": "订单"},
    )
    assert imported_xlsx.source_file == "九月订单.xlsx"
    assert imported_xlsx.rows[0]["source_file"] == "九月订单.xlsx"
    with pytest.raises(ValueError, match="^INVALID_SOURCE_NAME$"):
        import_file(
            store, "a", "t", "orders", "2026-10", "/tmp/订单.csv", data, ORDERS
        )
