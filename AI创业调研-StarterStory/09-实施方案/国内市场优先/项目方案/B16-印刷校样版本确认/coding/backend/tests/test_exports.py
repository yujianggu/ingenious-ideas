import hashlib
import io
import json
import zipfile
import pytest
from test_stale_confirmation import create, publish, command


def enqueue(api, order, key='export-a', **extra):
    return api.post('/api/exports', {'owner_id': order['id'], **extra}, format='json', HTTP_IDEMPOTENCY_KEY=key)


@pytest.mark.django_db(transaction=True)
def test_export_scope_idempotency_and_draft(api, other_api, settings, tmp_path):
    settings.PRIVATE_LOCAL_ROOT = tmp_path / 'objects'
    order = create(api)
    assert enqueue(other_api, order).status_code == 403
    queued = enqueue(api, order)
    assert queued.status_code == 202
    assert queued.json()['state'] == 'queued'
    assert enqueue(api, order).json() == queued.json()
    assert enqueue(api, order, extra='different').status_code == 409
    jid = queued.json()['job_id']
    assert other_api.get(f'/api/exports/{jid}').status_code == 403
    from jobs.worker import claim_job, run_job
    job = claim_job('one', 120)
    run_job(job.id, lease_token=job.lease_token)
    status = api.get(f'/api/exports/{jid}').json()
    assert status['state'] == 'succeeded', status
    response = api.get(f"/api/files/{status['file_id']}/content")
    archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
    manifest = json.loads(archive.read('manifest.json'))
    assert manifest['incomplete'] is True
    assert manifest['label'] == '草稿／不完整'
    assert archive.read('confirmation.pdf').startswith(b'%PDF-')
    assert other_api.get(f"/api/files/{status['file_id']}/content").status_code == 403


@pytest.mark.django_db(transaction=True)
def test_snapshot_originals_hashes_names_and_recipient_scope(api, ready_file, customer_api):
    from files.models import FileRecord
    from jobs.worker import claim_job, run_job
    order = create(api)
    fid = ready_file(order['id'])
    FileRecord.objects.filter(pk=fid).update(display_name='../../=SUM(1,1).pdf')
    first = publish(api, order, fid)
    guest = customer_api(first['request_id'])
    queued = enqueue(api, order).json()
    assert enqueue(guest, order).status_code in (401, 403)
    command(api, order['id'], 'revoke', first['revision'])
    job = claim_job('one', 120)
    run_job(job.id, lease_token=job.lease_token)
    status = api.get(f"/api/exports/{queued['job_id']}").json()
    assert status['state'] == 'succeeded', status
    assert guest.get(f"/api/exports/{job.id}").status_code in (401, 403)
    assert guest.get(f"/api/files/{status['file_id']}/content").status_code == 403
    response = api.get(f"/api/files/{status['file_id']}/content")
    archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
    manifest = json.loads(archive.read('manifest.json'))
    assert manifest['order']['status'] == 'awaiting_confirmation'
    assert manifest['order']['versions'][0]['request']['revoked_at'] is None
    for item in manifest['files']:
        assert item['path'].startswith('originals/v0001-')
        assert '..' not in item['path'] and '=' not in item['path']
        assert hashlib.sha256(archive.read(item['path'])).hexdigest() == item['sha256']


@pytest.mark.django_db(transaction=True)
def test_missing_original_fails_without_download(api, ready_file):
    from files.models import FileRecord
    from files.storage import get_store
    from jobs.worker import claim_job, run_job
    order = create(api)
    fid = ready_file(order['id'])
    publish(api, order, fid)
    jid = enqueue(api, order).json()['job_id']
    get_store().delete(FileRecord.objects.get(pk=fid).object_key)
    job = claim_job('one', 120)
    run_job(job.id, lease_token=job.lease_token)
    status = api.get(f'/api/exports/{jid}').json()
    assert status == {'job_id': jid, 'state': 'failed', 'file_id': None, 'error_code': 'original_unavailable'}
    assert FileRecord.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_export_command_requires_mfa_csrf_and_key(api):
    from rest_framework.test import APIClient
    order = create(api)
    assert api.post('/api/exports', {'owner_id': order['id']}, format='json').status_code == 422
    strict = APIClient(enforce_csrf_checks=True)
    strict.cookies = api.cookies
    assert enqueue(strict, order).status_code == 403
    session = api.session
    del session['otp_verified_user_id']
    session.save()
    assert enqueue(api, order).status_code == 403


