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
    record = b06.create(dict(title='Bright & Kind', email='hello@example.com', phone='+1 212 555 0100',
                             tagline='A welcoming home', about='A local cleaning team.', areas='Brooklyn\nQueens'))
    record['id'] = 'site-123'
    return b06.act(record, 'add-service', dict(title='Home cleaning', description='Kitchen and bathroom care.', priceNote='Request a quote'))


def storybook():
    c04 = product('c04')
    record = c04.create(dict(title="Mia's adventure", childName='Mia', companion='fox', setting='garden', dedication='For Mia, with love.'))
    record['id'] = 'book-123'
    return record


def order_fields():
    return dict(customerName='Alex Reader', customerEmail='alex@example.com', recipientName='Alex Reader',
                addressLine1='10 Maple Street', addressLine2='', city='Boston', region='MA', postalCode='02108',
                country='US', quantity=1, adult=True)


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
    html, mime, filename = b06.export(record, 'html')
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
    assert '<script>alert' not in rendered and '<img src=x' not in rendered and '<svg onload=bad>' not in rendered
    assert '&lt;script&gt;' in rendered and '&lt;svg' in rendered
    assert '/api/public/sites/site-123/inquiries' in rendered


def test_b06_real_inquiries_validate_published_service_consent_and_followup():
    b06 = product('b06')
    record = cleaning_site()
    record = b06.act(record, 'publish', dict(publicConsent=True))
    fields = dict(name='Taylor Client', email='taylor@example.com', phone='', area='Queens',
                  serviceId=record['site']['services'][0]['id'], message='Please quote a weekly clean.', consent=True)
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


def test_c04_finite_story_personalization_and_editing_invalidate_proof():
    c04 = product('c04')
    record = storybook()
    assert len(record['pages']) == 8 and 'Mia' in record['pages'][1]['text']
    with pytest.raises(HTTPException):
        c04.create(dict(title='Book', childName='Mia', companion='dragon', setting='garden'))
    record = c04.act(record, 'approve-proof', {'adult': True, 'reviewedAllPages': True, 'proofVersion': 1, 'approverName': 'Alex Reader'})
    assert record['approval']['proofVersion'] == 1
    record = c04.act(record, 'edit-page', {'pageId': 'page-2', 'title': 'A bright morning', 'text': 'Mia found a tiny fox beside the flowers.'})
    assert record['proofVersion'] == 2 and record['approval'] is None
    with pytest.raises(HTTPException):
        c04.act(record, 'approve-proof', {'adult': True, 'reviewedAllPages': True, 'proofVersion': 1, 'approverName': 'Alex'})


def test_c04_order_requires_current_adult_proof_and_real_fulfillment_references():
    c04 = product('c04')
    record = storybook()
    with pytest.raises(HTTPException):
        c04.act(record, 'place-order', order_fields())
    with pytest.raises(HTTPException):
        c04.act(record, 'approve-proof', {'adult': False, 'reviewedAllPages': True, 'proofVersion': 1, 'approverName': 'Alex'})
    record = c04.act(record, 'approve-proof', {'adult': True, 'reviewedAllPages': True, 'proofVersion': 1, 'approverName': 'Alex'})
    record = c04.act(record, 'place-order', order_fields())
    assert record['order']['status'] == 'awaiting_print' and record['order']['proofVersion'] == 1
    with pytest.raises(HTTPException):
        c04.act(record, 'record-print', {'provider': 'Local Print', 'providerReference': '', 'completed': True})
    with pytest.raises(HTTPException):
        c04.act(record, 'record-shipment', {'carrier': 'UPS', 'trackingNumber': 'REAL123', 'handedToCarrier': True})
    record = c04.act(record, 'edit-page', {'pageId': 'page-2', 'title': 'A new day', 'text': 'Mia found a small fox at the garden gate.'})
    assert record['order']['status'] == 'proof_changed'
    with pytest.raises(HTTPException):
        c04.act(record, 'record-print', {'provider': 'Local Print', 'providerReference': 'INV-42', 'completed': True})
    record = c04.act(record, 'approve-proof', {'adult': True, 'reviewedAllPages': True, 'proofVersion': 2, 'approverName': 'Alex'})
    record = c04.act(record, 'place-order', order_fields())
    record = c04.act(record, 'record-print', {'provider': 'Local Print', 'providerReference': 'INV-42', 'completed': True})
    assert record['order']['status'] == 'printed'
    with pytest.raises(HTTPException):
        c04.act(record, 'edit-page', {'pageId': 'page-2', 'title': 'Changed', 'text': 'A changed story.'})
    with pytest.raises(HTTPException):
        c04.act(record, 'record-shipment', {'carrier': 'UPS', 'trackingNumber': '', 'handedToCarrier': True})
    record = c04.act(record, 'record-shipment', {'carrier': 'UPS', 'trackingNumber': 'REAL123', 'handedToCarrier': True})
    assert record['order']['status'] == 'shipped'
    assert record['order']['trackingNumber'] == 'REAL123'
    assert record['orderSummary']['status'] == 'shipped'
    assert record['orderSummary']['trackingNumber'] == 'REAL123'
    assert 'pages' not in record['orderSummary'] and 'proofHash' not in record['orderSummary']


