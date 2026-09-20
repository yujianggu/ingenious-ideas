import hashlib
import io
import os
import uuid
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

DATA = Path(__file__).parent / 'test-data'

@pytest.fixture(autouse=True)
def private_config(settings, tmp_path):
    settings.PRIVATE_STORE = 'local'
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    settings.FILE_SCANNER_COMMAND = []


def order(api):
    r = api.post('/api/orders', {'title': '文件任务'}, format='json', HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert r.status_code == 201
    return r.json()['id']


def upload(api, owner_id, name='valid.pdf', raw=None, key=None):
    return api.post('/api/files', {'owner_id': owner_id, 'file': SimpleUploadedFile(name, raw if raw is not None else (DATA / 'valid.pdf').read_bytes(), content_type='application/octet-stream')}, HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))


def validate(api, file_id, key=None):
    return api.post(f'/api/files/{file_id}/validate', {}, format='json', HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

@pytest.fixture
def clean_scanner(settings, tmp_path):
    # Explicit process adapter fixture; actual ClamAV is exercised separately.
    import sys
    script = tmp_path / 'clean.py'
    script.write_text('import sys\nfrom pathlib import Path\nassert Path(sys.argv[1]).is_file()\nsys.exit(0)\n')
    settings.FILE_SCANNER_COMMAND = [sys.executable, str(script), '{file}']

@pytest.mark.django_db
def test_private_upload_and_cross_tenant_denial(api, other_api):
    owner_id = order(api)
    raw = b'%PDF-1.4\n%%EOF'
    up = upload(api, owner_id, '../相同.pdf', raw)
    assert up.status_code == 201
    file_id = up.json()['id']
    assert up.json()['state'] == 'quarantined'
    assert up.json()['sha256'] == hashlib.sha256(raw).hexdigest()
    assert up.json()['size'] == len(raw)
    assert '/' not in up.json()['display_name']
    assert other_api.get(f'/api/files/{file_id}/content').status_code == 403
    assert api.get(f'/api/files/{file_id}/content').status_code == 409
    assert upload(other_api, owner_id).status_code == 403
    assert validate(other_api, file_id).status_code == 403

@pytest.mark.django_db
def test_upload_replay_same_name_distinct_ids_and_immutable_store(api, settings):
    from files.storage import get_store
    from files.models import FileRecord
    oid = order(api)
    first = upload(api, oid, key='repeat').json()
    assert upload(api, oid, key='repeat').json() == first
    assert upload(api, oid, raw=b'changed', key='repeat').status_code == 409
    second = upload(api, oid).json()
    assert first['id'] != second['id']
    assert FileRecord.objects.count() == 2
    record = FileRecord.objects.get(id=first['id'])
    with pytest.raises(FileExistsError):
        get_store().put(record.object_key, io.BytesIO(b'overwrite'))
    assert list(settings.PRIVATE_LOCAL_ROOT.iterdir())

@pytest.mark.django_db
def test_upload_size_limit_mfa_and_missing_key(api, settings):
    oid = order(api)
    settings.MAX_UPLOAD_BYTES = 10
    assert upload(api, oid).status_code == 422
    settings.MAX_UPLOAD_BYTES = 1024 * 1024
    assert api.post('/api/files', {'owner_id': oid, 'file': SimpleUploadedFile('a.pdf', b'data')}).status_code == 422
    session = api.session
    session.pop('otp_verified_user_id')
    session.save()
    assert upload(api, oid).status_code == 403

@pytest.mark.django_db
@pytest.mark.parametrize('name', ['valid.pdf', 'valid.png'])
def test_real_formats_ready_download_and_ranges(api, clean_scanner, name):
    raw = (DATA / name).read_bytes()
    up = upload(api, order(api), name, raw).json()
    r = validate(api, up['id'], key='validate')
    assert r.status_code == 200
    assert r.json()['state'] == 'ready'
    assert validate(api, up['id'], key='validate').json() == r.json()
    url = f"/api/files/{up['id']}/content"
    result = api.get(url)
    assert result.status_code == 200
    assert b''.join(result.streaming_content) == raw
    assert result['Cache-Control'] == 'private, no-store'
    assert result['X-Content-Type-Options'] == 'nosniff'
    for header, expected in [('bytes=0-7', raw[:8]), ('bytes=-7', raw[-7:]), ('bytes=8-', raw[8:])]:
        ranged = api.get(url, HTTP_RANGE=header)
        assert ranged.status_code == 206
        assert b''.join(ranged.streaming_content) == expected
    for header in ['bytes=999999-', 'bytes=7-1', 'bytes=0-1,3-4', 'garbage']:
        assert api.get(url, HTTP_RANGE=header).status_code == 416

@pytest.mark.django_db
@pytest.mark.parametrize('name,raw', [('fake.png', b'%PDF-1.4\n%%EOF'), ('broken.pdf', b'%PDF-1.4\n%%EOF'), ('fake.exe', b'MZbad'), ('fake.pdf', (DATA / 'valid.png').read_bytes())])
def test_invalid_format_rejected(api, clean_scanner, name, raw):
    up = upload(api, order(api), name, raw).json()
    assert validate(api, up['id']).json()['state'] == 'rejected'
    assert api.get(f"/api/files/{up['id']}/content").status_code == 409

@pytest.mark.django_db
@pytest.mark.parametrize('damage', ['missing', 'digest', 'size'])
def test_stored_object_integrity_checked(api, clean_scanner, damage):
    from files.models import FileRecord
    from files.storage import get_store
    up = upload(api, order(api)).json()
    record = FileRecord.objects.get(id=up['id'])
    if damage == 'missing':
        get_store().delete(record.object_key)
    else:
        FileRecord.objects.filter(pk=record.pk).update(**({'sha256': '0' * 64} if damage == 'digest' else {'size': 1}))
    assert validate(api, up['id']).json()['state'] == 'rejected'

@pytest.mark.django_db
def test_scanner_unavailable_quarantines_and_retry_recovers(api, clean_scanner, settings):
    up = upload(api, order(api)).json()
    clean_command = settings.FILE_SCANNER_COMMAND
    settings.FILE_SCANNER_COMMAND = ['/nonexistent-scanner', '{file}']
    r = validate(api, up['id'], key='scan')
    assert r.status_code == 503
    assert api.get(f"/api/files/{up['id']}/content").status_code == 409
    settings.FILE_SCANNER_COMMAND = clean_command
    assert validate(api, up['id'], key='scan').json()['state'] == 'ready'

@pytest.mark.django_db
def test_detected_scan_rejects_file(api, settings, tmp_path):
    import sys
    settings.FILE_SCANNER_COMMAND = [sys.executable, '-c', 'import sys; sys.exit(1)', '{file}']
    up = upload(api, order(api)).json()
    assert validate(api, up['id']).json()['state'] == 'rejected'

@pytest.mark.django_db
def test_upload_csrf_enforced(owner, tenant):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(owner)
    session = client.session
    session.update({'active_tenant': str(tenant.id), 'otp_verified_user_id': owner.id})
    session.save()
    assert upload(client, str(uuid.uuid4())).status_code == 403


def test_local_store_prevents_traversal_and_private_permissions(settings):
    from files.storage import get_store
    store = get_store()
    with pytest.raises(ValueError):
        store.put('../escape', io.BytesIO(b'bytes'))
    key = uuid.uuid4().hex
    store.put(key, io.BytesIO(b'private'))
    assert (settings.PRIVATE_LOCAL_ROOT / key).stat().st_mode & 0o777 == 0o600
    assert settings.PRIVATE_LOCAL_ROOT.stat().st_mode & 0o777 == 0o700
    with store.open(key) as stream:
        assert stream.read() == b'private'
    store.delete(key)
    with pytest.raises(FileNotFoundError):
        store.open(key)

@pytest.mark.skipif(not os.environ.get('B16_TEST_SCANNER_COMMAND'), reason='real scanner explicitly configured only')
def test_real_clamav_clean_eicar_and_unavailable(settings, tmp_path):
    import json
    from files.validation import scan_file, ScanUnavailable
    settings.FILE_SCANNER_COMMAND = json.loads(os.environ['B16_TEST_SCANNER_COMMAND'])
    assert scan_file(DATA / 'valid.pdf') == 'clean'
    sample = tmp_path / 'eicar.txt'
    sample.write_bytes(b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*')
    assert scan_file(sample) == 'infected'
    settings.FILE_SCANNER_COMMAND = ['/missing/scanner', '{file}']
    with pytest.raises(ScanUnavailable):
        scan_file(sample)

@pytest.mark.django_db
def test_download_denial_is_never_cacheable(api, other_api):
    file_id = upload(api, order(api)).json()['id']
    response = other_api.get(f'/api/files/{file_id}/content', HTTP_RANGE='bytes=0-4')
    assert response.status_code == 403
    assert response['Cache-Control'] == 'private, no-store'

@pytest.mark.django_db
@pytest.mark.skipif(not os.environ.get('B16_TEST_SCANNER_COMMAND'), reason='real scanner explicitly configured only')
def test_real_scanner_upload_validation_lifecycle(api, settings):
    import json
    settings.FILE_SCANNER_COMMAND = json.loads(os.environ['B16_TEST_SCANNER_COMMAND'])
    oid = order(api)
    for name in ['valid.pdf', 'valid.png']:
        up = upload(api, oid, name, (DATA / name).read_bytes()).json()
        assert validate(api, up['id']).json()['state'] == 'ready'
    eicar_pdf = b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
    assert len(eicar_pdf) == 68  # EICAR specifies the exact 68-byte standalone sample.
    up = upload(api, oid, 'infected.pdf', eicar_pdf).json()
    result = validate(api, up['id']).json()
    assert result['state'] == 'rejected'
    assert result['failure_reason'] == 'malware_detected'

@pytest.mark.django_db
def test_failed_upload_ack_does_not_leave_unreferenced_object(api, settings, monkeypatch):
    from files.models import FileRecord
    from files.storage import LocalPrivateStore
    original = LocalPrivateStore.put
    def lose_ack(store, key, stream):
        original(store, key, stream)
        raise OSError('simulated lost storage acknowledgement')
    monkeypatch.setattr(LocalPrivateStore, 'put', lose_ack)
    with pytest.raises(OSError):
        upload(api, order(api))
    assert not FileRecord.objects.exists()
    assert list(settings.PRIVATE_LOCAL_ROOT.iterdir()) == []
