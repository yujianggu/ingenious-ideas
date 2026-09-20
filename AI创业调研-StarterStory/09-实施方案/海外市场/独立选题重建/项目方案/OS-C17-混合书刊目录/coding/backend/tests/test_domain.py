import base64
import csv
import io
import json
import zipfile
from copy import deepcopy
from importlib import import_module
import pytest
from fastapi import HTTPException

def product(code):
    try:
        return import_module('app.products.' + code)
    except ModuleNotFoundError:
        pytest.fail(f'{code} domain module must exist')

def library():
    return product('c17').create({'title': 'Home shelves'})

def test_library_numeric_order_isbn_validation_and_duplicate_detection():
    c17 = product('c17')
    r = library()
    for issue in [10, 2, 1]:
        r = c17.act(r, 'add-entry', {'title': 'Moon', 'type': 'comic', 'volume': 1, 'issue': issue, 'location': 'Shelf A'})
    assert [i['issue'] for i in r['entries']] == [1, 2, 10]
    with pytest.raises(HTTPException):
        c17.act(r, 'add-entry', {'title': ' moon ', 'type': 'comic', 'volume': 1, 'issue': 2})
    with pytest.raises(HTTPException):
        c17.act(r, 'add-entry', {'title': 'Bad ISBN', 'type': 'book', 'isbn': '9780306406158'})
    r = c17.act(r, 'add-entry', {'title': 'Book', 'type': 'book', 'isbn': '978-0-306-40615-7'})
    with pytest.raises(HTTPException):
        c17.act(r, 'add-entry', {'title': 'Alternate title', 'type': 'book', 'isbn': '0-306-40615-2'})

def test_library_filters_locations_and_reports_issue_gaps():
    c17 = product('c17')
    r = library()
    for issue in [1, 3, 10]:
        r = c17.act(r, 'add-entry', {'title': 'Moon', 'type': 'comic', 'volume': 2, 'issue': issue, 'location': 'Shelf A'})
    r = c17.act(r, 'query', {'search': 'moon', 'type': 'comic', 'location': 'Shelf A'})
    assert len(r['queryResults']) == 3
    r = c17.act(r, 'find-gaps', {'title': 'Moon', 'type': 'comic', 'volume': 2, 'fromIssue': 1, 'toIssue': 4})
    assert [x['issue'] for x in r['gapResults']] == [2, 4]
    r = c17.act(r, 'move-entry', {'entryId': r['entries'][0]['id'], 'location': 'Box B'})
    r = c17.act(r, 'query', {'location': 'Box B'})
    assert len(r['queryResults']) == 1

def test_library_csv_preview_roundtrip_formula_safety_and_restore():
    c17 = product('c17')
    r = library()
    for title in ['=SUM(1,2)', "'=literal", 'Normal']:
        r = c17.act(r, 'add-entry', {'title': title, 'type': 'book', 'notes': '@note'})
    (raw, _, _) = c17.export(r, 'csv')
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    assert all((not row['title'].startswith(('=', '+', '-', '@')) for row in rows))
    r2 = c17.act(library(), 'preview-import', {'content': raw.decode()})
    assert r2['entries'] == []
    assert len(r2['importPreview']['entries']) == 3
    r2 = c17.act(r2, 'commit-import', {})
    assert [e['title'] for e in r2['entries']] == [e['title'] for e in r['entries']]
    assert all((e['notes'] == '@note' for e in r2['entries']))
    r2 = c17.act(r2, 'preview-import', {'content': raw.decode()})
    assert len(r2['importPreview']['rejected']) == 3
    restored = c17.act(library(), 'restore-backup', {'content': json.dumps(r)})
    assert [e['title'] for e in restored['entries']] == [e['title'] for e in r['entries']]

def test_domain_actions_never_mutate_input_records_on_error_or_success():
    for (code, record, action, body) in [('c17', library(), 'add-entry', {'title': 'Novel', 'type': 'book'})]:
        before = deepcopy(record)
        try:
            product(code).act(record, action, body)
        except HTTPException:
            pass
        assert record == before

@pytest.mark.parametrize('code,body', [('c17', {'title': []})])
def test_invalid_create_fields_produce_validation_errors(code, body):
    with pytest.raises(HTTPException) as error:
        product(code).create(body)
    assert error.value.status_code == 422

def test_isbn_unicode_numerals_are_rejected_without_internal_errors():
    c17 = product('c17')
    for value in ['²030640615', '０306406152', '978０306406157']:
        with pytest.raises(HTTPException) as error:
            c17.act(library(), 'add-entry', {'title': 'Invalid', 'isbn': value})
        assert error.value.status_code == 422

def test_collection_restore_is_atomic_and_rejects_invalid_isbn():
    c17 = product('c17')
    r = c17.act(library(), 'add-entry', {'title': 'Owned', 'type': 'book'})
    snapshot = deepcopy(r)
    bad = {'title': 'Imported', 'entries': [{'title': 'Fine', 'type': 'book'}, {'title': 'Bad', 'isbn': '9780306406158'}]}
    with pytest.raises(HTTPException):
        c17.act(r, 'restore-backup', {'content': json.dumps(bad)})
    assert r == snapshot

def test_consumer_specs_reference_supported_actions_and_explicit_business_fields():
    from pathlib import Path
    for code in ['c17']:
        spec = json.loads((Path(__file__).parents[2] / 'shared' / 'products' / (code + '.json')).read_text())
        assert spec['code'] == code.upper()
        assert spec['create'][0]['name'] == 'title'
        assert all((f['type'] != 'json' for a in spec['actions'] for f in a['fields']))
        assert len({a['id'] for a in spec['actions']}) == len(spec['actions'])

def test_library_selection_labels_distinguish_issues_with_same_title():
    c17 = product('c17')
    r = library()
    for issue in [2, 10]:
        r = c17.act(r, 'add-entry', {'title': 'Moon', 'type': 'comic', 'volume': 1, 'issue': issue})
    assert r['entries'][0].get('displayTitle') != r['entries'][1].get('displayTitle')
    assert '10' in r['entries'][1]['displayTitle']

def test_large_valid_collection_backup_can_be_restored():
    c17 = product('c17')
    record = library()
    record['entries'] = [c17.entry({'title': f'Book {i}', 'notes': 'n' * 4000}) for i in range(520)]
    content = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
    assert 2 * 1024 * 1024 < len(content.encode()) < 12 * 1024 * 1024
    restored = c17.act(library(), 'restore-backup', {'content': content})
    assert len(restored['entries']) == 520
    assert all((e['notes'] == 'n' * 4000 for e in restored['entries']))

def test_catalog_preview_rejects_huge_numbers_without_losing_valid_rows():
    c17 = product('c17')
    raw = 'type,title,volume\ncomic,Valid,2\ncomic,Too large,' + '9' * 5000
    preview = c17.act(library(), 'preview-import', {'content': raw})['importPreview']
    assert [e['title'] for e in preview['entries']] == ['Valid']
    assert len(preview['rejected']) == 1 and preview['rejected'][0]['row'] == 3

@pytest.mark.parametrize('query', ['0-306-40615-2', '0306406152', '978-0-306-40615-7', '9780306406157'])
def test_catalog_search_matches_equivalent_valid_isbn_forms(query):
    c17 = product('c17')
    record = c17.act(library(), 'add-entry', {'title': 'Physics', 'isbn': '0-306-40615-2'})
    results = c17.act(record, 'query', {'search': query})['queryResults']
    assert len(results) == 1 and results[0]['title'] == 'Physics'
    assert c17.act(record, 'query', {'search': 'physics'})['queryResults'][0]['id'] == results[0]['id']
