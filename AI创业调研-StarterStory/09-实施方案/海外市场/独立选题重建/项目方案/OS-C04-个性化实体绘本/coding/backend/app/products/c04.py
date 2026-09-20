"""A fixed, personalizable English story and a truthful manual print-order ledger."""
import copy
import hashlib
import io
import json
import math
import re
from html import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from app.store import fail, ident, now, require

COMPANIONS = ('fox', 'rabbit', 'owl')
SETTINGS = ('garden', 'forest', 'seaside')
TITLE_STYLE = ParagraphStyle('StoryTitle', fontName='Helvetica-Bold', fontSize=29, leading=34, textColor=HexColor('#224b46'))
TEXT_STYLE = ParagraphStyle('StoryText', fontName='Helvetica', fontSize=18, leading=28, textColor=HexColor('#334744'))


def _text(value, label, limit=200, required=True, printable=False):
    require(isinstance(value, str), f'{label} must be text')
    value = value.strip()
    require((bool(value) or not required) and len(value) <= limit, f'{label} must contain {"1" if required else "0"}-{limit} characters')
    require(not any(ord(ch) < 32 and ch not in '\n\t' for ch in value), f'{label} contains unsupported characters')
    if printable:
        try:
            value.encode('cp1252')
        except UnicodeEncodeError:
            fail(422, f'{label} supports English and Western European letters in this edition')
    return value


def _paragraph(text, style):
    return Paragraph(escape(text).replace('\n', '<br/>'), style)


def _validate_page(title, text):
    title = _text(title, 'Page title', 80, printable=True)
    text = _text(text, 'Page text', 1000, printable=True)
    title_height = _paragraph(title, TITLE_STYLE).wrap(484, 500)[1]
    text_height = _paragraph(text, TEXT_STYLE).wrap(484, 500)[1]
    require(title_height + 24 + text_height <= 382, 'This page is too full for the print layout; shorten the title or text')
    return title, text


def _personalization(body):
    name = _text(body.get('childName'), "Child's first name", 30, printable=True)
    require('\n' not in name and '\t' not in name, "Enter the child's first name on one line")
    companion, setting = body.get('companion'), body.get('setting')
    require(companion in COMPANIONS, 'Choose fox, rabbit, or owl')
    require(setting in SETTINGS, 'Choose garden, forest, or seaside')
    return dict(childName=name, companion=companion, setting=setting,
                dedication=_text(body.get('dedication', ''), 'Dedication', 200, False, True))


def _pages(title, personalization):
    name, friend, setting = (personalization[k] for k in ('childName', 'companion', 'setting'))
    place = dict(garden='garden gate', forest='forest path', seaside='seaside dunes')[setting]
    entries = [
        (title, f"A little story about {name}, a {friend}, and the courage to help.\n\n" + personalization['dedication']),
        ('A small hello', f"One bright morning, {name} set out for the {place}. Beside the path sat a little {friend}, looking carefully at a folded map.\n\n'Hello,' said {name}. 'Would you like some company?'"),
        ('The missing star', f"The {friend} pointed to a paper star caught beyond a narrow stream. It belonged on a welcome sign for the evening gathering.\n\n'We can work it out together,' said {name}."),
        ('A moment to notice', f"{name} paused to look around. There were smooth stones, fallen branches, and a clear path along the bank.\n\nThe friends followed the path until they found a small wooden bridge. Careful steps carried them safely across."),
        ('One kind idea', f"The paper star rested on a low branch. {name} held the branch steady while the {friend} gently lifted the star free.\n\nIt had a little tear, but the friends had an idea: a small paper patch could make it strong again."),
        ('Made together', f"Back at the {place}, {name} and the {friend} mended the star. They added bright paper circles and wrote a simple message beneath it:\n\n'You are welcome here.'"),
        ('A place for everyone', f"As the evening grew golden, neighbors arrived at the gathering. The mended star shone above the welcome sign.\n\n{ name} smiled. The best part of the adventure was not finding the star. It was finding a friend."),
        ('Your kindness goes with you', f"That night, {name} remembered the careful steps, the patient pause, and the little paper patch.\n\nSmall acts of kindness can make a big difference. Tomorrow would bring another chance to help.\n\nThe end."),
    ]
    result = []
    for index, (page_title, text) in enumerate(entries, 1):
        page_title, text = _validate_page(page_title, text)
        result.append(dict(id=f'page-{index}', number=index, title=page_title, text=text))
    return result


def create(body):
    title = _text(body.get('title'), 'Book title', 80, printable=True)
    personalization = _personalization(body)
    return dict(title=title, personalization=personalization, pages=_pages(title, personalization),
                proofVersion=1, approval=None, order=None, orderSummary=None, orderHistory=[],
                source='Original fixed English story template: The Welcome Star, edition 1. No AI or image generation.',
                fulfillmentNotice='PDFs are review files. Printing, payment, and shipping are recorded manually from real external receipts.')


