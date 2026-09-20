from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from typing import Any

from .reporting import RULE_VERSION, calculate
from .store import Store, blob_digest, checked_child


TRANSITIONS = {
    ("draft", "submit"): "submitted",
    ("submitted", "review"): "reviewed",
    ("reviewed", "deliver"): "delivered",
    ("delivered", "confirm"): "accepted",
    ("delivered", "return"): "returned",
    ("returned", "resubmit"): "submitted",
    ("accepted", "amend"): "draft",
    ("delivered", "amend"): "draft",
    ("returned", "amend"): "draft",
}


def transition(state: str, action: str) -> str:
    try:
        return TRANSITIONS[(state, action)]
    except KeyError:
        raise ValueError("INVALID_TRANSITION") from None


def stage_revision(
    store: Store,
    client: str,
    task: str,
    input_snapshot: dict[str, Any],
    report_snapshot: dict[str, Any],
    *,
    expected_revision: int | None = None,
) -> int:
    """Create or replace the current draft; submitted JSON is never updated."""
    inputs_json, report_json = _snapshot_json(input_snapshot, report_snapshot)
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            return _stage_in_transaction(
                connection, client, task, inputs_json, report_json,
                expected_revision=expected_revision,
            )


def stage_and_submit_revision(
    store: Store,
    client: str,
    task: str,
    input_snapshot: dict[str, Any],
    report_snapshot: dict[str, Any],
    actor: dict[str, Any],
    reason: str,
    *,
    expected_revision: int | None = None,
) -> int:
    """Save the production snapshot and submit event in one transaction.

    The single shared lock covers both changes and the commit. Any event or
    commit failure rolls back a new revision or restores the previous draft.
    """
    inputs_json, report_json = _snapshot_json(input_snapshot, report_snapshot)
    _validate_event_reason("submit", reason)
    with store.job_lock():
        with store._connect() as connection:
            # executescript may commit, so schema initialization must precede
            # BEGIN and must never run inside either transaction helper.
            _schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            revision = _stage_in_transaction(
                connection, client, task, inputs_json, report_json,
                expected_revision=expected_revision,
            )
            _event_in_transaction(
                store, connection, client, task, revision, "submit", actor, reason,
                expected_state="draft",
            )
            return revision


def _snapshot_json(input_snapshot, report_snapshot):
    inputs_json = _canonical_object(input_snapshot, "INVALID_INPUT_SNAPSHOT")
    report_json = _canonical_object(report_snapshot, "INVALID_REPORT_SNAPSHOT")
    _validate_snapshots(json.loads(inputs_json), json.loads(report_json))
    return inputs_json, report_json


def _stage_in_transaction(
    connection, client, task, inputs_json, report_json, *, expected_revision
):
    """Caller owns the lock, schema initialization and transaction boundary."""
    _task_exists(connection, client, task)
    row = _current(connection, client, task)
    if row is None:
        if expected_revision not in (None, 0):
            raise ValueError("STALE_VERSION")
        revision = 1
        connection.execute(
            """
            INSERT INTO workflow_revisions
                (client, task, revision, state, input_json, report_json)
            VALUES (?, ?, 1, 'draft', ?, ?)
            """,
            (client, task, inputs_json, report_json),
        )
        return revision
    revision, state = int(row[0]), str(row[1])
    if expected_revision is not None and expected_revision != revision:
        raise ValueError("STALE_VERSION")
    if state != "draft":
        raise ValueError("SNAPSHOT_FROZEN")
    connection.execute(
        """
        UPDATE workflow_revisions SET input_json = ?, report_json = ?
        WHERE client = ? AND task = ? AND revision = ?
        """,
        (inputs_json, report_json, client, task, revision),
    )
    return revision


def freeze_metric_rules(
    store: Store,
    client: str,
    task: str,
    revision: int,
    rules: dict[str, Any],
    actor: dict[str, Any],
    *,
    expected_state: str | None = "reviewed",
) -> None:
    rules_json = _canonical_object(rules, "INVALID_METRIC_RULES")
    if not rules:
        raise ValueError("INVALID_METRIC_RULES")
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            current = _require_current(connection, client, task, revision)
            _require_reviewed_mutable(current)
            if expected_state is not None and current[1] != expected_state:
                raise ValueError("STALE_STATE")
            if current[4] is not None:
                raise ValueError("METRIC_RULES_ALREADY_FROZEN")
            actor_json, evidence = _validated_actor(store, connection, client, task, actor)
            connection.execute(
                """
                UPDATE workflow_revisions
                SET metric_rules_json = ?, metric_rules_actor_json = ?,
                    metric_rules_evidence = ?
                WHERE client = ? AND task = ? AND revision = ?
                """,
                (rules_json, actor_json, evidence, client, task, revision),
            )


