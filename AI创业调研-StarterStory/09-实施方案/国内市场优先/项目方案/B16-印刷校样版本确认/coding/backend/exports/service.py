import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile
from django.conf import settings
from django.db import transaction
from django.template import Context, Engine
from django.utils import timezone
from files.models import FileRecord
from files.storage import get_store
from proofing.commands import serialize_order
from proofing.models import Order, ConfirmationRequest


class ExportFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def build_manifest(owner_id, tenant_id):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=owner_id, tenant_id=tenant_id)
        data = serialize_order(order)
        frozen_at = timezone.now()
        requests = {str(r.version_id): r for r in ConfirmationRequest.objects.filter(version__order=order).select_related('invitation')}
        for version in data['versions']:
            grant = requests[version['id']].invitation
            version['request']['access_revoked_at'] = grant.revoked_at.isoformat() if grant.revoked_at else None
            version['request']['access_expires_at'] = grant.expires_at.isoformat()
        current = next((v for v in data['versions'] if v['id'] == data['current_version_id']), None)
        request = requests.get(data['current_version_id'])
        complete = bool(current and current['print_review'] and data['status'] == 'confirmed'
                        and request and not request.revoked_at and not request.superseded_at
                        and request.expires_at > frozen_at and not request.invitation.revoked_at
                        and request.invitation.expires_at > frozen_at)
        files = []
        for version in data['versions']:
            asset = FileRecord.objects.get(pk=version['asset_id'], tenant_id=tenant_id, owner=order, purpose='original')
            extension = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg'}.get(asset.content_type, '.bin')
            files.append({'file_id': str(asset.id), 'version_id': version['id'], 'path': f"originals/v{version['sequence']:04d}-{asset.id}{extension}",
                          'display_name': version['display_name'], 'sha256': version['sha256'], 'size': asset.size,
                          'content_type': asset.content_type, 'object_key': asset.object_key})
        return {'schema': 1, 'tenant_id': str(tenant_id), 'owner_id': str(owner_id),
                'frozen_at': frozen_at.isoformat(), 'incomplete': not complete,
                'label': '完整记录（截至快照时间）' if complete else '草稿／不完整',
                'order': data, 'files': files}


def record_html(manifest, images):
    engine = Engine(autoescape=True)
    template = engine.from_string(Path(__file__).with_name('templates').joinpath('record.html').read_text())
    return template.render(Context({'manifest': manifest, 'images': images}, autoescape=True))


def build_archive(snapshot, tenant_id, owner_id):
    from .renderer import render_pdf
    if snapshot['tenant_id'] != str(tenant_id) or snapshot['owner_id'] != str(owner_id):
        raise ExportFailure('snapshot_scope')
    originals, images, total = [], [], 0
    store = get_store()
    for item in snapshot['files']:
        record = FileRecord.objects.filter(pk=item['file_id'], tenant_id=tenant_id, owner_id=owner_id, state='ready', purpose='original', object_key=item['object_key']).first()
        if record is None:
            raise ExportFailure('original_unavailable')
        try:
            with store.open(item['object_key']) as source:
                data = source.read(min(item['size'], settings.EXPORT_MAX_BYTES) + 1)
        except (FileNotFoundError, ValueError):
            raise ExportFailure('original_unavailable')
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ExportFailure('original_unavailable')
        total += len(data)
        if total > settings.EXPORT_MAX_BYTES:
            raise ExportFailure('export_size_limit')
        originals.append((item['path'], data))
        if item['content_type'] in ['image/png', 'image/jpeg'] and len(images) < settings.EXPORT_MAX_IMAGES:
            images.append({'title': item['display_name'], 'uri': 'data:' + item['content_type'] + ';base64,' + base64.b64encode(data).decode()})
    public = {**snapshot, 'files': [{k: v for k, v in f.items() if k != 'object_key'} for f in snapshot['files']]}
    pdf = render_pdf(record_html(public, images))
    public['record_pdf'] = {'path': 'confirmation.pdf', 'sha256': hashlib.sha256(pdf).hexdigest(), 'size': len(pdf)}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in originals:
            archive.writestr(name, data)
        archive.writestr('confirmation.pdf', pdf)
        archive.writestr('manifest.json', json.dumps(public, ensure_ascii=False, sort_keys=True, indent=2).encode())
    result = stream.getvalue()
    if len(result) > settings.EXPORT_MAX_BYTES:
        raise ExportFailure('export_size_limit')
    return result
