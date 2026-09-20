from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from .inputs import ImportBatch, PeriodImports


RULE_VERSION = "b10-reporting-v1"


def ratio(numerator: int, denominator: int) -> str | None:
    """Return a deterministic decimal ratio, or None for a zero denominator."""
    _nonnegative_integer(numerator, "INVALID_INTEGER")
    _nonnegative_integer(denominator, "INVALID_INTEGER")
    if denominator == 0:
        return None
    whole, remainder = divmod(numerator, denominator)
    if remainder == 0:
        return str(whole)

    digits: list[str] = []
    for _ in range(6):
        remainder *= 10
        digit, remainder = divmod(remainder, denominator)
        digits.append(str(digit))
        if remainder == 0:
            break
    if remainder:
        next_digit = (remainder * 10) // denominator
        if next_digit >= 5:
            rounded = int("".join(digits)) + 1
            width = len(digits)
            if rounded >= 10**width:
                whole += 1
                digits = ["0"] * width
            else:
                digits = list(str(rounded).zfill(width))
    fraction = "".join(digits).rstrip("0")
    return str(whole) if not fraction else f"{whole}.{fraction}"


def calculate(
    orders: list[dict[str, Any]],
    refunds: list[dict[str, Any]],
    ads: int | None,
) -> dict[str, Any]:
    """Calculate normalized integer-money metrics without inventing missing facts."""
    if not isinstance(orders, list) or not isinstance(refunds, list):
        raise ValueError("INVALID_INPUT")
    validated_orders = [_order(row) for row in orders]
    validated_refunds = [_refund(row) for row in refunds]
    if ads is not None:
        _nonnegative_integer(ads, "INVALID_MONEY")

    keyed: dict[tuple[str, str], dict[str, Any]] = {}
    for order in validated_orders:
        key = (order["shop"], order["order"])
        if key in keyed:
            raise ValueError("DUPLICATE_ORDER")
        keyed[key] = order
    refund_ids: set[str] = set()
    for refund in validated_refunds:
        if refund["id"] in refund_ids:
            raise ValueError("DUPLICATE_REFUND")
        refund_ids.add(refund["id"])

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unmatched: list[dict[str, Any]] = []
    for refund in validated_refunds:
        key = (refund["shop"], refund["order"])
        (grouped[key] if key in keyed else unmatched).append(refund)

    audit: list[dict[str, Any]] = []
    matched_refunds: list[dict[str, Any]] = []
    for key in sorted(keyed):
        order = keyed[key]
        joined = sorted(grouped.get(key, []), key=lambda row: row["id"])
        matched_refunds.extend(joined)
        audit.append(
            {
                "shop": key[0],
                "order": key[1],
                "paid": order["paid"],
                "refund": sum(row["amount"] for row in joined),
                "refund_ids": [row["id"] for row in joined],
            }
        )

    paid_less_refund = sum(row["paid"] for row in validated_orders) - sum(
        row["amount"] for row in matched_refunds
    )
    money_sources = _sources("orders", validated_orders) + _sources(
        "refunds", matched_refunds
    )
    result = {
        "paid_less_refund": paid_less_refund,
        "ads": ads,
        "exceptions": sorted(row["id"] for row in unmatched),
        "exception_details": [
            {
                "code": "UNMATCHED_REFUND",
                "id": row["id"],
                "shop": row["shop"],
                "order": row["order"],
                "amount": row["amount"],
                "sources": _sources("refunds", [row]),
            }
            for row in sorted(unmatched, key=lambda value: value["id"])
        ],
        "refund_audit": audit,
        "metrics": {
            "paid_less_refund": _metric(paid_less_refund, money_sources),
            "ads": _metric(
                ads,
                [],
                reason="MISSING_ADS" if ads is None else None,
            ),
        },
    }
    return result


