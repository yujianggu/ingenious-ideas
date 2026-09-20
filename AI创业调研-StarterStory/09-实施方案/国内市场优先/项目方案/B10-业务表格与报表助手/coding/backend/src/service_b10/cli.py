"""Local operator CLI. Identifiers are scope boundaries, not customer login."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from .store import Store, checked_child
from .inputs import PeriodImports, get_active_import, import_file
from .reporting import calculate_batches
from .workflow import (get_revision, stage_and_submit_revision, record_event, freeze_metric_rules,
                       resolve_exception, register_confirmation_proof,
                       delivery_instance_id, delivery_snapshot_digest)
from .exports import (export_bundle, verify_bundle, _publish_no_replace,
                      _reject_symlink_components)
from .backup import backup, restore
from .model import generate_candidate, validate_candidate


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes raw values, including paths or mistakenly pasted keys.
        raise ValueError('INVALID_ARGUMENTS')


def parser():
    p = Parser(description='B10 本地业务表格生产与人工交付；模型默认关闭')
    commands = p.add_subparsers(dest='command', required=True)
    for name in ('init', 'new', 'submit', 'produce', 'review', 'deliver', 'return', 'confirm',
                 'amend', 'resubmit', 'evidence', 'freeze-rules', 'resolve', 'receipt',
                 'status', 'export', 'backup', 'restore'):
        cmd = commands.add_parser(name)
        if name != 'restore':
            cmd.add_argument('--root', type=Path, required=True)
        if name not in {'init', 'backup', 'restore'}:
            cmd.add_argument('--client', required=True)
            cmd.add_argument('--task', required=True)
        if name in {'review', 'deliver', 'return', 'confirm', 'amend', 'resubmit',
                    'freeze-rules', 'resolve', 'receipt', 'export', 'status'}:
            cmd.add_argument('--revision', type=int, required=name not in {'status', 'amend'})
        if name in {'produce', 'review', 'deliver', 'return', 'confirm', 'amend',
                    'resubmit', 'freeze-rules', 'resolve'}:
            cmd.add_argument('--actor-record', type=Path, required=True)
            cmd.add_argument('--reason', default='', required=name in {'return', 'amend', 'resolve'})
        if name in {'evidence', 'submit', 'receipt'}:
            cmd.add_argument('--input', type=Path, required=True)
        if name == 'submit':
            cmd.add_argument('--schema', type=Path, required=True)
            cmd.add_argument('--category', choices=['orders', 'refunds', 'ads'], required=True)
            cmd.add_argument('--period', required=True)
            cmd.add_argument('--supersedes')
        if name == 'produce':
            cmd.add_argument('--period', required=True)
            cmd.add_argument('--candidate', type=Path)
            cmd.add_argument('--enable-model', action='store_true')
            cmd.add_argument('--model-config', type=Path)
            cmd.add_argument('--model-authorization', type=Path)
        if name == 'freeze-rules':
            cmd.add_argument('--rules', type=Path, required=True)
        if name == 'resolve':
            cmd.add_argument('--refund-id', required=True)
            cmd.add_argument('--resolution', required=True)
        if name in {'export', 'backup', 'restore'}:
            cmd.add_argument('--out', type=Path, required=True)
        if name == 'restore':
            cmd.add_argument('--manifest', type=Path, required=True)
    return p


def _json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('INVALID_JSON') from None
    if not isinstance(value, dict):
        raise ValueError('INVALID_JSON')
    return value


def _actor(store, args):
    actor = _json(args.actor_record)
    if any(not isinstance(actor.get(key), str) or not actor[key].strip()
           for key in ('name', 'channel', 'evidence')):
        raise ValueError('ACTOR_REQUIRED')
    store.read(args.client, args.task, actor['evidence'])
    return actor


def _current(store, args):
    try:
        return get_revision(store, args.client, args.task)
    except ValueError as error:
        if str(error) == 'NOT_FOUND':
            return None
        raise


def _produce(store, args, actor):
    current = _current(store, args)
    if current is not None and current['state'] != 'draft':
        raise ValueError('SNAPSHOT_FROZEN')
    batches = [get_active_import(store, args.client, args.task, category, args.period)
               for category in ('orders', 'refunds', 'ads')]
    if batches[0] is None or batches[1] is None:
        raise ValueError('MISSING_REQUIRED')
    report = calculate_batches(PeriodImports(*batches))
    frozen = {key: report[key] for key in ('orders', 'refunds', 'ads')}
    ids = [batch.source_file_id for batch in batches if batch is not None]
    metrics = {key: report[key] for key in ('paid_less_refund', 'ads')}
    candidate = None
    if args.candidate:
        candidate = validate_candidate(_json(args.candidate), metrics=metrics, source_ids=ids)
    if args.enable_model:
        if not args.model_config or not args.model_authorization:
            raise ValueError('MODEL_NOT_AUTHORIZED')
        config = _json(args.model_config)
        authorization = _json(args.model_authorization)
        if (authorization.get('purpose') != 'authorize_model'
                or authorization.get('model_id') != config.get('model_id')
                or authorization.get('endpoint') != config.get('endpoint')
                or authorization.get('allowed_fields') != ['metrics', 'source_ids']
                or not isinstance(authorization.get('evidence'), str)):
            raise ValueError('MODEL_NOT_AUTHORIZED')
        store.read(args.client, args.task, authorization['evidence'])
        candidate = generate_candidate({'config': config, 'input_snapshot': frozen,
                                       'source_ids': ids, 'metrics': metrics},
                                      enabled=True, permitted=True, transport=None)
        frozen['model_authorization_evidence'] = store.put(
            args.client, args.task, 'model-authorization.json',
            json.dumps(authorization, ensure_ascii=False).encode())
        frozen['model_config_evidence'] = store.put(
            args.client, args.task, 'model-config.json',
            json.dumps(config, ensure_ascii=False).encode())
    # Default path never constructs a transport, reads a key or calls the model.
    if candidate is not None:
        frozen['candidate_evidence'] = store.put(args.client, args.task, 'candidate.json',
                                                json.dumps(candidate, ensure_ascii=False).encode())
    revision = stage_and_submit_revision(
        store, args.client, args.task, frozen, report, actor, args.reason,
        expected_revision=0 if current is None else current['revision'],
    )
    return {'revision': revision, 'state': 'submitted'}


def _archive_export(store, args):
    payload = get_revision(store, args.client, args.task, args.revision)
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    archive = checked_child(store.root, args.client, args.task, 'exports',
                            f'r{args.revision}-{identity}')
    destination = args.out.absolute()
    _reject_symlink_components(destination)
    # Only the owning task's managed archive may be inside Store; arbitrary
    # customer output directories remain supported outside the workspace.
    if destination.resolve().is_relative_to(store.root) and destination.resolve() != archive:
        raise ValueError('EXPORT_OUTSIDE_STORE_REQUIRED')
    if destination.exists() and destination.resolve() != archive:
        raise ValueError('VERSION_EXISTS')
    with store.job_lock():
        if archive.exists():
            verify_bundle(archive / 'manifest.json')
            if json.loads((archive / 'snapshot.json').read_text()) != payload:
                raise ValueError('DIGEST_MISMATCH')
        else:
            export_bundle(archive, payload)
        if destination.resolve() == archive:
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.b10-export-', dir=destination.parent))
        try:
            shutil.copytree(archive, temporary, dirs_exist_ok=True)
            verify_bundle(temporary / 'manifest.json')
            _publish_no_replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def _run(args):
    if args.command == 'restore':
        restore(args.manifest, args.out)
        return {}
    store = Store(args.root)
    command = args.command
    if command == 'init':
        return {}
    if command == 'backup':
        backup(store, args.out)
        return {}
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.client) or not re.fullmatch(r'[A-Za-z0-9_-]+', args.task):
        raise ValueError('INVALID_PATH')
    if command == 'new':
        store.create(args.client, args.task)
        return {}
    if command == 'evidence':
        return {'evidence': store.put(args.client, args.task, 'evidence.bin', args.input.read_bytes())}
    if command == 'submit':
        current = _current(store, args)
        if current is not None and current['state'] != 'draft':
            raise ValueError('SNAPSHOT_FROZEN')
        schema = _json(args.schema)
        selected = schema.get(args.category, schema)
        if not isinstance(selected, dict):
            raise ValueError('INVALID_CONFIG')
        batch = import_file(store, args.client, args.task, args.category, args.period,
                            args.input.name, args.input.read_bytes(), selected,
                            supersedes=args.supersedes)
        return {'batch_id': batch.batch_id, 'source_id': batch.source_file_id}
    if command == 'export':
        _archive_export(store, args)
        return {'revision': args.revision}
    if command == 'receipt':
        receipt = _json(args.input)
        evidence = store.put(args.client, args.task, 'confirmation-receipt.json', args.input.read_bytes())
        fields = ('evidence_kind', 'purpose', 'delivery_instance_id', 'snapshot_digest',
                  'source_channel', 'source_evidence')
        if any(key not in receipt for key in fields):
            raise ValueError('INVALID_CONFIRMATION_PROOF')
        register_confirmation_proof(store, args.client, args.task, args.revision, evidence,
                                    **{key: receipt[key] for key in fields})
        return {'evidence': evidence}
    if command == 'status':
        revision = get_revision(store, args.client, args.task, args.revision)
        result = {key: revision[key] for key in ('revision', 'state', 'delivery_instance_id')}
        if revision['state'] in {'delivered', 'accepted'}:
            result['snapshot_digest'] = delivery_snapshot_digest(store, args.client, args.task, revision['revision'])
        return result
    actor = _actor(store, args)
    if command == 'produce':
        return _produce(store, args, actor)
    revision = args.revision
    if revision is None:
        revision = get_revision(store, args.client, args.task)['revision']
    if command == 'freeze-rules':
        freeze_metric_rules(store, args.client, args.task, revision, _json(args.rules), actor)
        return {'revision': revision}
    if command == 'resolve':
        resolve_exception(store, args.client, args.task, revision, args.refund_id,
                          args.resolution, args.reason, actor)
        return {'revision': revision}
    state = record_event(store, args.client, args.task, revision, command, actor, args.reason)
    result = {'revision': revision + 1 if command == 'amend' else revision, 'state': state}
    if command == 'deliver':
        result.update(delivery_instance_id=delivery_instance_id(store, args.client, args.task, revision),
                      snapshot_digest=delivery_snapshot_digest(store, args.client, args.task, revision))
    return result


def main(argv: list[str] | None = None) -> int:
    args = None
    try:
        args = parser().parse_args(argv)
        result = _run(args)
        print(json.dumps({'code': 'OK', **({'task': args.task} if hasattr(args, 'task') else {}), **result}))
        return 0
    except ValueError as error:
        code = str(error)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,63}', code):
            code = 'INVALID_INPUT'
        exit_code = 2
    except (OSError, sqlite3.Error):
        code, exit_code = 'IO_ERROR', 1
    except (TypeError, KeyError, AttributeError, OverflowError, BadZipFile, ParseError):
        code, exit_code = 'INVALID_INPUT', 2
    except Exception:
        # Never expose an unexpected parser/driver message or traceback in operator logs.
        code, exit_code = 'INTERNAL_ERROR', 1
    task = getattr(args, 'task', None)
    diagnostic = {'code': code}
    if isinstance(task, str) and re.fullmatch(r'[A-Za-z0-9_-]+', task):
        diagnostic['task'] = task
    print(json.dumps(diagnostic), file=sys.stderr)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
