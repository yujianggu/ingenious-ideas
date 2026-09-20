import json
import sqlite3
from copy import deepcopy

import pytest

from service_b10.reporting import calculate
from service_b10.store import Store
from service_b10.workflow import (
    delivery_instance_id,
    delivery_snapshot_digest,
    freeze_metric_rules,
    get_events,
    get_revision,
    record_event,
    register_confirmation_proof,
    resolve_exception,
    stage_revision,
    transition,
)


def _evidence(store, client, task, name="operator"):
    return store.put(client, task, f"{name}.json", b'{"verified":true}')


def _actor(store, client, task, *, name="reviewer", channel="internal"):
    return {
        "name": name,
        "channel": channel,
        "evidence": _evidence(store, client, task, name),
    }


def _confirmation_actor(store, client="a", task="t1", revision=1):
    instance_id = delivery_instance_id(store, client, task, revision)
    snapshot_digest = delivery_snapshot_digest(store, client, task, revision)
    source_evidence = store.put(
        client, task, f"customer-source-r{revision}.eml", b"customer accepted delivery"
    )
    receipt = {
        "evidence_kind": "customer_confirmation_receipt",
        "purpose": "confirm_delivery",
        "client": client,
        "task": task,
        "revision": revision,
        "delivery_instance_id": instance_id,
        "snapshot_digest": snapshot_digest,
        "source_channel": "email",
        "source_evidence": source_evidence,
    }
    evidence = store.put(
        client,
        task,
        f"confirmation-r{revision}.json",
        json.dumps(receipt, sort_keys=True).encode(),
    )
    register_confirmation_proof(
        store, client, task, revision, evidence,
        evidence_kind="customer_confirmation_receipt",
        purpose="confirm_delivery",
        delivery_instance_id=instance_id,
        snapshot_digest=snapshot_digest,
        source_channel="email",
        source_evidence=source_evidence,
    )
    return {"name": "buyer", "channel": "customer", "evidence": evidence}


def _snapshots(unmatched=True):
    orders = [{"shop": "a", "order": "1", "paid": 10000}]
    refunds = [{"id": "r1", "shop": "a", "order": "1", "amount": 1000}]
    if unmatched:
        refunds.append({"id": "rx", "shop": "x", "order": "9", "amount": 50})
    report = calculate(orders, refunds, 500)
    report.update({
        "rule_version": "b10-reporting-v1",
        "currency": "CNY",
        "places": 2,
        "period": "2026-09",
        "orders": orders,
        "refunds": refunds,
    })
    for metric in report["metrics"].values():
        metric.update(
            rule_version="b10-reporting-v1",
            currency="CNY",
            places=2,
            period="2026-09",
        )
    return {"orders": orders, "refunds": refunds, "ads": 500}, report


def _new_workflow(tmp_path, client="a", task="t1", *, unmatched=True):
    store = Store(tmp_path)
    store.create(client, task)
    inputs, report = _snapshots(unmatched)
    assert stage_revision(store, client, task, inputs, report) == 1
    return store, inputs, report


def _submit_review(store, client="a", task="t1", revision=1):
    record_event(store, client, task, revision, "submit", _actor(store, client, task), "")
    record_event(store, client, task, revision, "review", _actor(store, client, task), "checked")


def test_return_requires_new_delivery():
    state = transition("draft", "submit")
    state = transition(state, "review")
    state = transition(state, "deliver")
    state = transition(state, "return")
    with pytest.raises(ValueError, match="INVALID_TRANSITION"):
        transition(state, "confirm")
    state = transition(state, "resubmit")
    state = transition(state, "review")
    assert transition(transition(state, "deliver"), "confirm") == "accepted"


def test_submit_freezes_json_and_replay_isolated_from_original_objects(tmp_path):
    store, inputs, report = _new_workflow(tmp_path, unmatched=False)
    record_event(store, "a", "t1", 1, "submit", _actor(store, "a", "t1"), "")
    inputs["orders"][0]["paid"] = 999999
    report["paid_less_refund"] = 1

    saved = get_revision(store, "a", "t1", 1)
    assert saved["input_snapshot"]["orders"][0]["paid"] == 10000
    assert saved["report_snapshot"]["paid_less_refund"] == 9000
    serialized = json.dumps(saved["input_snapshot"], sort_keys=True)
    replay = json.loads(serialized)
    assert calculate(replay["orders"], replay["refunds"], replay["ads"])["paid_less_refund"] == 9000


