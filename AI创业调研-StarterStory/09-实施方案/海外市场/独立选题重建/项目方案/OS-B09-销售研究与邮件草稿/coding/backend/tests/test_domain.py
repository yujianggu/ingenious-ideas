"""Behavior checks for evidence, review gates, and stale approval handling."""
import csv
import importlib
import io
from copy import deepcopy
import pytest
from fastapi import HTTPException

@pytest.fixture
def b09():
    return importlib.import_module('app.products.b09')

def company(module):
    record = module.create({'title': 'Acme account', 'companyName': 'Acme Logistics', 'website': 'https://example.com', 'icp': 'logistics, warehouse', 'offer': 'warehouse planning workshops'})
    record = module.act(record, 'add-source', {'title': 'Company overview', 'url': 'https://example.com/about', 'content': 'Acme operates three logistics warehouses in Bristol.'})
    return module.act(record, 'add-fact', {'sourceId': record['sources'][0]['id'], 'category': 'operations', 'statement': 'Acme operates three logistics warehouses in Bristol.', 'quote': 'Acme operates three logistics warehouses in Bristol.'})

def verified(module, record):
    return module.act(record, 'verify-fact', {'factId': record['facts'][0]['id'], 'confirmed': True})

def reviewed(module, record):
    return module.act(record, 'review-research', {'confirmed': True, 'fit': 'good', 'notes': 'The published warehouse operations match the offer.'})

def email(module, record):
    record = module.act(record, 'draft-email', {})
    return module.act(record, 'review-email', {'confirmed': True})

def rejects(call, code=422):
    with pytest.raises(HTTPException) as error:
        call()
    assert error.value.status_code == code

def test_b09_facts_require_source_quote_and_human_verification(b09):
    record = company(b09)
    assert record['facts'][0]['status'] == 'unverified'
    rejects(lambda : b09.act(record, 'add-fact', {'sourceId': record['sources'][0]['id'], 'category': 'operations', 'statement': 'A false fact', 'quote': 'Made-up quotation'}))
    rejects(lambda : b09.act(record, 'verify-fact', {'factId': record['facts'][0]['id'], 'confirmed': 'true'}))
    rejects(lambda : b09.act(record, 'review-research', {'confirmed': True, 'fit': 'good', 'notes': 'Matched'}))
    rejects(lambda : b09.act(record, 'draft-email', {}))
    record = verified(b09, record)
    assert record['facts'][0]['status'] == 'verified'
    assert 'logistics' in record['assessment']['matchedKeywords']

def test_b09_grounded_email_requires_current_research_and_email_review(b09):
    record = reviewed(b09, verified(b09, company(b09)))
    record = b09.act(record, 'draft-email', {})
    assert 'three logistics warehouses' in record['email']['body']
    assert 'warehouse planning workshops' in record['email']['body']
    assert record['email']['citations'][0]['sourceVersion'] == 1
    rejects(lambda : b09.export(record, 'txt'))
    record = b09.act(record, 'review-email', {'confirmed': True})
    (content, _, _) = b09.export(record, 'txt')
    assert 'https://example.com/about' in content.decode()
    assert 'Hello Acme Logistics team' in content.decode()
    assert record['email']['status'] == 'approved'
    (csv_bytes, _, _) = b09.export(record, 'csv')
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode())))
    assert rows[0]['statement'] == 'Acme operates three logistics warehouses in Bristol.'

def test_b09_source_update_invalidates_fact_research_and_email(b09):
    record = email(b09, reviewed(b09, verified(b09, company(b09))))
    before = deepcopy(record)
    changed = b09.act(record, 'update-source', {'sourceId': record['sources'][0]['id'], 'title': 'Company overview', 'url': 'https://example.com/about', 'content': 'Acme operates four logistics warehouses in Bristol.'})
    assert record == before
    assert changed['facts'][0]['status'] == 'stale'
    assert changed['research']['status'] == 'needs-review'
    assert changed['email']['status'] == 'needs-refresh'
    assert changed['sources'][0]['history'][0]['version'] == 1
    rejects(lambda : b09.act(changed, 'verify-fact', {'factId': changed['facts'][0]['id'], 'confirmed': True}))
    rejects(lambda : b09.export(changed, 'txt'))
    rejects(lambda : b09.export(changed, 'csv'))