def test_c04_pdf_has_all_pages_personalized_text_and_proof_version():
    c04 = product('c04')
    record = storybook()
    data, mime, filename = c04.export(record, 'pdf')
    assert data.startswith(b'%PDF-') and data.rstrip().endswith(b'%%EOF')
    assert mime == 'application/pdf' and filename.endswith('.pdf')
    assert len(re.findall(rb'/Type\s*/Page\b', data)) == 8
    assert b'Mia' in data and b'PROOF 1' in data
    with pytest.raises(HTTPException):
        c04.act(record, 'edit-page', {'pageId': 'page-2', 'title': 'Too long', 'text': 'word ' * 1000})


def test_c04_payment_receipt_is_explicit_manual_record_and_not_automatic_charge():
    c04 = product('c04')
    record = storybook()
    record = c04.act(record, 'approve-proof', {'adult': True, 'reviewedAllPages': True, 'proofVersion': 1, 'approverName': 'Alex'})
    record = c04.act(record, 'place-order', order_fields())
    assert record['order']['paymentStatus'] == 'not_recorded'
    with pytest.raises(HTTPException):
        c04.act(record, 'record-payment', {'amount': 30, 'currency': 'USD', 'receiptReference': '', 'received': True})
    record = c04.act(record, 'record-payment', {'amount': 30, 'currency': 'USD', 'receiptReference': 'BANK-123', 'received': True})
    assert record['order']['paymentStatus'] == 'recorded_manually'
    assert record['order']['payment']['receiptReference'] == 'BANK-123'


def test_c04_rejects_oversized_payment_without_internal_error():
    c04 = product('c04')
    record = storybook()
    record = c04.act(record, 'approve-proof', {'adult': True, 'reviewedAllPages': True, 'proofVersion': 1, 'approverName': 'Alex'})
    record = c04.act(record, 'place-order', order_fields())
    with pytest.raises(HTTPException) as error:
        c04.act(record, 'record-payment', {'amount': 10 ** 1000, 'currency': 'USD', 'receiptReference': 'BANK-123', 'received': True})
    assert error.value.status_code == 422


def test_c04_cancel_and_reorder_preserve_real_payment_history():
    c04 = product('c04')
    record = storybook()
    record = c04.act(record, 'approve-proof', {'adult': True, 'reviewedAllPages': True, 'proofVersion': 1, 'approverName': 'Alex'})
    record = c04.act(record, 'place-order', order_fields())
    record = c04.act(record, 'record-payment', {'amount': 30, 'currency': 'USD', 'receiptReference': 'BANK-123', 'received': True})
    with pytest.raises(HTTPException):
        c04.act(record, 'record-payment', {'amount': 40, 'currency': 'USD', 'receiptReference': 'BANK-456', 'received': True})
    record = c04.act(record, 'cancel-order', {'reason': 'Customer changed recipient.'})
    assert record['order']['payment']['receiptReference'] == 'BANK-123'
    old_id = record['order']['id']
    record = c04.act(record, 'place-order', order_fields())
    assert record['order']['id'] != old_id
    assert record['order']['paymentStatus'] == 'not_recorded'
    assert record['orderHistory'][0]['payment']['receiptReference'] == 'BANK-123'


def test_c04_reset_requires_confirmation_and_unsupported_glyphs_fail_cleanly():
    c04 = product('c04')
    record = storybook()
    personalization = dict(title='A day with Sam', childName='Sam', companion='owl', setting='forest', dedication='From Alex')
    with pytest.raises(HTTPException):
        c04.act(record, 'personalize', personalization)
    record = c04.act(record, 'personalize', {**personalization, 'replacePages': True})
    assert record['proofVersion'] == 2 and 'Sam' in record['pages'][1]['text'] and 'Mia' not in record['pages'][1]['text']
    with pytest.raises(HTTPException) as error:
        c04.act(record, 'edit-page', {'pageId': 'page-2', 'title': 'Unsupported', 'text': 'Hello 🦊'})
    assert error.value.status_code == 422
    record = c04.act(record, 'edit-page', {'pageId': 'page-2', 'title': 'Literal markup', 'text': '<img src="file:///etc/passwd"> & <b>safe text</b>'})
    data, _, _ = c04.export(record, 'pdf')
    assert b'/Subtype /Image' not in data


def test_b06_export_cannot_trigger_spreadsheet_formula_and_snapshot_inquiry_stays_valid():
    b06 = product('b06')
    record = cleaning_site()
    record = b06.act(record, 'publish', {'publicConsent': True})
    sid = record['site']['services'][0]['id']
    record = b06.act(record, 'remove-service', {'serviceId': sid})
    record = b06.public_action(record, dict(name='=SUM(1,1)', email='taylor@example.com', area='Queens',
                                         serviceId=sid, message='Please quote a clean.', consent='on'))
    data, mime, filename = b06.export(record, 'csv')
    assert "'=SUM(1,1)" in data.decode('utf-8-sig') and mime == 'text/csv'
    static, _, _ = b06.export(record, 'html')
    assert b'<form' not in static and b'mailto:' in static