def test_delivery_gate_requires_frozen_rules_and_all_refund_resolutions(tmp_path):
    store, _, _ = _new_workflow(tmp_path)
    _submit_review(store)
    actor = _actor(store, "a", "t1")
    with pytest.raises(ValueError, match="METRIC_RULES_NOT_FROZEN"):
        record_event(store, "a", "t1", 1, "deliver", actor, "")
    freeze_metric_rules(store, "a", "t1", 1, {"version": "b10-reporting-v1"}, actor)
    with pytest.raises(ValueError, match="UNRESOLVED_EXCEPTIONS"):
        record_event(store, "a", "t1", 1, "deliver", actor, "")
    with pytest.raises(ValueError, match="RESOLUTION_REQUIRED"):
        resolve_exception(store, "a", "t1", 1, "rx", "excluded", "", actor)
    resolve_exception(store, "a", "t1", 1, "rx", "excluded", "wrong shop", actor)
    assert record_event(store, "a", "t1", 1, "deliver", actor, "ready") == "delivered"


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda report: report.update(exception_details=[]),
        lambda report: report["metrics"]["paid_less_refund"].update(value=999999),
        lambda report: report["metrics"]["paid_less_refund"].update(rule_version="fake"),
        lambda report: report.update(orders=[]),
    ],
)
def test_stage_rejects_caller_corruption_of_authoritative_report(tmp_path, corrupt):
    store = Store(tmp_path)
    store.create("a", "t1")
    inputs, report = _snapshots(True)
    corrupt(report)
    with pytest.raises(ValueError, match="INVALID_REPORT_SNAPSHOT"):
        stage_revision(store, "a", "t1", inputs, report)


