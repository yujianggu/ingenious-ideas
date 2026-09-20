from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from .store import Store


_PERIOD = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
_CATEGORIES = {"orders", "refunds", "ads"}
_CANONICAL = {
    "orders": ({"shop", "order", "paid"}, {"paid"}),
    "refunds": ({"id", "shop", "order", "amount"}, {"amount"}),
    "ads": ({"total"}, {"total"}),
}
_LINEAGE = ("source_file", "sheet", "row_index")


@dataclass(frozen=True)
class ImportBatch:
    batch_id: str
    client: str
    task: str
    category: str
    period: str
    digest: str
    source_file: str
    source_file_id: str
    currency: str
    places: int
    rows: list[dict[str, Any]]
    supersedes: str | None


@dataclass(frozen=True)
class PeriodImports:
    orders: ImportBatch
    refunds: ImportBatch
    ads: ImportBatch | None


def parse_amount(text: str, *, thousands: str, places: int) -> int:
    if (
        not isinstance(text, str)
        or not isinstance(thousands, str)
        or not thousands
        or thousands == "."
        or not isinstance(places, int)
        or isinstance(places, bool)
        or places < 0
    ):
        raise ValueError("INVALID_CONFIG")
    grouping = re.escape(thousands)
    pattern = rf"-?(?:[0-9]+|[0-9]{{1,3}}(?:{grouping}[0-9]{{3}})+)(?:\.[0-9]+)?"
    if not re.fullmatch(pattern, text):
        raise ValueError("INVALID_AMOUNT")
    value = Decimal(text.replace(thousands, ""))
    sign, digits, exponent = value.as_tuple()
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    shift = exponent + places
    if shift >= 0:
        scaled = coefficient * (10**shift)
    else:
        scaled, remainder = divmod(coefficient, 10 ** (-shift))
        if remainder:
            raise ValueError("INVALID_PRECISION")
    if not value.is_finite():
        raise ValueError("INVALID_PRECISION")
    return -scaled if sign else scaled


def normalize_rows(
    rows: list[dict[str, Any]], schema: dict[str, Any]
) -> list[dict[str, Any]]:
    columns, required = _schema_mapping(schema)
    expected_headers = set(columns.values())
    normalized: list[dict[str, Any]] = []
    for row in rows:
        headers = set(row) - set(_LINEAGE)
        if headers != expected_headers:
            raise ValueError("SCHEMA_CHANGED")
        mapped = {standard: row[source] for standard, source in columns.items()}
        if any(_blank(mapped[name]) for name in required):
            if "order" in required and _blank(mapped.get("order")):
                raise ValueError("EMPTY_ORDER_ID")
            if "order_id" in required and _blank(mapped.get("order_id")):
                raise ValueError("EMPTY_ORDER_ID")
            raise ValueError("MISSING_REQUIRED")
        for field in _LINEAGE:
            if field in row:
                mapped[field] = row[field]
        normalized.append(mapped)
    return normalized


