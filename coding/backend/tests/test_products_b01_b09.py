"""Behavior checks for evidence, review gates, and stale approval handling."""
import csv
import importlib
import io
from copy import deepcopy

import pytest
from fastapi import HTTPException


@pytest.fixture
def b01():
    return importlib.import_module('app.products.b01')


@pytest.fixture
def b09():
    return importlib.import_module('app.products.b09')


def desk(module):
    record = module.create({'title': 'Customer help', 'escalationQueue': 'Operations'})
    return module.act(record, 'add-source', {
        'title': 'Refund policy', 'url': 'https://example.com/refunds',
        'content': 'Customers can request a refund within 30 days of purchase.', 'authorized': True,
    })


def answer(module, record, question='How many days can customers request a refund?'):
    record = module.act(record, 'add-ticket', {'question': question, 'customerLabel': 'Order 12'})
    ticket_id = record['tickets'][-1]['id']
    record = module.act(record, 'draft-response', {'ticketId': ticket_id})
    return record, ticket_id


def company(module):
    record = module.create({'title': 'Acme account', 'companyName': 'Acme Logistics',
        'website': 'https://example.com', 'icp': 'logistics, warehouse', 'offer': 'warehouse planning workshops'})
    record = module.act(record, 'add-source', {'title': 'Company overview',
        'url': 'https://example.com/about', 'content': 'Acme operates three logistics warehouses in Bristol.'})
    return module.act(record, 'add-fact', {'sourceId': record['sources'][0]['id'],
        'category': 'operations', 'statement': 'Acme operates three logistics warehouses in Bristol.',
        'quote': 'Acme operates three logistics warehouses in Bristol.'})


def verified(module, record):
    return module.act(record, 'verify-fact', {'factId': record['facts'][0]['id'], 'confirmed': True})


def reviewed(module, record):
    return module.act(record, 'review-research', {'confirmed': True, 'fit': 'good',
        'notes': 'The published warehouse operations match the offer.'})


def email(module, record):
    record = module.act(record, 'draft-email', {})
    return module.act(record, 'review-email', {'confirmed': True})


def rejects(call, code=422):
    with pytest.raises(HTTPException) as error:
        call()
    assert error.value.status_code == code


def test_b01_requires_explicit_authorization_and_does_not_mutate_input(b01):
    record = b01.create({'title': 'Help'})
    original = deepcopy(record)
    for authorization in (False, 'true', 1, None):
        rejects(lambda: b01.act(record, 'add-source', {'title': 'Policy', 'url': 'https://example.com',
            'content': 'A source.', 'authorized': authorization}))
    assert record == original
    result = desk(b01)
    assert result['sources'][0]['version'] == 1