def test_b09_fact_and_offer_edits_invalidate_prior_reviews(b09):
    record = email(b09, reviewed(b09, verified(b09, company(b09))))
    changed = b09.act(record, 'update-fact', {'factId': record['facts'][0]['id'], 'sourceId': record['sources'][0]['id'], 'category': 'operations', 'statement': 'Acme has logistics warehouses in Bristol.', 'quote': 'Acme operates three logistics warehouses in Bristol.'})
    assert changed['facts'][0]['status'] == 'unverified'
    assert changed['email']['status'] == 'needs-refresh'
    changed = b09.act(record, 'update-profile', {'companyName': 'Acme Logistics', 'website': 'https://example.com', 'icp': 'logistics', 'offer': 'new offer'})
    assert changed['research']['status'] == 'needs-review'
    assert changed['email']['status'] == 'needs-refresh'
    rejects(lambda : b09.act(changed, 'review-email', {'confirmed': True}))

def test_b09_manual_email_edit_resets_review_and_does_not_mutate_record(b09):
    record = email(b09, reviewed(b09, verified(b09, company(b09))))
    before = deepcopy(record)
    changed = b09.act(record, 'edit-email', {'subject': 'A warehouse planning question', 'body': 'Hello Acme team, may we discuss the operations described in [1]?'})
    assert record == before
    assert changed['email']['status'] == 'draft'
    assert changed['email']['approvedAt'] is None
    rejects(lambda : b09.export(changed, 'txt'))

@pytest.mark.parametrize('code', ['b09'])
def test_modules_reject_invalid_source_urls_and_ignore_reserved_metadata(code):
    module = importlib.import_module('app.products.' + code)
    body = {'title': 'Test', 'companyName': 'Test', 'website': 'https://example.com', 'icp': 'retail', 'offer': 'planning', 'owner': 'other-user', 'code': 'X', 'revision': 999}
    record = module.create(body)
    assert not {'owner', 'code', 'revision'} & record.keys()
    for url in ('javascript:alert(1)', 'file:///etc/passwd', 'https://user:pass@example.com', 'not a URL'):
        rejects(lambda : module.act(record, 'add-source', {'title': 'Test', 'url': url, 'content': 'A relevant statement', 'authorized': True}))

def test_b09_changing_company_identity_requires_reverification_of_existing_facts(b09):
    record = email(b09, reviewed(b09, verified(b09, company(b09))))
    changed = b09.act(record, 'update-profile', {'companyName': 'Different Company', 'website': 'https://different.example', 'icp': 'logistics', 'offer': 'planning'})
    assert changed['facts'][0]['status'] == 'unverified'
    assert changed['facts'][0]['verifiedAt'] is None
    assert changed['assessment']['verifiedFacts'] == 0
    rejects(lambda : reviewed(b09, changed))

def test_b09_unverified_facts_never_enter_email_or_reviewed_csv(b09):
    record = verified(b09, company(b09))
    record = b09.act(record, 'add-fact', {'sourceId': record['sources'][0]['id'], 'category': 'size', 'statement': 'UNVERIFIED CLAIM', 'quote': 'three logistics warehouses'})
    record = reviewed(b09, record)
    record = b09.act(record, 'draft-email', {})
    assert 'UNVERIFIED CLAIM' not in record['email']['body']
    assert len(record['email']['citations']) == 1
    assert 'UNVERIFIED CLAIM' not in b09.export(record, 'csv')[0].decode()

def test_reviewed_csv_exports_escape_spreadsheet_formula_cells(b09):
    research = verified(b09, company(b09))
    research = b09.act(research, 'review-research', {'confirmed': True, 'fit': 'good', 'notes': '=1+1'})
    research_rows = list(csv.DictReader(io.StringIO(b09.export(research, 'csv')[0].decode())))
    assert research_rows[0]['review_notes'] == "'=1+1"

def test_b09_company_website_uses_the_same_strict_url_validation(b09):
    rejects(lambda : b09.create({'title': 'Company', 'companyName': 'Acme', 'website': 'https://example.com:bad', 'icp': 'logistics', 'offer': 'planning'}))
    record = company(b09)
    rejects(lambda : b09.act(record, 'update-profile', {'companyName': 'Acme', 'website': 'https://example.com:99999', 'icp': 'logistics', 'offer': 'planning'}))

@pytest.mark.parametrize('code', ['b09'])
@pytest.mark.parametrize('url', ['https://example.com:bad/refunds', 'https://example.com:99999/refunds', 'https://exam\x00ple.com/refunds', '\thttps://example.com/refunds', 'https://example.com/refunds\x7f'])
def test_research_source_urls_reject_invalid_ports_and_controls(code, url):
    module = importlib.import_module('app.products.' + code)
    record = company(module)
    rejects(lambda : module.act(record, 'add-source', {'title': 'Invalid URL', 'url': url, 'content': 'Published source information.', 'authorized': True}))