def _hash(record):
    return hashlib.sha256(json.dumps(record['pages'], ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def _editable(record):
    require(not record['order'] or record['order']['status'] not in ('printed', 'shipped'), 'This book has entered fulfillment; create a new book for a different edition')


def _invalidate(record):
    record['proofVersion'] += 1
    record['approval'] = None
    if record['order'] and record['order']['status'] != 'cancelled':
        record['order']['status'] = 'proof_changed'


def _approved(record):
    approval = record['approval']
    require(approval and approval['proofVersion'] == record['proofVersion'] and approval['proofHash'] == _hash(record), 'An adult must approve the current proof before ordering or printing')


def _email(value):
    value = _text(value, 'Customer email', 254)
    require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[A-Za-z]{2,63}', value), 'Enter a valid customer email address')
    return value


def _order(record, body):
    _approved(record)
    require(body.get('adult') is True, 'An adult must place the order')
    require(type(body.get('quantity')) is int and 1 <= body['quantity'] <= 10, 'Order quantity must be a whole number from 1 to 10')
    details = {key: _text(body.get(key, ''), label, limit, required) for key, label, limit, required in [
        ('customerName', 'Adult customer name', 120, True), ('recipientName', 'Recipient name', 120, True),
        ('addressLine1', 'Address line 1', 200, True), ('addressLine2', 'Address line 2', 200, False),
        ('city', 'City', 100, True), ('region', 'State / region', 100, False), ('postalCode', 'Postal code', 30, True),
        ('country', 'Country', 100, True)]}
    previous = record['order']
    require(not previous or previous['status'] in ('awaiting_print', 'proof_changed', 'cancelled'), 'This order is already in fulfillment')
    if previous and previous['status'] == 'cancelled':
        record['orderHistory'].append(copy.deepcopy(previous))
        previous = None
    return dict(**details, customerEmail=_email(body.get('customerEmail')), quantity=body['quantity'],
                id=previous['id'] if previous else ident(), status='awaiting_print', createdAt=previous['createdAt'] if previous else now(),
                confirmedAt=now(), proofVersion=record['proofVersion'], proofHash=_hash(record), pages=copy.deepcopy(record['pages']),
                approval=copy.deepcopy(record['approval']), paymentStatus=previous['paymentStatus'] if previous else 'not_recorded',
                payment=copy.deepcopy(previous.get('payment')) if previous else None)


def act(record, action, body):
    record = copy.deepcopy(record)
    if action == 'personalize':
        _editable(record)
        require(body.get('replacePages') is True, 'Confirm that personalization replaces the existing page edits')
        title = _text(body.get('title'), 'Book title', 80, printable=True)
        personalization = _personalization(body)
        record.update(title=title, personalization=personalization, pages=_pages(title, personalization))
        _invalidate(record)
    elif action == 'edit-page':
        _editable(record)
        page = next((p for p in record['pages'] if p['id'] == body.get('pageId')), None)
        require(page is not None, 'Page not found', 404)
        title, text = _validate_page(body.get('title'), body.get('text'))
        page.update(title=title, text=text)
        if page['number'] == 1:
            record['title'] = title
        _invalidate(record)
    elif action == 'approve-proof':
        _editable(record)
        require(body.get('adult') is True and body.get('reviewedAllPages') is True, 'An adult must review every page before approval')
        require(type(body.get('proofVersion')) is int and body['proofVersion'] == record['proofVersion'], 'The proof changed; review the current version', 409)
        record['approval'] = dict(approverName=_text(body.get('approverName'), 'Adult approver name', 120),
                                  proofVersion=record['proofVersion'], proofHash=_hash(record), approvedAt=now(), adult=True)
    elif action == 'place-order':
        record['order'] = _order(record, body)
    elif action == 'record-payment':
        order = record['order']
        require(order and order['status'] != 'cancelled', 'Create an active order before recording payment')
        require(order['paymentStatus'] == 'not_recorded', 'Payment is already recorded; preserve the existing receipt')
        amount = body.get('amount')
        require(type(amount) in (float, int) and 0 < amount <= 100000 and math.isfinite(amount) and round(amount, 2) == amount, 'Enter a positive amount with at most two decimal places')
        currency = _text(body.get('currency'), 'Currency code', 3).upper()
        require(re.fullmatch('[A-Z]{3}', currency), 'Use a three-letter currency code')
        require(body.get('received') is True, 'Confirm that payment was actually received outside this application')
        order['payment'] = dict(amount=amount, currency=currency, receiptReference=_text(body.get('receiptReference'), 'Real payment receipt reference', 200), recordedAt=now())
        order['paymentStatus'] = 'recorded_manually'
    elif action == 'record-print':
        _approved(record)
        order = record['order']
        require(order and order['status'] == 'awaiting_print' and order['proofHash'] == _hash(record), 'Confirm an order for the approved proof before recording printing')
        require(body.get('completed') is True, 'Confirm that the print provider has actually completed printing')
        order.update(provider=_text(body.get('provider'), 'Print provider', 120),
                     providerReference=_text(body.get('providerReference'), 'Real provider job / receipt reference', 200),
                     status='printed', printedAt=now())
    elif action == 'record-shipment':
        order = record['order']
        require(order and order['status'] == 'printed', 'Record completed printing before shipping')
        require(body.get('handedToCarrier') is True, 'Confirm that the physical books were handed to the carrier')
        order.update(carrier=_text(body.get('carrier'), 'Carrier', 100),
                     trackingNumber=_text(body.get('trackingNumber'), 'Tracking number', 150), status='shipped', shippedAt=now())
    elif action == 'cancel-order':
        order = record['order']
        require(order and order['status'] in ('awaiting_print', 'proof_changed'), 'Only orders that have not entered fulfillment can be cancelled here')
        order.update(status='cancelled', cancelledAt=now(), cancellationReason=_text(body.get('reason'), 'Cancellation reason', 500))
    else:
        fail(404, 'Unknown storybook action')
    if record['order']:
        record['orderSummary'] = {key: copy.deepcopy(value) for key, value in record['order'].items()
                                  if key not in ('id', 'pages', 'proofHash', 'approval')}
    return record


def export(record, format):
    if format == 'pdf':
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=(612, 612), pageCompression=0)
        pdf.setTitle(record['title'])
        pdf.setAuthor('Story Keepsake - fixed template edition 1')
        proof_state = 'ADULT APPROVED' if record['approval'] and record['approval']['proofHash'] == _hash(record) else 'AWAITING ADULT REVIEW'
        for page in record['pages']:
            pdf.setFillColor(HexColor('#fbf7ee'))
            pdf.rect(0, 0, 612, 612, fill=1, stroke=0)
            pdf.setFillColor(HexColor('#e7b765'))
            pdf.rect(0, 596, 612, 16, fill=1, stroke=0)
            pdf.setFont('Helvetica-Bold', 10)
            pdf.setFillColor(HexColor('#58736a'))
            pdf.drawString(64, 555, 'THE WELCOME STAR / A PERSONAL STORY')
            pdf.setFillColor(HexColor('#e9e1d1'))
            pdf.setFont('Helvetica-Bold', 66)
            pdf.drawRightString(550, 487, f'{page["number"]:02d}')
            title = _paragraph(page['title'], TITLE_STYLE)
            title_height = title.wrap(484, 500)[1]
            title.drawOn(pdf, 64, 470 - title_height)
            text = _paragraph(page['text'], TEXT_STYLE)
            text_height = text.wrap(484, 500)[1]
            text.drawOn(pdf, 64, 446 - title_height - text_height)
            pdf.setStrokeColor(HexColor('#d5d9ca'))
            pdf.line(64, 59, 548, 59)
            pdf.setFillColor(HexColor('#58736a'))
            pdf.setFont('Helvetica', 8)
            pdf.drawString(64, 42, f'PROOF {record["proofVersion"]} / {proof_state}')
            pdf.drawRightString(548, 42, f'{page["number"]} / {len(record["pages"])}')
            pdf.showPage()
        pdf.save()
        return output.getvalue(), 'application/pdf', f'storybook-proof-v{record["proofVersion"]}.pdf'
    if format == 'txt':
        order = record['order']
        require(order, 'Create an order before exporting its handoff')
        lines = ['STORY KEEPSAKE - MANUAL ORDER HANDOFF', f'Book: {record["title"]}', f'Order: {order["id"]}',
                 f'Status: {order["status"]}', f'Proof version: {order["proofVersion"]}', f'Proof SHA256: {order["proofHash"]}',
                 f'Quantity: {order["quantity"]}', f'Adult customer: {order["customerName"]}', f'Email: {order["customerEmail"]}',
                 f'Recipient: {order["recipientName"]}', *[order[k] for k in ('addressLine1', 'addressLine2', 'city', 'region', 'postalCode', 'country')],
                 f'Payment: {order["paymentStatus"]}', 'This export does not charge, print, ship, or refund an order.']
        if order.get('payment'):
            lines.append('Payment receipt: ' + order['payment']['receiptReference'])
        for key in ('provider', 'providerReference', 'carrier', 'trackingNumber'):
            if order.get(key):
                lines.append(f'{key}: {order[key]}')
        return '\n'.join(lines).encode(), 'text/plain', 'storybook-order-handoff.txt'
    fail(404, 'Unknown storybook export format')