def resolve_exception(
    store: Store,
    client: str,
    task: str,
    revision: int,
    refund_id: str,
    resolution: str,
    explanation: str,
    actor: dict[str, Any],
    *,
    expected_state: str | None = "reviewed",
) -> None:
    if not all(isinstance(value, str) and value.strip() for value in (
        refund_id, resolution, explanation
    )):
        raise ValueError("RESOLUTION_REQUIRED")
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            current = _require_current(connection, client, task, revision)
            _require_reviewed_mutable(current)
            if expected_state is not None and current[1] != expected_state:
                raise ValueError("STALE_STATE")
            report = json.loads(current[3])
            exception_ids = _exception_ids(report)
            if refund_id not in exception_ids:
                raise ValueError("EXCEPTION_NOT_FOUND")
            actor_json, evidence = _validated_actor(store, connection, client, task, actor)
            if connection.execute(
                """
                SELECT 1 FROM workflow_resolutions
                WHERE client = ? AND task = ? AND revision = ? AND refund_id = ?
                """,
                (client, task, revision, refund_id),
            ).fetchone() is not None:
                raise ValueError("EXCEPTION_ALREADY_RESOLVED")
            connection.execute(
                """
                INSERT INTO workflow_resolutions
                    (client, task, revision, refund_id, resolution, explanation,
                     actor_json, evidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client, task, revision, refund_id, resolution.strip(),
                    explanation.strip(), actor_json, evidence,
                ),
            )


def record_event(
    store: Store,
    client: str,
    task: str,
    revision: int,
    action: str,
    actor: dict[str, Any],
    reason: str,
    *,
    expected_state: str | None = None,
) -> str:
    """Atomically check revision/state, append evidence, and change state."""
    _validate_event_reason(action, reason)
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            return _event_in_transaction(
                store, connection, client, task, revision, action, actor, reason,
                expected_state=expected_state,
            )


def _validate_event_reason(action, reason):
    if action in {"return", "amend"} and not (
        isinstance(reason, str) and reason.strip()
    ):
        raise ValueError("REASON_REQUIRED")
    if not isinstance(reason, str):
        raise ValueError("INVALID_REASON")


def _event_in_transaction(
    store, connection, client, task, revision, action, actor, reason, *, expected_state
):
    """Caller owns the lock and transaction; all existing event gates apply."""
    current = _require_current(connection, client, task, revision)
    state = str(current[1])
    if expected_state is not None and state != expected_state:
        raise ValueError("STALE_STATE")
    new_state = transition(state, action)
    actor_json, evidence = _validated_actor(store, connection, client, task, actor)
    actor_value = json.loads(actor_json)
    if action == "confirm":
        if actor_value["channel"] != "customer":
            raise ValueError("CUSTOMER_EVIDENCE_REQUIRED")
        _validate_confirmation_proof(
            store, connection, client, task, revision, evidence, current
        )
    if action == "deliver":
        _delivery_gate(connection, client, task, revision, current)

    connection.execute(
        """
        INSERT INTO workflow_events
            (client, task, revision, action, from_state, to_state,
             actor_json, evidence, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            client, task, revision, action, state, new_state,
            actor_json, evidence, reason.strip(),
        ),
    )
    if action == "amend":
        new_revision = revision + 1
        connection.execute(
            """
            INSERT INTO workflow_revisions
                (client, task, revision, state, input_json, report_json)
            VALUES (?, ?, ?, 'draft', ?, ?)
            """,
            (client, task, new_revision, current[2], current[3]),
        )
    else:
        connection.execute(
            """
            UPDATE workflow_revisions SET state = ?
            WHERE client = ? AND task = ? AND revision = ?
            """,
            (new_state, client, task, revision),
        )
    return new_state


def delivery_snapshot_digest(
    store: Store, client: str, task: str, revision: int
) -> str:
    """Digest the immutable business content to which a receipt must refer."""
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            row = connection.execute(
                """
                SELECT revision, state, input_json, report_json, metric_rules_json
                FROM workflow_revisions
                WHERE client = ? AND task = ? AND revision = ?
                """,
                (client, task, revision),
            ).fetchone()
            if row is None:
                raise ValueError("NOT_FOUND")
            if row[1] not in {"delivered", "accepted"}:
                raise ValueError("NOT_DELIVERED")
            instance_id = _delivery_instance_id(connection, client, task, revision)
            return _delivery_digest(
                connection, client, task, revision, row, instance_id
            )


