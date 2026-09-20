"""Opt-in real cloud contract. Never replaced by an in-memory OSS double."""
import io
import os
import uuid
from urllib.error import HTTPError
from urllib.request import urlopen
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_OSS_CONTRACT') != '1' or not all(os.environ.get(k) for k in ['B16_OSS_ENDPOINT', 'B16_OSS_BUCKET', 'B16_OSS_REGION', 'OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET']), reason='OSS private test bucket credentials absent; real cloud contract 未执行')

@pytest.mark.django_db
def test_real_private_oss_put_get_and_revoked_gateway(api, settings, tmp_path):
    from files.storage import OssPrivateStore
    from files.models import FileRecord
    from identity.code_providers import DevelopmentCodeProvider
    from test_files import order, upload, DATA
    from test_invitations import issue, guest_challenge, verify, write
    settings.PRIVATE_STORE = 'oss'
    for name in ['OSS_ENDPOINT', 'OSS_BUCKET', 'OSS_REGION']:
        setattr(settings, name, os.environ['B16_' + name])
    settings.CODE_PROVIDER = 'identity.code_providers.DevelopmentCodeProvider'
    store = OssPrivateStore()
    key = uuid.uuid4().hex
    record_key = None
    try:
        store.put(key, io.BytesIO(b'private contract'))
        with store.open(key) as stream:
            assert stream.read() == b'private contract'
        with pytest.raises(FileExistsError):
            store.put(key, io.BytesIO(b'overwrite forbidden'))
        host = settings.OSS_ENDPOINT.removeprefix('https://')
        with pytest.raises(HTTPError) as exc:
            urlopen(f'https://{settings.OSS_BUCKET}.{host}/{key}', timeout=10)
        assert exc.value.code == 403
        oid = order(api)
        uploaded = upload(api, oid).json()
        record = FileRecord.objects.get(pk=uploaded['id'])
        record_key = record.object_key
        # This cloud contract isolates storage/access revocation; format+AV have separate real integration tests.
        record.state = 'ready'
        record.content_type = 'application/pdf'
        record.save(update_fields=['state', 'content_type'])
        invitation = issue(api, oid, str(record.id)).json()
        guest, code = guest_challenge(invitation['token'], DevelopmentCodeProvider)
        assert verify(guest, invitation['token'], code).status_code == 200
        response = guest.get(f'/api/files/{record.id}/content')
        assert b''.join(response.streaming_content) == (DATA / 'valid.pdf').read_bytes()
        assert write(api, f'/api/access/{oid}/revoke').status_code == 200
        assert guest.get(f'/api/files/{record.id}/content').status_code == 403
        assert guest.get(f'/api/files/{record.id}/content', HTTP_RANGE='bytes=0-9').status_code == 403
    finally:
        store.delete(key)
        if record_key:
            store.delete(record_key)
    with pytest.raises(FileNotFoundError):
        store.open(key)
