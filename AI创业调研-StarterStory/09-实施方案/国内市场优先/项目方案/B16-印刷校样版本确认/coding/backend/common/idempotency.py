import hashlib
import json

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from .errors import IdempotencyConflict
from .models import CommandReceipt


def request_digest(payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay(receipt, digest):
    if receipt.request_hash != digest:
        raise IdempotencyConflict()
    return receipt.result_json


def execute_once(scope: tuple, key: str, payload: dict, command) -> dict:
    tenant_id, actor_id, operation, object_id = scope
    key = str(key or "").strip()
    if not key:
        raise ValidationError({"idempotency_key": ["不能为空"]})
    key_max_length = CommandReceipt._meta.get_field("key").max_length
    if len(key) > key_max_length:
        raise ValidationError({"idempotency_key": [f"不能超过{key_max_length}个字符"]})
    digest = request_digest(payload)
    lookup = {
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "operation": operation,
        "object_id": str(object_id or ""),
        "key": key,
    }
    existing = CommandReceipt.objects.filter(**lookup).first()
    if existing is not None:
        return _replay(existing, digest)
    try:
        with transaction.atomic():
            receipt = CommandReceipt.objects.create(
                **lookup, request_hash=digest, result_json={}
            )
            result = command()
            receipt.result_json = result
            receipt.save(update_fields=["result_json"])
            return result
    except IntegrityError as exc:
        constraint_name = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
        if constraint_name != "unique_command_receipt_scope_key":
            raise
        # The failed atomic block has ended. PostgreSQL's uniqueness check has
        # waited for the winner, so its committed receipt is now visible.
        return _replay(CommandReceipt.objects.get(**lookup), digest)