def delivery_instance_id(
    store: Store, client: str, task: str, revision: int
) -> int:
    """Return the immutable event ID for the current delivery instance."""
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            row = connection.execute(
                """
                SELECT state FROM workflow_revisions
                WHERE client = ? AND task = ? AND revision = ?
                """,
                (client, task, revision),
            ).fetchone()
            if row is None:
                raise ValueError("NOT_FOUND")
            if row[0] not in {"delivered", "accepted"}:
                raise ValueError("NOT_DELIVERED")
            return _delivery_instance_id(connection, client, task, revision)


def register_confirmation_proof(
    store: Store,
    client: str,
    task: str,
    revision: int,
    evidence: str,
    *,
    evidence_kind: str,
    purpose: str,
    delivery_instance_id: int,
    snapshot_digest: str,
    source_channel: str,
    source_evidence: str,
) -> None:
    """Register a structured receipt bound to this delivered revision and source."""
    if evidence_kind != "customer_confirmation_receipt" or purpose != "confirm_delivery":
        raise ValueError("INVALID_CONFIRMATION_PROOF")
    if source_channel not in {"email", "signed_document", "portal", "in_person"}:
        raise ValueError("INVALID_CONFIRMATION_PROOF")
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            current = _require_current(connection, client, task, revision)
            if current[1] != "delivered":
                raise ValueError("NOT_DELIVERED")
            current_instance_id = _delivery_instance_id(
                connection, client, task, revision
            )
            if delivery_instance_id != current_instance_id:
                raise ValueError("CONFIRMATION_BINDING_MISMATCH")
            receipt_bytes = _validated_evidence(
                store, connection, client, task, evidence
            )
            _validated_evidence(store, connection, client, task, source_evidence)
            if evidence == source_evidence:
                raise ValueError("INVALID_CONFIRMATION_PROOF")
            actual_digest = _delivery_digest(
                connection, client, task, revision, current, current_instance_id
            )
            if snapshot_digest != actual_digest:
                raise ValueError("CONFIRMATION_BINDING_MISMATCH")
            try:
                receipt = json.loads(receipt_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("INVALID_CONFIRMATION_PROOF") from None
            expected = {
                "evidence_kind": evidence_kind,
                "purpose": purpose,
                "client": client,
                "task": task,
                "revision": revision,
                "delivery_instance_id": delivery_instance_id,
                "snapshot_digest": snapshot_digest,
                "source_channel": source_channel,
                "source_evidence": source_evidence,
            }
            if receipt != expected:
                raise ValueError("CONFIRMATION_BINDING_MISMATCH")
            try:
                connection.execute(
                    """
                    INSERT INTO workflow_confirmation_proofs
                        (client, task, revision, delivery_event_id,
                         evidence, evidence_kind, purpose,
                         snapshot_digest, source_channel, source_evidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        client, task, revision, delivery_instance_id, evidence,
                        evidence_kind, purpose, snapshot_digest, source_channel,
                        source_evidence,
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValueError("CONFIRMATION_PROOF_ALREADY_REGISTERED") from None


def get_revision(
    store: Store, client: str, task: str, revision: int | None = None
) -> dict[str, Any]:
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            if revision is None:
                row = _current(connection, client, task)
            else:
                row = connection.execute(
                    """
                    SELECT revision, state, input_json, report_json, metric_rules_json
                    FROM workflow_revisions
                    WHERE client = ? AND task = ? AND revision = ?
                    """,
                    (client, task, revision),
                ).fetchone()
            if row is None:
                raise ValueError("NOT_FOUND")
            revision_value = int(row[0])
            resolutions = [
                {
                    "refund_id": item[0], "resolution": item[1],
                    "explanation": item[2], "actor": json.loads(item[3]),
                    "evidence": item[4],
                }
                for item in connection.execute(
                    """
                    SELECT refund_id, resolution, explanation, actor_json, evidence
                    FROM workflow_resolutions
                    WHERE client = ? AND task = ? AND revision = ?
                    ORDER BY refund_id
                    """,
                    (client, task, revision_value),
                )
            ]
            proof_rows = connection.execute(
                """
                SELECT delivery_event_id, evidence, evidence_kind, purpose,
                       snapshot_digest, source_channel, source_evidence
                FROM workflow_confirmation_proofs
                WHERE client = ? AND task = ? AND revision = ?
                ORDER BY delivery_event_id
                """,
                (client, task, revision_value),
            ).fetchall()
            current_delivery = (
                _optional_delivery_instance_id(
                    connection, client, task, revision_value
                )
                if row[1] in {"delivered", "accepted"}
                else None
            )
            proof_history = [
                {
                    "delivery_instance_id": item[0], "evidence": item[1],
                    "evidence_kind": item[2], "purpose": item[3],
                    "snapshot_digest": item[4], "source_channel": item[5],
                    "source_evidence": item[6],
                }
                for item in proof_rows
            ]
            active_proof = next(
                (
                    item for item in proof_history
                    if item["delivery_instance_id"] == current_delivery
                ),
                None,
            )
            return {
                "client": client,
                "task": task,
                "revision": revision_value,
                "state": row[1],
                "input_snapshot": deepcopy(json.loads(row[2])),
                "report_snapshot": deepcopy(json.loads(row[3])),
                "metric_rules": None if row[4] is None else json.loads(row[4]),
                "resolutions": resolutions,
                "delivery_instance_id": current_delivery,
                "confirmation_proof": active_proof,
                "confirmation_proofs": proof_history,
                "events": _events(connection, client, task, revision_value),
            }


def get_events(
    store: Store, client: str, task: str, revision: int | None = None
) -> list[dict[str, Any]]:
    with store.job_lock():
        with store._connect() as connection:
            _schema(connection)
            if revision is None:
                row = _current(connection, client, task)
                if row is None:
                    return []
                revision = int(row[0])
            return _events(connection, client, task, revision)


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflow_revisions (
            client TEXT NOT NULL, task TEXT NOT NULL, revision INTEGER NOT NULL,
            state TEXT NOT NULL, input_json TEXT NOT NULL, report_json TEXT NOT NULL,
            metric_rules_json TEXT, metric_rules_actor_json TEXT,
            metric_rules_evidence TEXT,
            PRIMARY KEY (client, task, revision),
            FOREIGN KEY (client, task) REFERENCES tasks (client, task)
        );
        CREATE TABLE IF NOT EXISTS workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client TEXT NOT NULL, task TEXT NOT NULL, revision INTEGER NOT NULL,
            action TEXT NOT NULL, from_state TEXT NOT NULL, to_state TEXT NOT NULL,
            actor_json TEXT NOT NULL, evidence TEXT NOT NULL, reason TEXT NOT NULL,
            FOREIGN KEY (client, task, revision)
                REFERENCES workflow_revisions (client, task, revision)
        );
        CREATE TABLE IF NOT EXISTS workflow_resolutions (
            client TEXT NOT NULL, task TEXT NOT NULL, revision INTEGER NOT NULL,
            refund_id TEXT NOT NULL, resolution TEXT NOT NULL,
            explanation TEXT NOT NULL, actor_json TEXT NOT NULL,
            evidence TEXT NOT NULL,
            PRIMARY KEY (client, task, revision, refund_id),
            FOREIGN KEY (client, task, revision)
                REFERENCES workflow_revisions (client, task, revision)
        );
        CREATE TABLE IF NOT EXISTS workflow_confirmation_proofs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client TEXT NOT NULL, task TEXT NOT NULL, revision INTEGER NOT NULL,
            delivery_event_id INTEGER NOT NULL,
            evidence TEXT NOT NULL UNIQUE, evidence_kind TEXT NOT NULL,
            purpose TEXT NOT NULL, snapshot_digest TEXT NOT NULL,
            source_channel TEXT NOT NULL, source_evidence TEXT NOT NULL,
            UNIQUE (client, task, revision, delivery_event_id),
            FOREIGN KEY (client, task, revision)
                REFERENCES workflow_revisions (client, task, revision),
            FOREIGN KEY (delivery_event_id) REFERENCES workflow_events (id)
        );
        """
    )


def _task_exists(connection: sqlite3.Connection, client: str, task: str) -> None:
    if connection.execute(
        "SELECT 1 FROM tasks WHERE client = ? AND task = ?", (client, task)
    ).fetchone() is None:
        raise ValueError("NOT_FOUND")


def _current(connection: sqlite3.Connection, client: str, task: str):
    return connection.execute(
        """
        SELECT revision, state, input_json, report_json, metric_rules_json
        FROM workflow_revisions WHERE client = ? AND task = ?
        ORDER BY revision DESC LIMIT 1
        """,
        (client, task),
    ).fetchone()


def _require_current(
    connection: sqlite3.Connection, client: str, task: str, revision: int
):
    _task_exists(connection, client, task)
    current = _current(connection, client, task)
    if current is None:
        raise ValueError("NOT_FOUND")
    if int(current[0]) != revision:
        raise ValueError("STALE_VERSION")
    return current


def _validated_actor(store, connection, client, task, actor):
    if not isinstance(actor, dict):
        raise ValueError("ACTOR_REQUIRED")
    for field in ("name", "channel", "evidence"):
        if not isinstance(actor.get(field), str) or not actor[field].strip():
            raise ValueError("ACTOR_REQUIRED")
    evidence = actor["evidence"]
    _validated_evidence(store, connection, client, task, evidence)
    return _canonical_object(actor, "ACTOR_REQUIRED"), evidence


def _validated_evidence(store, connection, client, task, evidence):
    if not isinstance(evidence, str) or not evidence:
        raise ValueError("EVIDENCE_NOT_FOUND")
    if connection.execute(
        """
        SELECT 1 FROM blobs WHERE client = ? AND task = ? AND digest = ?
        """,
        (client, task, evidence),
    ).fetchone() is None:
        raise ValueError("EVIDENCE_NOT_FOUND")
    evidence_path = checked_child(store.root, client, task, "blobs", evidence)
    try:
        evidence_bytes = evidence_path.read_bytes()
    except OSError:
        raise ValueError("EVIDENCE_NOT_FOUND") from None
    if blob_digest(evidence_bytes) != evidence:
        raise ValueError("EVIDENCE_NOT_FOUND")
    return evidence_bytes


def _canonical_object(value: Any, code: str) -> str:
    if not isinstance(value, dict):
        raise ValueError(code)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError(code) from None


def _validate_snapshots(inputs: dict[str, Any], report: dict[str, Any]) -> None:
    required = ("rule_version", "metrics", "exception_details", "orders", "refunds")
    if any(field not in report for field in required):
        raise ValueError("INVALID_REPORT_SNAPSHOT")
    if report.get("rule_version") != RULE_VERSION:
        raise ValueError("INVALID_REPORT_SNAPSHOT")
    if not isinstance(report["metrics"], dict) or not isinstance(
        report["exception_details"], list
    ):
        raise ValueError("INVALID_REPORT_SNAPSHOT")
    if any(field not in inputs for field in ("orders", "refunds", "ads")):
        raise ValueError("INVALID_INPUT_SNAPSHOT")
    try:
        replay = calculate(inputs["orders"], inputs["refunds"], inputs["ads"])
    except ValueError:
        raise ValueError("INVALID_INPUT_SNAPSHOT") from None
    for field in ("paid_less_refund", "ads", "exceptions"):
        if report.get(field) != replay[field]:
            raise ValueError("INVALID_REPORT_SNAPSHOT")
    if report.get("orders") != inputs["orders"] or report.get("refunds") != inputs["refunds"]:
        raise ValueError("INVALID_REPORT_SNAPSHOT")
    if _domain_rows(report.get("exception_details"), (
        "code", "id", "shop", "order", "amount"
    )) != _domain_rows(replay["exception_details"], (
        "code", "id", "shop", "order", "amount"
    )):
        raise ValueError("INVALID_REPORT_SNAPSHOT")
    if _domain_rows(report.get("refund_audit"), (
        "shop", "order", "paid", "refund", "refund_ids"
    )) != _domain_rows(replay["refund_audit"], (
        "shop", "order", "paid", "refund", "refund_ids"
    )):
        raise ValueError("INVALID_REPORT_SNAPSHOT")
    for metric_name, canonical in replay["metrics"].items():
        supplied = report["metrics"].get(metric_name)
        if not isinstance(supplied, dict) or any(
            supplied.get(field) != canonical[field] for field in ("value", "reason")
        ):
            raise ValueError("INVALID_REPORT_SNAPSHOT")
        if any(
            supplied.get(field) != report.get(field)
            for field in ("currency", "places", "period")
        ) or supplied.get("rule_version") != report["rule_version"]:
            raise ValueError("INVALID_REPORT_SNAPSHOT")
        if not isinstance(supplied.get("sources"), list):
            raise ValueError("INVALID_REPORT_SNAPSHOT")


def _domain_rows(value: Any, fields: tuple[str, ...]) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    if any(not isinstance(row, dict) for row in value):
        return None
    return [{field: row.get(field) for field in fields} for row in value]


def _exception_ids(report: dict[str, Any]) -> set[str]:
    ids = set()
    for detail in report.get("exception_details", []):
        if detail.get("code") == "UNMATCHED_REFUND" and isinstance(detail.get("id"), str):
            ids.add(detail["id"])
    return ids


def _delivery_gate(connection, client, task, revision, current) -> None:
    if current[4] is None:
        raise ValueError("METRIC_RULES_NOT_FROZEN")
    inputs = json.loads(current[2])
    required = set(calculate(
        inputs["orders"], inputs["refunds"], inputs["ads"]
    )["exceptions"])
    resolved = {
        row[0]
        for row in connection.execute(
            """
            SELECT refund_id FROM workflow_resolutions
            WHERE client = ? AND task = ? AND revision = ?
              AND length(trim(resolution)) > 0 AND length(trim(explanation)) > 0
            """,
            (client, task, revision),
        )
    }
    if not required.issubset(resolved):
        raise ValueError("UNRESOLVED_EXCEPTIONS")


def _require_reviewed_mutable(current) -> None:
    state = str(current[1])
    if state in {"delivered", "accepted", "returned"}:
        raise ValueError("IMMUTABLE_REVISION")
    if state != "reviewed":
        raise ValueError("INVALID_TRANSITION")


def _delivery_digest(
    connection, client, task, revision, current, delivery_event_id
) -> str:
    resolutions = [
        {
            "refund_id": row[0], "resolution": row[1], "explanation": row[2],
            "actor": json.loads(row[3]), "evidence": row[4],
        }
        for row in connection.execute(
            """
            SELECT refund_id, resolution, explanation, actor_json, evidence
            FROM workflow_resolutions
            WHERE client = ? AND task = ? AND revision = ? ORDER BY refund_id
            """,
            (client, task, revision),
        )
    ]
    binding = {
        "client": client,
        "task": task,
        "revision": revision,
        "delivery_instance_id": delivery_event_id,
        "input_snapshot": json.loads(current[2]),
        "report_snapshot": json.loads(current[3]),
        "metric_rules": json.loads(current[4]),
        "resolutions": resolutions,
    }
    return blob_digest(_canonical_object(binding, "INVALID_SNAPSHOT").encode())


def _validate_confirmation_proof(
    store, connection, client, task, revision, evidence, current
) -> None:
    current_instance_id = _delivery_instance_id(connection, client, task, revision)
    proof = connection.execute(
        """
        SELECT evidence_kind, purpose, snapshot_digest, source_channel,
               source_evidence
        FROM workflow_confirmation_proofs
        WHERE client = ? AND task = ? AND revision = ?
          AND delivery_event_id = ? AND evidence = ?
        """,
        (client, task, revision, current_instance_id, evidence),
    ).fetchone()
    if proof is None:
        raise ValueError("CUSTOMER_CONFIRMATION_PROOF_REQUIRED")
    if proof[0] != "customer_confirmation_receipt" or proof[1] != "confirm_delivery":
        raise ValueError("CUSTOMER_CONFIRMATION_PROOF_REQUIRED")
    _validated_evidence(store, connection, client, task, proof[4])
    if proof[2] != _delivery_digest(
        connection, client, task, revision, current, current_instance_id
    ):
        raise ValueError("CONFIRMATION_BINDING_MISMATCH")


def _delivery_instance_id(connection, client, task, revision) -> int:
    instance_id = _optional_delivery_instance_id(connection, client, task, revision)
    if instance_id is None:
        raise ValueError("NOT_DELIVERED")
    return instance_id


def _optional_delivery_instance_id(connection, client, task, revision) -> int | None:
    row = connection.execute(
        """
        SELECT id FROM workflow_events
        WHERE client = ? AND task = ? AND revision = ? AND action = 'deliver'
        ORDER BY id DESC LIMIT 1
        """,
        (client, task, revision),
    ).fetchone()
    return None if row is None else int(row[0])


def _events(connection, client, task, revision):
    return [
        {
            "action": row[0], "from_state": row[1], "to_state": row[2],
            "actor": json.loads(row[3]), "evidence": row[4], "reason": row[5],
        }
        for row in connection.execute(
            """
            SELECT action, from_state, to_state, actor_json, evidence, reason
            FROM workflow_events
            WHERE client = ? AND task = ? AND revision = ? ORDER BY id
            """,
            (client, task, revision),
        )
    ]
