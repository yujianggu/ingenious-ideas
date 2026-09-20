import copy
import importlib
import importlib.util
import re
import pytest
from fastapi import HTTPException

def product(code):
    assert importlib.util.find_spec(f'app.products.{code}') is not None, f'{code} workflow is missing'
    return importlib.import_module(f'app.products.{code}')

def storybook():
    c04 = product('c04')
    record = c04.create(dict(title="Mia's adventure", childName='Mia', companion='fox', setting='garden', dedication='For Mia, with love.'))
    record['id'] = 'book-123'
    return record

def order_fields():
    return dict(customerName='Alex Reader', customerEmail='alex@example.com', recipientName='Alex Reader', addressLine1='10 Maple Street', addressLine2='', city='Boston', region='MA', postalCode='02108', country='US', quantity=1, adult=True)

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
    (data, mime, filename) = c04.export(record, 'pdf')
    assert data.startswith(b'%PDF-') and data.rstrip().endswith(b'%%EOF')
    assert mime == 'application/pdf' and filename.endswith('.pdf')
    assert len(re.findall(b'/Type\\s*/Page\\b', data)) == 8
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
    assert record['proofVersion'] == 2 and 'Sam' in record['pages'][1]['text'] and ('Mia' not in record['pages'][1]['text'])
    with pytest.raises(HTTPException) as error:
        c04.act(record, 'edit-page', {'pageId': 'page-2', 'title': 'Unsupported', 'text': 'Hello 🦊'})
    assert error.value.status_code == 422
    record = c04.act(record, 'edit-page', {'pageId': 'page-2', 'title': 'Literal markup', 'text': '<img src="file:///etc/passwd"> & <b>safe text</b>'})
    (data, _, _) = c04.export(record, 'pdf')
    assert b'/Subtype /Image' not in data