def read_rows(
    data: bytes, *, filename: str, schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Read configured tabular bytes and attach auditable source positions.

    CSV row_index is the one-based physical line where a logical record
    starts. A quoted multiline record therefore consumes every physical line
    through its closing quote, and later blank records do not collapse the
    following position. XLSX row_index is the one-based worksheet row.
    """
    currency, places = _protocol(schema)
    del currency
    suffix = Path(filename).suffix.lower()
    row_positions: list[int] | None = None
    if suffix == ".csv":
        encoding = schema.get("encoding")
        if not isinstance(encoding, str) or not encoding:
            raise ValueError("INVALID_CONFIG")
        decoded = data.decode(encoding)
        csv_reader = csv.reader(io.StringIO(decoded, newline=""))
        row_positions = []
        previous_end = 0
        for _ in csv_reader:
            row_positions.append(previous_end + 1)
            previous_end = csv_reader.line_num
        frame = pd.read_csv(
            io.BytesIO(data), header=None, dtype=object,
            keep_default_na=False, encoding=encoding, skip_blank_lines=False,
        )
        sheet = filename
    elif suffix == ".xlsx":
        sheet = schema.get("sheet", 0)
        if not isinstance(sheet, (str, int)) or isinstance(sheet, bool):
            raise ValueError("INVALID_CONFIG")
        frame = pd.read_excel(
            io.BytesIO(data), sheet_name=sheet, header=None, dtype=object,
            keep_default_na=False, engine="openpyxl",
        )
        if isinstance(sheet, int):
            with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as workbook:
                try:
                    sheet = workbook.sheet_names[sheet]
                except IndexError as error:
                    raise ValueError("INVALID_SHEET") from error
    else:
        raise ValueError("UNSUPPORTED_FILE")
    if frame.empty:
        raise ValueError("SCHEMA_CHANGED")
    headers = [_cell_text(value) for value in frame.iloc[0].tolist()]
    if len(headers) != len(set(headers)):
        raise ValueError("DUPLICATE_COLUMN")
    columns, _ = _schema_mapping(schema)
    if set(headers) != set(columns.values()):
        raise ValueError("SCHEMA_CHANGED")
    raw_rows: list[dict[str, Any]] = []
    positions = (
        row_positions[1:]
        if row_positions is not None
        else range(2, len(frame.index) + 1)
    )
    for offset, values in zip(
        positions, frame.iloc[1:].itertuples(index=False, name=None)
    ):
        if all(_blank(value) for value in values):
            continue
        raw = {header: _cell_text(value) for header, value in zip(headers, values)}
        raw.update(source_file=filename, sheet=sheet, row_index=offset)
        raw_rows.append(raw)
    result = normalize_rows(raw_rows, schema)
    thousands = schema.get("thousands", ",")
    for row in result:
        for field in schema.get("money", []):
            if field not in row:
                raise ValueError("INVALID_CONFIG")
            row[field] = parse_amount(
                row[field], thousands=thousands, places=places
            )
    return result


def import_file(
    store: Store,
    client: str,
    task: str,
    category: str,
    period: str,
    filename: str,
    data: bytes,
    schema: dict[str, Any],
    *,
    supersedes: str | None = None,
) -> ImportBatch:
    if category not in _CATEGORIES or not _PERIOD.fullmatch(period):
        raise ValueError("INVALID_CONFIG")
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
    ):
        raise ValueError("INVALID_SOURCE_NAME")
    currency, places = _protocol(schema)
    columns, required = _schema_mapping(schema)
    expected_fields, expected_money = _CANONICAL[category]
    if (
        set(columns) != expected_fields
        or set(required) != expected_fields
        or set(schema["money"]) != expected_money
    ):
        raise ValueError("INVALID_CONFIG")
    digest = hashlib.sha256(data).hexdigest()
    internal_label = f"source-{digest}{Path(filename).suffix.lower()}"
    if store.put(client, task, internal_label, data) != digest:
        raise ValueError("CORRUPT_BLOB")
    rows = read_rows(data, filename=filename, schema=schema)
    batch_id = hashlib.sha256(
        "\0".join((client, task, category, period, digest)).encode()
    ).hexdigest()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))

    # Store.put owns its lock. Registration starts only after it returns so this
    # non-reentrant process lock is never nested.
    with store.job_lock():
        with sqlite3.connect(store.db_path) as connection:
            _ensure_batch_schema(connection)
            active = connection.execute(
                """
                SELECT batch_id FROM input_batches
                WHERE client=? AND task=? AND category=? AND period=?
                  AND superseded_by IS NULL
                """,
                (client, task, category, period),
            ).fetchone()
            if active is not None and active[0] == batch_id:
                return _load_batch(connection, batch_id)
            if active is None:
                if supersedes is not None:
                    raise ValueError("INVALID_SUPERSEDES")
            elif supersedes is None:
                raise ValueError("REPLACEMENT_REQUIRED")
            elif supersedes != active[0]:
                raise ValueError("INVALID_SUPERSEDES")
            if connection.execute(
                "SELECT 1 FROM input_batches WHERE batch_id=?", (batch_id,)
            ).fetchone():
                raise ValueError("INVALID_SUPERSEDES")
            if supersedes is not None:
                changed = connection.execute(
                    """
                    UPDATE input_batches SET superseded_by=?
                    WHERE batch_id=? AND client=? AND task=? AND category=?
                      AND period=? AND superseded_by IS NULL
                    """,
                    (batch_id, supersedes, client, task, category, period),
                ).rowcount
                if changed != 1:
                    raise ValueError("INVALID_SUPERSEDES")
            connection.execute(
                """
                INSERT INTO input_batches(
                    batch_id, client, task, category, period, digest,
                    source_file, source_file_id, currency, places,
                    rows_json, supersedes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id, client, task, category, period, digest,
                    filename, digest, currency, places, payload, supersedes,
                ),
            )
            return _load_batch(connection, batch_id)