def test_b01_draft_has_verbatim_evidence_and_reviewed_export_gate(b01):
    record, ticket_id = answer(b01, desk(b01))
    ticket = record['tickets'][0]
    assert ticket['status'] == 'draft'
    assert 'within 30 days' in ticket['draft']
    assert ticket['citations'][0]['sourceVersion'] == 1
    assert ticket['citations'][0]['quote'] in record['sources'][0]['content']
    rejects(lambda: b01.export(record, 'txt'))
    rejects(lambda: b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': False}))
    record = b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': True})
    content, mime, filename = b01.export(record, 'txt')
    assert 'https://example.com/refunds' in content.decode()
    assert 'within 30 days' in content.decode()
    assert mime.startswith('text/plain') and filename.endswith('.txt')
    record = b01.act(record, 'close-ticket', {'ticketId': ticket_id, 'confirmed': True, 'note': 'Checked for order 12'})
    assert record['tickets'][0]['status'] == 'closed'
    assert record['tickets'][0]['resolutionNote'] == 'Checked for order 12'


def test_b01_insufficient_evidence_escalates_and_cannot_be_approved(b01):
    record, ticket_id = answer(b01, desk(b01), 'Does your product support submarine navigation?')
    ticket = record['tickets'][0]
    assert ticket['status'] == 'needs-human'
    assert ticket['citations'] == [] and ticket['draft'] == ''
    assert 'Operations' in ticket['escalationReason']
    rejects(lambda: b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': True}))
    rejects(lambda: b01.act(record, 'close-ticket', {'ticketId': ticket_id, 'confirmed': True, 'note': 'Done'}))


def test_b01_source_edit_keeps_prior_version_and_invalidates_approved_ticket(b01):
    record, ticket_id = answer(b01, desk(b01))
    record = b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': True})
    before = deepcopy(record)
    changed = b01.act(record, 'update-source', {'sourceId': record['sources'][0]['id'],
        'title': 'Refund policy', 'url': 'https://example.com/refunds',
        'content': 'Customers can request a refund within 14 days of purchase.', 'authorized': True})
    assert record == before
    assert changed['sources'][0]['version'] == 2
    assert '30 days' in changed['sources'][0]['history'][0]['content']
    assert changed['tickets'][0]['status'] == 'needs-refresh'
    assert changed['tickets'][0]['approvedAt'] is None
    rejects(lambda: b01.export(changed, 'csv'))
    rejects(lambda: b01.act(changed, 'review-response', {'ticketId': ticket_id, 'confirmed': True}))
    refreshed = b01.act(changed, 'draft-response', {'ticketId': ticket_id})
    assert '14 days' in refreshed['tickets'][0]['draft']
    assert refreshed['tickets'][0]['citations'][0]['sourceVersion'] == 2


def test_b01_retired_source_is_not_retrieved_and_draft_edit_resets_review(b01):
    record, ticket_id = answer(b01, desk(b01))
    record = b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': True})
    record = b01.act(record, 'edit-response', {'ticketId': ticket_id, 'draft': 'Please see the refund policy [1].'})
    assert record['tickets'][0]['status'] == 'draft' and record['tickets'][0]['approvedAt'] is None
    record = b01.act(record, 'retire-source', {'sourceId': record['sources'][0]['id']})
    record = b01.act(record, 'draft-response', {'ticketId': ticket_id})
    assert record['tickets'][0]['status'] == 'needs-human'


def test_b01_cannot_close_a_draft_or_export_unsupported_format(b01):
    record, ticket_id = answer(b01, desk(b01))
    rejects(lambda: b01.act(record, 'close-ticket', {'ticketId': ticket_id, 'confirmed': True, 'note': 'Done'}))
    rejects(lambda: b01.export(record, 'pdf'))
    rejects(lambda: b01.act(record, 'draft-response', {'ticketId': 'missing'}), 404)


def test_b09_facts_require_source_quote_and_human_verification(b09):
    record = company(b09)
    assert record['facts'][0]['status'] == 'unverified'
    rejects(lambda: b09.act(record, 'add-fact', {'sourceId': record['sources'][0]['id'],
        'category': 'operations', 'statement': 'A false fact', 'quote': 'Made-up quotation'}))
    rejects(lambda: b09.act(record, 'verify-fact', {'factId': record['facts'][0]['id'], 'confirmed': 'true'}))
    rejects(lambda: b09.act(record, 'review-research', {'confirmed': True, 'fit': 'good', 'notes': 'Matched'}))
    rejects(lambda: b09.act(record, 'draft-email', {}))
    record = verified(b09, record)
    assert record['facts'][0]['status'] == 'verified'
    assert 'logistics' in record['assessment']['matchedKeywords']


def test_b09_grounded_email_requires_current_research_and_email_review(b09):
    record = reviewed(b09, verified(b09, company(b09)))
    record = b09.act(record, 'draft-email', {})
    assert 'three logistics warehouses' in record['email']['body']
    assert 'warehouse planning workshops' in record['email']['body']
    assert record['email']['citations'][0]['sourceVersion'] == 1
    rejects(lambda: b09.export(record, 'txt'))
    record = b09.act(record, 'review-email', {'confirmed': True})
    content, _, _ = b09.export(record, 'txt')
    assert 'https://example.com/about' in content.decode()
    assert 'Hello Acme Logistics team' in content.decode()
    assert record['email']['status'] == 'approved'
    csv_bytes, _, _ = b09.export(record, 'csv')
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode())))
    assert rows[0]['statement'] == 'Acme operates three logistics warehouses in Bristol.'


def test_b09_source_update_invalidates_fact_research_and_email(b09):
    record = email(b09, reviewed(b09, verified(b09, company(b09))))
    before = deepcopy(record)
    changed = b09.act(record, 'update-source', {'sourceId': record['sources'][0]['id'],
        'title': 'Company overview', 'url': 'https://example.com/about',
        'content': 'Acme operates four logistics warehouses in Bristol.'})
    assert record == before
    assert changed['facts'][0]['status'] == 'stale'
    assert changed['research']['status'] == 'needs-review'
    assert changed['email']['status'] == 'needs-refresh'
    assert changed['sources'][0]['history'][0]['version'] == 1
    rejects(lambda: b09.act(changed, 'verify-fact', {'factId': changed['facts'][0]['id'], 'confirmed': True}))
    rejects(lambda: b09.export(changed, 'txt'))
    rejects(lambda: b09.export(changed, 'csv'))


