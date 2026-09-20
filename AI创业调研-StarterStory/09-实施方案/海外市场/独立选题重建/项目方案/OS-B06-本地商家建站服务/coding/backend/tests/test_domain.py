import copy
import importlib
import importlib.util
import re
import pytest
from fastapi import HTTPException

def product(code):
    assert importlib.util.find_spec(f'app.products.{code}') is not None, f'{code} workflow is missing'
    return importlib.import_module(f'app.products.{code}')

def cleaning_site():
    b06 = product('b06')
    record = b06.create(dict(title='Bright & Kind', email='hello@example.com', phone='+1 212 555 0100', tagline='A welcoming home', about='A local cleaning team.', areas='Brooklyn\nQueens'))
    record['id'] = 'site-123'
    return b06.act(record, 'add-service', dict(title='Home cleaning', description='Kitchen and bathroom care.', priceNote='Request a quote'))

def test_b06_publish_snapshot_hides_later_draft_changes_and_unpublishes():
    b06 = product('b06')
    record = cleaning_site()
    with pytest.raises(HTTPException) as e:
        b06.public_view(record)
    assert e.value.status_code == 404
    record = b06.act(record, 'publish', dict(publicConsent=True))
    record = b06.act(record, 'update-site', {'title': 'New draft business'})
    assert 'Bright &amp; Kind' in b06.public_view(record)
    assert 'New draft business' not in b06.public_view(record)
    (html, mime, filename) = b06.export(record, 'html')
    assert b'New draft business' in html and mime == 'text/html' and filename.endswith('.html')
    record = b06.act(record, 'publish', dict(publicConsent=True))
    assert 'New draft business' in b06.public_view(record)
    record = b06.act(record, 'unpublish', {})
    with pytest.raises(HTTPException):
        b06.public_view(record)

def test_b06_escapes_all_untrusted_public_copy():
    b06 = product('b06')
    record = cleaning_site()
    record = b06.act(record, 'update-site', {'title': '<script>alert(1)</script>', 'about': '<img src=x onerror=alert(1)>'})
    record = b06.act(record, 'add-service', dict(title='" onfocus="bad', description='<svg onload=bad>', priceNote='A&B'))
    record = b06.act(record, 'publish', dict(publicConsent=True))
    rendered = b06.public_view(record)
    assert '<script>alert' not in rendered and '<img src=x' not in rendered and ('<svg onload=bad>' not in rendered)
    assert '&lt;script&gt;' in rendered and '&lt;svg' in rendered
    assert '/api/public/sites/site-123/inquiries' in rendered

def test_b06_real_inquiries_validate_published_service_consent_and_followup():
    b06 = product('b06')
    record = cleaning_site()
    record = b06.act(record, 'publish', dict(publicConsent=True))
    fields = dict(name='Taylor Client', email='taylor@example.com', phone='', area='Queens', serviceId=record['site']['services'][0]['id'], message='Please quote a weekly clean.', consent=True)
    for invalid in ({'consent': False}, {'serviceId': 'unknown'}, {'email': 'not-email'}, {'message': 'x'}, {'website': 'bot'}):
        with pytest.raises(HTTPException):
            b06.public_action(copy.deepcopy(record), {**fields, **invalid})
    record = b06.public_action(record, fields)
    inquiry = record['inquiries'][0]
    assert inquiry['serviceTitle'] == 'Home cleaning' and inquiry['status'] == 'new'
    assert inquiry['email'] == 'taylor@example.com'
    assert 'taylor@example.com' not in b06.public_view(record)
    record = b06.act(record, 'follow-up', dict(inquiryId=inquiry['id'], status='contacted', note='Called customer; quote requested.'))
    assert record['inquiries'][0]['status'] == 'contacted'
    assert record['inquiries'][0]['history'][0]['note'] == 'Called customer; quote requested.'
    record = b06.act(record, 'unpublish', {})
    with pytest.raises(HTTPException):
        b06.public_action(record, fields)

def test_b06_service_edit_remove_and_publish_validation():
    b06 = product('b06')
    record = cleaning_site()
    sid = record['site']['services'][0]['id']
    record = b06.act(record, 'update-service', dict(serviceId=sid, title='Deep clean', description='Detailed cleaning.', priceNote='Quote first'))
    assert record['site']['services'][0]['title'] == 'Deep clean'
    record = b06.act(record, 'remove-service', {'serviceId': sid})
    with pytest.raises(HTTPException):
        b06.act(record, 'publish', dict(publicConsent=True))
    with pytest.raises(HTTPException):
        b06.act(record, 'update-site', {'email': 'javascript:alert(1)'})

def test_b06_export_cannot_trigger_spreadsheet_formula_and_snapshot_inquiry_stays_valid():
    b06 = product('b06')
    record = cleaning_site()
    record = b06.act(record, 'publish', {'publicConsent': True})
    sid = record['site']['services'][0]['id']
    record = b06.act(record, 'remove-service', {'serviceId': sid})
    record = b06.public_action(record, dict(name='=SUM(1,1)', email='taylor@example.com', area='Queens', serviceId=sid, message='Please quote a clean.', consent='on'))
    (data, mime, filename) = b06.export(record, 'csv')
    assert "'=SUM(1,1)" in data.decode('utf-8-sig') and mime == 'text/csv'
    (static, _, _) = b06.export(record, 'html')
    assert b'<form' not in static and b'mailto:' in static