def import_period(
    store: Store,
    client: str,
    task: str,
    period: str,
    *,
    orders: tuple[str, bytes, dict[str, Any]],
    refunds: tuple[str, bytes, dict[str, Any]],
    ads: tuple[str, bytes, dict[str, Any]] | None = None,
    supersedes: dict[str, str] | None = None,
) -> PeriodImports:
    replacements = supersedes or {}
    order_batch = import_file(
        store, client, task, "orders", period, *orders,
        supersedes=replacements.get("orders"),
    )
    refund_batch = import_file(
        store, client, task, "refunds", period, *refunds,
        supersedes=replacements.get("refunds"),
    )
    ad_batch = None
    if ads is not None:
        ad_batch = import_file(
            store, client, task, "ads", period, *ads,
            supersedes=replacements.get("ads"),
        )
    return PeriodImports(order_batch, refund_batch, ad_batch)


def get_active_import(
    store: Store, client: str, task: str, category: str, period: str
) -> ImportBatch | None:
    if category not in _CATEGORIES or not _PERIOD.fullmatch(period):
        raise ValueError("INVALID_CONFIG")
    # Schema initialization is a write even on this read-facing API, so it
    # participates in the same non-reentrant lock as imports and backups.
    with store.job_lock():
        with sqlite3.connect(store.db_path) as connection:
            _ensure_batch_schema(connection)
            found = connection.execute(
                """
                SELECT batch_id FROM input_batches
                WHERE client=? AND task=? AND category=? AND period=?
                  AND superseded_by IS NULL
                """,
                (client, task, category, period),
            ).fetchone()
            return None if found is None else _load_batch(connection, found[0])


def _schema_mapping(
    schema: dict[str, Any],
) -> tuple[dict[str, str], list[str]]:
    columns = schema.get("columns")
    required = schema.get("required")
    if (
        not isinstance(columns, dict)
        or not columns
        or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in columns.items())
        or not isinstance(required, list)
        or not all(isinstance(value, str) and value in columns for value in required)
    ):
        raise ValueError("INVALID_CONFIG")
    if len(columns.values()) != len(set(columns.values())):
        raise ValueError("DUPLICATE_COLUMN")
    return columns, required


def _protocol(schema: dict[str, Any]) -> tuple[str, int]:
    _schema_mapping(schema)
    currency = schema.get("currency")
    places = schema.get("places")
    money = schema.get("money")
    if (
        not isinstance(currency, str)
        or re.fullmatch(r"[A-Z]{3}", currency) is None
        or not isinstance(places, int)
        or isinstance(places, bool)
        or places < 0
        or not isinstance(money, list)
        or not all(isinstance(field, str) and field in schema["columns"] for field in money)
        or len(money) != len(set(money))
    ):
        raise ValueError("INVALID_CONFIG")
    return currency, places


def _ensure_batch_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS input_batches (
            batch_id TEXT PRIMARY KEY,
            client TEXT NOT NULL,
            task TEXT NOT NULL,
            category TEXT NOT NULL,
            period TEXT NOT NULL,
            digest TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_file_id TEXT NOT NULL,
            currency TEXT NOT NULL,
            places INTEGER NOT NULL,
            rows_json TEXT NOT NULL,
            supersedes TEXT,
            superseded_by TEXT,
            UNIQUE(client, task, category, period, digest),
            FOREIGN KEY(client, task) REFERENCES tasks(client, task)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_input
        ON input_batches(client, task, category, period)
        WHERE superseded_by IS NULL;
        """
    )


def _load_batch(connection: sqlite3.Connection, batch_id: str) -> ImportBatch:
    row = connection.execute(
        """
        SELECT batch_id, client, task, category, period, digest,
               source_file, source_file_id, currency, places,
               rows_json, supersedes
        FROM input_batches WHERE batch_id=?
        """,
        (batch_id,),
    ).fetchone()
    if row is None:
        raise ValueError("NOT_FOUND")
    return ImportBatch(*row[:10], json.loads(row[10]), row[11])


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _cell_text(value: Any) -> str:
    if _blank(value):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