def test_b09_fact_and_offer_edits_invalidate_prior_reviews(b09):
    record = email(b09, reviewed(b09, verified(b09, company(b09))))
    changed = b09.act(record, 'update-fact', {'factId': record['facts'][0]['id'],
        'sourceId': record['sources'][0]['id'], 'category': 'operations',
        'statement': 'Acme has logistics warehouses in Bristol.',
        'quote': 'Acme operates three logistics warehouses in Bristol.'})
    assert changed['facts'][0]['status'] == 'unverified'
    assert changed['email']['status'] == 'needs-refresh'
    changed = b09.act(record, 'update-profile', {'companyName': 'Acme Logistics',
        'website': 'https://example.com', 'icp': 'logistics', 'offer': 'new offer'})
    assert changed['research']['status'] == 'needs-review'
    assert changed['email']['status'] == 'needs-refresh'
    rejects(lambda: b09.act(changed, 'review-email', {'confirmed': True}))


def test_b09_manual_email_edit_resets_review_and_does_not_mutate_record(b09):
    record = email(b09, reviewed(b09, verified(b09, company(b09))))
    before = deepcopy(record)
    changed = b09.act(record, 'edit-email', {'subject': 'A warehouse planning question',
        'body': 'Hello Acme team, may we discuss the operations described in [1]?'})
    assert record == before
    assert changed['email']['status'] == 'draft'
    assert changed['email']['approvedAt'] is None
    rejects(lambda: b09.export(changed, 'txt'))


@pytest.mark.parametrize('code', ['b01', 'b09'])
def test_modules_reject_invalid_source_urls_and_ignore_reserved_metadata(code):
    module = importlib.import_module('app.products.' + code)
    body = {'title': 'Test', 'companyName': 'Test', 'website': 'https://example.com', 'icp': 'retail',
        'offer': 'planning', 'owner': 'other-user', 'code': 'X', 'revision': 999}
    record = module.create(body)
    assert not {'owner', 'code', 'revision'} & record.keys()
    for url in ('javascript:alert(1)', 'file:///etc/passwd', 'https://user:pass@example.com', 'not a URL'):
        rejects(lambda: module.act(record, 'add-source', {'title': 'Test', 'url': url,
            'content': 'A relevant statement', 'authorized': True}))


def test_b09_changing_company_identity_requires_reverification_of_existing_facts(b09):
    record = email(b09, reviewed(b09, verified(b09, company(b09))))
    changed = b09.act(record, 'update-profile', {'companyName': 'Different Company',
        'website': 'https://different.example', 'icp': 'logistics', 'offer': 'planning'})
    assert changed['facts'][0]['status'] == 'unverified'
    assert changed['facts'][0]['verifiedAt'] is None
    assert changed['assessment']['verifiedFacts'] == 0
    rejects(lambda: reviewed(b09, changed))


def test_b01_new_knowledge_requires_refresh_and_handoff_is_explicit(b01):
    record, ticket_id = answer(b01, desk(b01))
    record = b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': True})
    record = b01.act(record, 'add-source', {'title': 'Special conditions', 'url': 'https://example.com/special',
        'content': 'A holiday refund can be requested within 60 days.', 'authorized': True})
    assert record['tickets'][0]['status'] == 'needs-refresh'
    rejects(lambda: b01.export(record, 'txt'))
    record, no_match = answer(b01, record, 'Does the submarine navigate underwater?')
    record = b01.act(record, 'record-escalation', {'ticketId': no_match, 'note': 'Assigned to Alex to obtain technical documentation.'})
    assert record['tickets'][-1]['status'] == 'needs-human'
    assert 'Alex' in record['tickets'][-1]['escalationNote']


def test_b09_unverified_facts_never_enter_email_or_reviewed_csv(b09):
    record = verified(b09, company(b09))
    record = b09.act(record, 'add-fact', {'sourceId': record['sources'][0]['id'], 'category': 'size',
        'statement': 'UNVERIFIED CLAIM', 'quote': 'three logistics warehouses'})
    record = reviewed(b09, record)
    record = b09.act(record, 'draft-email', {})
    assert 'UNVERIFIED CLAIM' not in record['email']['body']
    assert len(record['email']['citations']) == 1
    assert 'UNVERIFIED CLAIM' not in b09.export(record, 'csv')[0].decode()