def test_actual_task_owned_evidence_required_and_role_string_is_ignored(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    store.create("a", "other")
    foreign = _evidence(store, "a", "other")
    with pytest.raises(ValueError, match="EVIDENCE_NOT_FOUND"):
        record_event(store, "a", "t1", 1, "submit", {
            "name": "customer", "channel": "customer", "evidence": foreign,
            "role": "customer",
        }, "")
    assert get_events(store, "a", "t1", 1) == []
    with pytest.raises(ValueError, match="ACTOR_REQUIRED"):
        record_event(store, "a", "t1", 1, "submit", {
            "name": "customer", "channel": "customer", "role": "customer",
        }, "")


def test_missing_evidence_bytes_are_not_authentication(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    actor = _actor(store, "a", "t1")
    (store.root / "a" / "t1" / "blobs" / actor["evidence"]).unlink()
    with pytest.raises(ValueError, match="EVIDENCE_NOT_FOUND"):
        record_event(store, "a", "t1", 1, "submit", actor, "")


def test_stale_state_double_confirmation_and_current_revision_only(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    _submit_review(store)
    actor = _actor(store, "a", "t1")
    freeze_metric_rules(store, "a", "t1", 1, {"version": "v1"}, actor)
    record_event(store, "a", "t1", 1, "deliver", actor, "")
    customer = _confirmation_actor(store)
    assert record_event(store, "a", "t1", 1, "confirm", customer, "accepted", expected_state="delivered") == "accepted"
    with pytest.raises(ValueError, match="IMMUTABLE_REVISION"):
        freeze_metric_rules(
            store, "a", "t1", 1, {"version": "changed"}, actor,
            expected_state="accepted",
        )
    with pytest.raises(ValueError, match="STALE_STATE"):
        record_event(store, "a", "t1", 1, "confirm", customer, "again", expected_state="delivered")

    assert record_event(store, "a", "t1", 1, "amend", actor, "correction") == "draft"
    assert get_revision(store, "a", "t1", 2)["state"] == "draft"
    with pytest.raises(ValueError, match="STALE_VERSION"):
        record_event(store, "a", "t1", 1, "confirm", customer, "old")


def test_second_store_observes_expected_state_atomically(tmp_path):
    first, _, _ = _new_workflow(tmp_path, unmatched=False)
    second = Store(tmp_path)
    actor = _actor(first, "a", "t1")
    record_event(first, "a", "t1", 1, "submit", actor, "", expected_state="draft")
    with pytest.raises(ValueError, match="STALE_STATE"):
        record_event(second, "a", "t1", 1, "submit", actor, "", expected_state="draft")
    assert len(get_events(first, "a", "t1", 1)) == 1


def test_confirm_rejects_unstructured_or_wrong_snapshot_evidence(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    _submit_review(store)
    actor = _actor(store, "a", "t1")
    freeze_metric_rules(store, "a", "t1", 1, {"version": "v1"}, actor)
    record_event(store, "a", "t1", 1, "deliver", actor, "")
    ordinary = {
        "name": "buyer", "channel": "customer",
        "evidence": store.put("a", "t1", "orders.csv", b"order,paid\n1,100"),
    }
    with pytest.raises(ValueError, match="CUSTOMER_CONFIRMATION_PROOF_REQUIRED"):
        record_event(store, "a", "t1", 1, "confirm", ordinary, "accepted")

    source = store.put("a", "t1", "source.eml", b"accepted")
    wrong = {
        "evidence_kind": "customer_confirmation_receipt",
        "purpose": "confirm_delivery",
        "client": "a", "task": "t1", "revision": 1,
        "delivery_instance_id": delivery_instance_id(store, "a", "t1", 1),
        "snapshot_digest": "0" * 64,
        "source_channel": "email", "source_evidence": source,
    }
    receipt = store.put("a", "t1", "wrong.json", json.dumps(wrong).encode())
    with pytest.raises(ValueError, match="CONFIRMATION_BINDING_MISMATCH"):
        register_confirmation_proof(
            store, "a", "t1", 1, receipt,
            evidence_kind="customer_confirmation_receipt",
            purpose="confirm_delivery",
            delivery_instance_id=delivery_instance_id(store, "a", "t1", 1),
            snapshot_digest="0" * 64,
            source_channel="email", source_evidence=source,
        )


def test_confirmation_receipt_cannot_be_reused_for_amended_revision(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    _submit_review(store)
    actor = _actor(store, "a", "t1")
    freeze_metric_rules(store, "a", "t1", 1, {"version": "v1"}, actor)
    record_event(store, "a", "t1", 1, "deliver", actor, "")
    old_receipt = _confirmation_actor(store)
    feedback = _actor(store, "a", "t1", name="buyer", channel="customer")
    record_event(store, "a", "t1", 1, "return", feedback, "revise")
    record_event(store, "a", "t1", 1, "amend", actor, "new revision")
    record_event(store, "a", "t1", 2, "submit", actor, "")
    record_event(store, "a", "t1", 2, "review", actor, "checked")
    freeze_metric_rules(store, "a", "t1", 2, {"version": "v1"}, actor)
    record_event(store, "a", "t1", 2, "deliver", actor, "")
    with pytest.raises(ValueError, match="CUSTOMER_CONFIRMATION_PROOF_REQUIRED"):
        record_event(store, "a", "t1", 2, "confirm", old_receipt, "accepted")


def test_confirmation_rechecks_original_source_attachment(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    _submit_review(store)
    actor = _actor(store, "a", "t1")
    freeze_metric_rules(store, "a", "t1", 1, {"version": "v1"}, actor)
    record_event(store, "a", "t1", 1, "deliver", actor, "")
    customer = _confirmation_actor(store)
    source = get_revision(store, "a", "t1", 1)["confirmation_proof"]["source_evidence"]
    (store.root / "a" / "t1" / "blobs" / source).unlink()
    with pytest.raises(ValueError, match="EVIDENCE_NOT_FOUND"):
        record_event(store, "a", "t1", 1, "confirm", customer, "accepted")


def test_return_resubmit_redelivery_requires_new_same_revision_receipt(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    _submit_review(store)
    actor = _actor(store, "a", "t1")
    freeze_metric_rules(store, "a", "t1", 1, {"version": "v1"}, actor)
    record_event(store, "a", "t1", 1, "deliver", actor, "first")
    old_receipt = _confirmation_actor(store)
    feedback = _actor(store, "a", "t1", name="buyer", channel="customer")
    record_event(store, "a", "t1", 1, "return", feedback, "recheck")
    returned = get_revision(store, "a", "t1", 1)
    assert returned["confirmation_proof"] is None
    assert len(returned["confirmation_proofs"]) == 1
    record_event(store, "a", "t1", 1, "resubmit", actor, "")
    record_event(store, "a", "t1", 1, "review", actor, "checked again")
    record_event(store, "a", "t1", 1, "deliver", actor, "second")

    with pytest.raises(ValueError, match="CUSTOMER_CONFIRMATION_PROOF_REQUIRED"):
        record_event(store, "a", "t1", 1, "confirm", old_receipt, "accepted")
    new_receipt = _confirmation_actor(store)
    assert record_event(
        store, "a", "t1", 1, "confirm", new_receipt, "accepted"
    ) == "accepted"
    assert len(get_revision(store, "a", "t1", 1)["confirmation_proofs"]) == 2


def test_return_and_amend_preserve_old_delivery_history(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    _submit_review(store)
    actor = _actor(store, "a", "t1")
    freeze_metric_rules(store, "a", "t1", 1, {"version": "v1"}, actor)
    record_event(store, "a", "t1", 1, "deliver", actor, "")
    customer = _actor(store, "a", "t1", name="buyer", channel="customer")
    record_event(store, "a", "t1", 1, "return", customer, "correct refund")
    assert record_event(store, "a", "t1", 1, "amend", actor, "will correct") == "draft"

    old = get_revision(store, "a", "t1", 1)
    new = get_revision(store, "a", "t1", 2)
    assert old["state"] == "returned"
    assert [event["action"] for event in old["events"]][-2:] == ["return", "amend"]
    assert new["report_snapshot"] == old["report_snapshot"]
    assert new["revision"] == 2


def test_rules_and_resolutions_are_immutable_outside_reviewed_state(tmp_path):
    store, _, _ = _new_workflow(tmp_path)
    _submit_review(store)
    actor = _actor(store, "a", "t1")
    freeze_metric_rules(store, "a", "t1", 1, {"version": "v1"}, actor)
    with pytest.raises(ValueError, match="METRIC_RULES_ALREADY_FROZEN"):
        freeze_metric_rules(store, "a", "t1", 1, {"version": "changed"}, actor)
    resolve_exception(store, "a", "t1", 1, "rx", "excluded", "wrong shop", actor)
    with pytest.raises(ValueError, match="EXCEPTION_ALREADY_RESOLVED"):
        resolve_exception(store, "a", "t1", 1, "rx", "changed", "rewrite", actor)
    record_event(store, "a", "t1", 1, "deliver", actor, "")
    frozen = deepcopy(get_revision(store, "a", "t1", 1))
    for expected_state in (None, "delivered"):
        with pytest.raises(ValueError, match="IMMUTABLE_REVISION"):
            freeze_metric_rules(
                store, "a", "t1", 1, {"version": "after"}, actor,
                expected_state=expected_state,
            )
        with pytest.raises(ValueError, match="IMMUTABLE_REVISION"):
            resolve_exception(
                store, "a", "t1", 1, "rx", "after", "after", actor,
                expected_state=expected_state,
            )
    assert get_revision(store, "a", "t1", 1) == frozen


def test_two_tasks_have_independent_revisions(tmp_path):
    store, _, _ = _new_workflow(tmp_path, "a", "t1", unmatched=False)
    store.create("a", "t2")
    inputs, report = _snapshots(False)
    stage_revision(store, "a", "t2", inputs, report)
    _submit_review(store, "a", "t1")
    assert get_revision(store, "a", "t1", 1)["state"] == "reviewed"
    assert get_revision(store, "a", "t2", 1)["state"] == "draft"


def test_failed_event_rolls_back_without_partial_history(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    actor = _actor(store, "a", "t1")
    record_event(store, "a", "t1", 1, "submit", actor, "")
    before = get_events(store, "a", "t1", 1)
    with pytest.raises(ValueError, match="REASON_REQUIRED"):
        record_event(store, "a", "t1", 1, "return", actor, "")
    assert get_events(store, "a", "t1", 1) == before
    assert get_revision(store, "a", "t1", 1)["state"] == "submitted"


def test_database_failure_after_event_insert_rolls_back_event(tmp_path):
    store, _, _ = _new_workflow(tmp_path, unmatched=False)
    actor = _actor(store, "a", "t1")
    record_event(store, "a", "t1", 1, "submit", actor, "")
    with store._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_review_update BEFORE UPDATE OF state
            ON workflow_revisions WHEN NEW.state = 'reviewed'
            BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """
        )
    before = get_events(store, "a", "t1", 1)
    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        record_event(store, "a", "t1", 1, "review", actor, "checked")
    assert get_events(store, "a", "t1", 1) == before
    assert get_revision(store, "a", "t1", 1)["state"] == "submitted"
