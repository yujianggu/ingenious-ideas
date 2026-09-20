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

def packing():
    return product('c15').create({'title': 'Family packing'})

def add_module(r, title, items):
    c15 = product('c15')
    r = c15.act(r, 'add-module', {'title': title})
    module_id = r['modules'][-1]['id']
    for (label, quantity, rule) in items:
        r = c15.act(r, 'add-module-item', {'moduleId': module_id, 'label': label, 'quantity': quantity, 'rule': rule})
    return r

def test_packing_merges_canonical_labels_and_preserves_quantity_rules():
    c15 = product('c15')
    r = add_module(packing(), 'Basics', [('Socks', 2, 'person'), ('Camera', 1, 'fixed')])
    r = add_module(r, 'Hiking', [('  socks ', 3, 'person'), ('Socks', 1, 'day')])
    r = c15.act(r, 'new-trip', {'title': 'Hill trip', 'people': 2, 'days': 4})
    assert len(r['items']) == 2
    socks = next((i for i in r['items'] if i['label'] == 'Socks'))
    assert socks['quantity'] == 10
    assert len(socks['rules']) == 2
    assert r['currentTrip']['people'] == 2

def test_packing_module_edits_and_new_trip_do_not_mutate_previous_snapshots():
    c15 = product('c15')
    r = add_module(packing(), 'Basics', [('Socks', 2, 'person')])
    r = c15.act(r, 'new-trip', {'title': 'First', 'people': 2, 'days': 3})
    original = deepcopy(r['currentTrip']['modules'])
    item_id = r['items'][0]['id']
    r = c15.act(r, 'toggle-item', {'itemId': item_id, 'checked': True})
    assert r['currentTrip']['status'] == 'packed'
    r = c15.act(r, 'add-module-item', {'moduleId': r['modules'][0]['id'], 'label': 'Hat', 'quantity': 1, 'rule': 'person'})
    assert r['currentTrip']['modules'] == original
    r = c15.act(r, 'new-trip', {'title': 'Second', 'people': 1, 'days': 2})
    assert len(r['items']) == 2
    assert r['history'][0]['items'][0]['checked'] is True
    assert r['history'][0]['modules'] == original
    r = c15.act(r, 'toggle-item', {'itemId': r['items'][0]['id'], 'checked': True})
    r = c15.act(r, 'reset-checks', {})
    assert all((i['checked'] is False for i in r['items']))
    assert r['history'][0]['items'][0]['checked'] is True

def test_packing_selection_validations_and_backup_restore():
    c15 = product('c15')
    r = add_module(packing(), 'Basics', [('Passport', 1, 'person')])
    with pytest.raises(HTTPException):
        c15.act(r, 'new-trip', {'title': 'Bad', 'people': 0, 'days': 2})
    r = c15.act(r, 'set-module-selection', {'moduleId': r['modules'][0]['id'], 'selected': False})
    with pytest.raises(HTTPException):
        c15.act(r, 'new-trip', {'title': 'Empty', 'people': 1, 'days': 2})
    r = c15.act(r, 'set-module-selection', {'moduleId': r['modules'][0]['id'], 'selected': True})
    r = c15.act(r, 'new-trip', {'title': 'Trip', 'people': 1, 'days': 2})
    with pytest.raises(HTTPException):
        c15.act(r, 'toggle-item', {'itemId': r['items'][0]['id'], 'checked': 'false'})
    restored = c15.act(packing(), 'restore-backup', {'content': json.dumps(r)})
    assert restored['modules'][0]['title'] == 'Basics'
    assert restored['items'][0]['label'] == 'Passport'

def test_domain_actions_never_mutate_input_records_on_error_or_success():
    for (code, record, action, body) in [('c15', packing(), 'add-module', {'title': 'Basics'})]:
        before = deepcopy(record)
        try:
            product(code).act(record, action, body)
        except HTTPException:
            pass
        assert record == before

@pytest.mark.parametrize('code,body', [('c15', {'title': []})])
def test_invalid_create_fields_produce_validation_errors(code, body):
    with pytest.raises(HTTPException) as error:
        product(code).create(body)
    assert error.value.status_code == 422

def test_packing_restore_rejects_tampered_quantities_and_preserves_root_metadata():
    c15 = product('c15')
    original = add_module(packing(), 'Basic', [('Passport', 1, 'person')])
    original = c15.act(original, 'new-trip', {'title': 'Trip', 'people': 2, 'days': 2})
    target = {**packing(), 'id': 'server-id', 'ownerId': 'real-owner', 'revision': 5}
    backup = {**original, 'id': 'other-id', 'ownerId': 'other-owner', 'revision': 99}
    restored = c15.act(target, 'restore-backup', {'content': json.dumps(backup)})
    assert (restored['id'], restored['ownerId'], restored['revision']) == ('server-id', 'real-owner', 5)
    backup['items'][0]['quantity'] = 999
    with pytest.raises(HTTPException):
        c15.act(target, 'restore-backup', {'content': json.dumps(backup)})

def test_consumer_specs_reference_supported_actions_and_explicit_business_fields():
    from pathlib import Path
    for code in ['c15']:
        spec = json.loads((Path(__file__).parents[2] / 'shared' / 'products' / (code + '.json')).read_text())
        assert spec['code'] == code.upper()
        assert spec['create'][0]['name'] == 'title'
        assert all((f['type'] != 'json' for a in spec['actions'] for f in a['fields']))
        assert len({a['id'] for a in spec['actions']}) == len(spec['actions'])

def test_large_valid_packing_backup_preserves_archived_checkmarks():
    c15 = product('c15')
    record = add_module(packing(), 'Gear', [(f'{i} ' + 'x' * 180, 1, 'fixed') for i in range(60)])
    record = c15.act(record, 'new-trip', {'title': 'Trip 0', 'people': 1, 'days': 2})
    record = c15.act(record, 'toggle-item', {'itemId': record['items'][0]['id'], 'checked': True})
    for i in range(1, 60):
        record = c15.act(record, 'new-trip', {'title': f'Trip {i}', 'people': 1, 'days': 2})
    content = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
    assert 2 * 1024 * 1024 < len(content.encode()) < 12 * 1024 * 1024
    restored = c15.act(packing(), 'restore-backup', {'content': content})
    assert len(restored['history']) == 59 and len(restored['items']) == 60
    assert restored['history'][0]['items'][0]['checked'] is True
    assert restored['currentTrip']['title'] == 'Trip 59'