def test_reviewed_csv_exports_escape_spreadsheet_formula_cells(b01, b09):
    support, ticket_id = answer(b01, desk(b01))
    support = b01.act(support, 'edit-response', {'ticketId': ticket_id, 'draft': '=HYPERLINK("https://example.com")'})
    support = b01.act(support, 'review-response', {'ticketId': ticket_id, 'confirmed': True})
    support_rows = list(csv.DictReader(io.StringIO(b01.export(support, 'csv')[0].decode())))
    assert support_rows[0]['response'].startswith("'=HYPERLINK")
    research = verified(b09, company(b09))
    research = b09.act(research, 'review-research', {'confirmed': True, 'fit': 'good', 'notes': '=1+1'})
    research_rows = list(csv.DictReader(io.StringIO(b09.export(research, 'csv')[0].decode())))
    assert research_rows[0]['review_notes'] == "'=1+1"


@pytest.mark.parametrize('reopen', ['knowledge-change', 'draft-response', 'edit-response'])
def test_b01_preserves_handling_receipt_when_closed_response_is_reopened(b01, reopen):
    record, ticket_id = answer(b01, desk(b01))
    record = b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': True})
    record = b01.act(record, 'close-ticket', {'ticketId': ticket_id, 'confirmed': True,
        'note': 'Sent through help desk ticket CASE-143; customer acknowledged.'})
    closed = deepcopy(record['tickets'][0])
    if reopen == 'knowledge-change':
        record = b01.act(record, 'update-source', {'sourceId': record['sources'][0]['id'],
            'title': 'Refund policy', 'url': 'https://example.com/refunds',
            'content': 'Customers can request a refund within 14 days of purchase.', 'authorized': True})
        record = b01.act(record, 'draft-response', {'ticketId': ticket_id})
    elif reopen == 'draft-response':
        record = b01.act(record, reopen, {'ticketId': ticket_id})
    else:
        record = b01.act(record, reopen, {'ticketId': ticket_id, 'draft': 'The policy allows a refund within 30 days [1].'})
    ticket = record['tickets'][0]
    assert ticket['closedAt'] is None and ticket['resolutionNote'] == ''
    assert len(ticket['closureHistory']) == 1
    snapshot = ticket['closureHistory'][0]
    for field in ('closedAt', 'resolutionNote', 'approvedAt', 'knowledgeVersion', 'draft', 'citations'):
        assert snapshot[field] == closed[field]
    record = b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': True})
    assert record['tickets'][0]['closureHistory'][0] == snapshot


def test_b01_preserves_legacy_closure_without_existing_history(b01):
    record, ticket_id = answer(b01, desk(b01))
    record = b01.act(record, 'review-response', {'ticketId': ticket_id, 'confirmed': True})
    record = b01.act(record, 'close-ticket', {'ticketId': ticket_id, 'confirmed': True, 'note': 'Legacy handling reference'})
    record['tickets'][0].pop('closureHistory', None)
    record = b01.act(record, 'draft-response', {'ticketId': ticket_id})
    assert record['tickets'][0]['closureHistory'][0]['resolutionNote'] == 'Legacy handling reference'


@pytest.mark.parametrize('code', ['b01', 'b09'])
@pytest.mark.parametrize('url', ['https://example.com:bad/refunds', 'https://example.com:99999/refunds',
    'https://exam\x00ple.com/refunds', '\thttps://example.com/refunds', 'https://example.com/refunds\x7f'])
def test_research_source_urls_reject_invalid_ports_and_controls(code, url):
    module = importlib.import_module('app.products.' + code)
    record = desk(module) if code == 'b01' else company(module)
    rejects(lambda: module.act(record, 'add-source', {'title': 'Invalid URL', 'url': url,
        'content': 'Published source information.', 'authorized': True}))


def test_b09_company_website_uses_the_same_strict_url_validation(b09):
    rejects(lambda: b09.create({'title': 'Company', 'companyName': 'Acme',
        'website': 'https://example.com:bad', 'icp': 'logistics', 'offer': 'planning'}))
    record = company(b09)
    rejects(lambda: b09.act(record, 'update-profile', {'companyName': 'Acme',
        'website': 'https://example.com:99999', 'icp': 'logistics', 'offer': 'planning'}))
