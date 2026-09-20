import pytest
from rest_framework.test import APIClient
from identity.models import Membership, Tenant, User

@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="甲印刷店")

@pytest.fixture
def other_tenant(db):
    return Tenant.objects.create(name="乙印刷店")

def verified_client(user, tenant):
    client = APIClient()
    client.force_login(user)
    session = client.session
    session["active_tenant"] = str(tenant.id)
    session["otp_verified_user_id"] = user.id
    session.save()
    return client

@pytest.fixture
def owner(db, tenant):
    user = User.objects.create_user("owner", password="correct-horse")
    Membership.objects.create(user=user, tenant=tenant, roles=["owner"])
    return user

@pytest.fixture
def other_owner(db, other_tenant):
    user = User.objects.create_user("other-owner", password="correct-horse")
    Membership.objects.create(user=user, tenant=other_tenant, roles=["owner"])
    return user

@pytest.fixture
def api(owner, tenant):
    return verified_client(owner, tenant)

@pytest.fixture
def other_api(other_owner, other_tenant):
    return verified_client(other_owner, other_tenant)


@pytest.fixture
def ready_file(api, settings, tmp_path, monkeypatch):
    """Real upload + hash + parser, caching only real AV verdicts for identical bytes/DB."""
    import hashlib
    import json
    import os
    from pathlib import Path
    from files import validation
    from test_files import upload, validate
    settings.PRIVATE_STORE = 'local'
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    settings.FILE_SCAN_TIMEOUT = 180
    root = Path.home() / '.local/share/ingenious-ideas/toolchains/clamav'
    settings.FILE_SCANNER_COMMAND = json.loads(os.environ.get('B16_TEST_SCANNER_COMMAND', 'null')) or [str(root / 'native-adapter/scan'), str(root / 'database'), '{file}']
    command = tuple(settings.FILE_SCANNER_COMMAND)
    default_database = root / 'database' if not os.environ.get('B16_TEST_SCANNER_COMMAND') else tmp_path / 'unknown-scanner-db'
    database = Path(os.environ.get('B16_TEST_SCANNER_DB_DIR', default_database))
    # Database version fingerprint invalidates the session cache on updates.
    version = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns, _database_header(p)) for p in sorted(database.glob('*.cvd')))
    if not version and database.exists():
        version = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(database.iterdir()) if p.is_file())
    real_scan = validation.scan_file
    def cached_scan(path):
        key = (hashlib.sha256(Path(path).read_bytes()).hexdigest(), command, version)
        if not version:
            return real_scan(path)
        if key not in _real_scan_cache:
            _real_scan_cache[key] = real_scan(path)
        return _real_scan_cache[key]
    monkeypatch.setattr(validation, 'scan_file', cached_scan)
    def create(owner_id):
        result = upload(api, owner_id)
        assert result.status_code == 201, result.data
        fid = result.json()['id']
        checked = validate(api, fid)
        assert checked.status_code == 200 and checked.json()['state'] == 'ready', checked.data
        return fid
    return create


def _database_header(path):
    with path.open('rb') as source:
        return source.read(512)


_real_scan_cache = {}


@pytest.fixture
def customer_api(settings):
    from domain_support import TestCodeProvider
    from identity.invitations import token_for
    from test_invitations import guest_challenge, verify
    settings.CODE_PROVIDER = 'domain_support.TestCodeProvider'
    TestCodeProvider.messages.clear()
    def create(request_id):
        from proofing.models import ConfirmationRequest
        confirmation = ConfirmationRequest.objects.select_related('invitation').get(pk=request_id)
        token = token_for(confirmation.invitation_id)
        client, code = guest_challenge(token, TestCodeProvider, confirmation.recipient)
        result = verify(client, token, code, confirmation.recipient)
        assert result.status_code == 200, result.data
        assert result.json()['principal']['kind'] == 'external_recipient'
        return client
    return create