@pytest.mark.django_db(transaction=True)
def test_all_versions_and_pdf_are_hashed(api, ready_file, customer_api):
    from jobs.worker import claim_job, run_job
    from test_stale_confirmation import decide
    order=create(api)
    first=publish(api, order, ready_file(order['id']))
    confirmed=decide(customer_api(first['request_id']),first).json()
    review=command(api, order['id'], 'print-review', confirmed['revision']).json()
    second=publish(api, review, ready_file(order['id']))
    jid=enqueue(api, order).json()['job_id']
    job=claim_job('one',120);run_job(job.id,lease_token=job.lease_token)
    result=api.get(f'/api/exports/{jid}').json()
    assert result['state']=='succeeded',result
    archive=zipfile.ZipFile(io.BytesIO(b''.join(api.get(f"/api/files/{result['file_id']}/content").streaming_content)))
    manifest=json.loads(archive.read('manifest.json'))
    assert len(manifest['files'])==2
    assert manifest['order']['versions'][0]['print_review']['confirmation_id']==confirmed['event_id']
    assert manifest['order']['versions'][0]['request']['superseded_at']
    for item in [*manifest['files'],manifest['record_pdf']]:
        assert hashlib.sha256(archive.read(item['path'])).hexdigest()==item['sha256']
        assert len(archive.read(item['path']))==item['size']
    assert manifest['incomplete'] is True
    assert command(api,order['id'],'publish',second['revision'],asset_id=result['file_id'],recipient='客户').status_code==409


@pytest.mark.django_db(transaction=True)
def test_manifest_marks_revoked_access_incomplete(api, ready_file, customer_api):
    from exports.service import build_manifest
    from proofing.models import Order
    from test_stale_confirmation import decide
    from test_invitations import write
    order=create(api)
    published=publish(api,order,ready_file(order['id']))
    confirmed=decide(customer_api(published['request_id']),published).json()
    assert command(api,order['id'],'print-review',confirmed['revision']).status_code==200
    tenant_id=Order.objects.get(pk=order['id']).tenant_id
    assert build_manifest(order['id'],tenant_id)['incomplete'] is False
    assert write(api,f"/api/access/{order['id']}/revoke").status_code==200
    snapshot=build_manifest(order['id'],tenant_id)
    assert snapshot['incomplete'] is True
    assert snapshot['order']['versions'][0]['request']['access_revoked_at']


@pytest.mark.django_db(transaction=True)
def test_even_exact_export_invitation_cannot_download_whole_history(api, settings, tmp_path):
    from jobs.worker import claim_job, run_job
    from test_invitations import issue, guest_challenge, verify
    from domain_support import TestCodeProvider
    settings.PRIVATE_LOCAL_ROOT=tmp_path/'objects'
    settings.CODE_PROVIDER='domain_support.TestCodeProvider'
    TestCodeProvider.messages.clear()
    order=create(api)
    enqueue(api,order)
    job=claim_job('one',120);run_job(job.id,lease_token=job.lease_token)
    result=api.get(f'/api/exports/{job.id}').json()
    assert result['state']=='succeeded'
    invitation=issue(api,order['id'],result['file_id']).json()
    guest,code=guest_challenge(invitation['token'],TestCodeProvider)
    assert verify(guest,invitation['token'],code).status_code==200
    assert guest.get(f"/api/files/{result['file_id']}/content").status_code==403
    assert guest.get(f"/api/files/{result['file_id']}/content",HTTP_RANGE='bytes=0-5').status_code==403
    assert guest.get(f'/api/exports/{job.id}').status_code in (401,403)