def calculate_batches(imports: PeriodImports) -> dict[str, Any]:
    """Validate compatible Task 2 batches and return a frozen audit snapshot."""
    if not isinstance(imports, PeriodImports):
        raise ValueError("INVALID_INPUT")
    batches = [imports.orders, imports.refunds]
    if imports.ads is not None:
        batches.append(imports.ads)
    _compatible_batches(batches)
    if imports.orders.category != "orders" or imports.refunds.category != "refunds":
        raise ValueError("INVALID_BATCH_CATEGORY")
    if imports.ads is not None and imports.ads.category != "ads":
        raise ValueError("INVALID_BATCH_CATEGORY")

    order_rows = _stable_rows(imports.orders.rows, ("shop", "order"))
    refund_rows = _stable_rows(imports.refunds.rows, ("id",))
    ad_rows = [] if imports.ads is None else _stable_rows(
        imports.ads.rows, ("source_file", "sheet", "row_index")
    )
    ad_total = None
    if imports.ads is not None:
        ad_total = 0
        for row in ad_rows:
            if not isinstance(row, dict) or "total" not in row:
                raise ValueError("INVALID_INPUT")
            _nonnegative_integer(row["total"], "INVALID_MONEY")
            ad_total += row["total"]

    result = calculate(order_rows, refund_rows, ad_total)
    currency = imports.orders.currency
    places = imports.orders.places
    period = imports.orders.period
    matched_ids = {
        refund_id
        for detail in result["refund_audit"]
        for refund_id in detail["refund_ids"]
    }
    matched_refunds = [row for row in refund_rows if row["id"] in matched_ids]
    result["metrics"]["paid_less_refund"].update(
        currency=currency,
        places=places,
        period=period,
        sources=(
            _sources("orders", order_rows, imports.orders)
            + _sources("refunds", matched_refunds, imports.refunds)
        ),
    )
    result["metrics"]["ads"].update(
        currency=currency,
        places=places,
        period=period,
        sources=_sources("ads", ad_rows, imports.ads),
    )
    order_by_key = {(row["shop"], row["order"]): row for row in order_rows}
    refunds_by_id = {row["id"]: row for row in refund_rows}
    for detail in result["refund_audit"]:
        detail["sources"] = (
            _sources(
                "orders",
                [order_by_key[(detail["shop"], detail["order"])]],
                imports.orders,
            )
            + _sources(
                "refunds",
                [refunds_by_id[refund_id] for refund_id in detail["refund_ids"]],
                imports.refunds,
            )
        )
    for detail in result["exception_details"]:
        detail["sources"] = _sources(
            "refunds", [refunds_by_id[detail["id"]]], imports.refunds
        )
    result.update(
        {
            "rule_version": RULE_VERSION,
            "currency": currency,
            "places": places,
            "period": period,
            "batch_ids": {
                "orders": imports.orders.batch_id,
                "refunds": imports.refunds.batch_id,
                "ads": None if imports.ads is None else imports.ads.batch_id,
            },
            "orders": order_rows,
            "refunds": refund_rows,
        }
    )
    return result


def _metric(value: int | None, sources: list[dict[str, Any]], *, reason=None):
    return {
        "value": value,
        "reason": reason,
        "rule_version": RULE_VERSION,
        "currency": None,
        "places": None,
        "period": None,
        "sources": sorted(sources, key=_source_key),
    }


def _sources(
    category: str,
    rows: list[dict[str, Any]],
    batch: ImportBatch | None = None,
) -> list[dict[str, Any]]:
    sources = []
    for row in rows:
        if not all(field in row for field in ("source_file", "sheet", "row_index")):
            continue
        source = {
            "category": category,
            "source_file": row["source_file"],
            "sheet": row["sheet"],
            "row_index": row["row_index"],
        }
        if batch is not None:
            source["source_file_id"] = batch.source_file_id
            source["batch_id"] = batch.batch_id
        sources.append(source)
    return sorted(sources, key=_source_key)


def _source_key(source: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(source["category"]),
        str(source["source_file"]),
        str(source["sheet"]),
        int(source["row_index"]),
    )


def _order(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("INVALID_INPUT")
    shop = _identifier(row.get("shop"))
    order = _identifier(row.get("order"))
    if shop is None or order is None:
        raise ValueError("INVALID_INPUT")
    if "paid" not in row:
        raise ValueError("INVALID_INPUT")
    _nonnegative_integer(row["paid"], "INVALID_MONEY")
    return row


def _refund(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("INVALID_INPUT")
    if any(_identifier(row.get(field)) is None for field in ("id", "shop", "order")):
        raise ValueError("INVALID_INPUT")
    if "amount" not in row:
        raise ValueError("INVALID_INPUT")
    _nonnegative_integer(row["amount"], "INVALID_MONEY")
    return row


def _identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _nonnegative_integer(value: Any, code: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(code)


def _compatible_batches(batches: list[ImportBatch]) -> None:
    if not all(isinstance(batch, ImportBatch) for batch in batches):
        raise ValueError("INVALID_INPUT")
    common = {
        (batch.client, batch.task, batch.currency, batch.places, batch.period)
        for batch in batches
    }
    if len(common) != 1:
        raise ValueError("INCOMPATIBLE_BATCHES")


def _stable_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]):
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("INVALID_INPUT")
    copied = deepcopy(rows)
    try:
        return sorted(copied, key=lambda row: tuple(str(row[field]) for field in fields))
    except KeyError as error:
        raise ValueError("INVALID_INPUT") from error
