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


def travel():
    return product('c14').create({'title': 'Japan', 'destination': 'Tokyo', 'timezone': 'Asia/Tokyo'})


def test_travel_requires_explicit_valid_timezone_and_offset():
    c14 = product('c14')
    with pytest.raises(HTTPException):
        c14.create({'title': 'Trip', 'timezone': 'Not/AZone'})
    r = travel()
    for start in ['2026-10-01T09:00:00', '2026-10-01T09:00:00+08:00']:
        with pytest.raises(HTTPException):
            c14.act(r, 'add-entry', {'title': 'Flight', 'start': start, 'end': '2026-10-01T12:00:00+09:00', 'timezone': 'Asia/Tokyo'})
    r = c14.act(r, 'add-entry', {'title': 'Flight', 'start': '2026-10-01T09:00:00+09:00', 'end': '2026-10-01T12:00:00+09:00', 'timezone': 'Asia/Tokyo'})
    assert r['entries'][0]['start'].endswith('+09:00')


def test_calendar_import_preview_rejects_floating_dates_and_commit_deduplicates():
    c14 = product('c14')
    text = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Flight\r\nDTSTART:20261001T000000Z\r\nDTEND:20261001T030000Z\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nSUMMARY:Floating\r\nDTSTART:20261002T120000\r\nDTEND:20261002T130000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
    r = c14.act(travel(), 'preview-import', {'format': 'ics', 'content': text})
    assert r['entries'] == []
    assert len(r['importPreview']['entries']) == 1
    assert len(r['importPreview']['rejected']) == 1
    r = c14.act(r, 'commit-import', {})
    assert r['entries'][0]['title'] == 'Flight'
    r = c14.act(r, 'preview-import', {'format': 'ics', 'content': text})
    r = c14.act(r, 'commit-import', {})
    assert len(r['entries']) == 1
    exported, mime, name = c14.export(r, 'ics')
    reread = c14.act(travel(), 'preview-import', {'format': 'ics', 'content': exported.decode()})
    assert len(reread['importPreview']['entries']) == 1
    assert mime.startswith('text/calendar')


def test_calendar_rejects_recurrence_dst_ambiguity_and_impossible_local_time():
    c14 = product('c14')
    template = 'BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nSUMMARY:Meet\nDTSTART;TZID=America/New_York:{start}\nDTEND;TZID=America/New_York:{end}\n{extra}END:VEVENT\nEND:VCALENDAR'
    for start, end, extra in [('20261101T013000', '20261101T023000', ''), ('20260308T023000', '20260308T033000', ''), ('20261001T120000', '20261001T130000', 'RRULE:FREQ=DAILY\n')]:
        r = c14.act(travel(), 'preview-import', {'format': 'ics', 'content': template.format(start=start, end=end, extra=extra)})
        assert not r['importPreview']['entries']
        assert len(r['importPreview']['rejected']) == 1


def test_travel_csv_roundtrip_and_original_attachment_archive():
    c14 = product('c14')
    r = c14.act(travel(), 'preview-import', {'format': 'csv', 'content': 'title,kind,start,end,timezone,location,reference,notes\nFlight,flight,2026-10-01T09:00:00+09:00,2026-10-01T12:00:00+09:00,Asia/Tokyo,NRT,AB12,Original booking\n'})
    r = c14.act(r, 'commit-import', {})
    raw = b'%PDF-1.4\nOriginal ticket bytes\n%%EOF'
    r = c14.act(r, 'add-attachment', {'file': {'name': 'ticket.pdf', 'contentType': 'application/pdf', 'base64': base64.b64encode(raw).decode()}})
    a = r['attachments'][0]
    assert a['size'] == len(raw) and base64.b64decode(a['base64']) == raw
    archive, _, _ = c14.export(r, 'zip')
    z = zipfile.ZipFile(io.BytesIO(archive))
    assert z.read(next(n for n in z.namelist() if n.endswith('ticket.pdf'))) == raw
    data, _, _ = c14.export(r, 'csv')
    r2 = c14.act(travel(), 'preview-import', {'format': 'csv', 'content': data.decode()})
    assert r2['importPreview']['entries'][0]['reference'] == 'AB12'
    for attachment in [{'name': '../x.pdf', 'contentType': 'application/pdf', 'base64': base64.b64encode(raw).decode()}, {'name': 'fake.pdf', 'contentType': 'application/pdf', 'base64': base64.b64encode(b'<script>x</script>').decode()}, {'name': 'big.txt', 'contentType': 'text/plain', 'base64': base64.b64encode(b'x' * (500 * 1024 + 1)).decode()}]:
        with pytest.raises(HTTPException):
            c14.act(r, 'add-attachment', {'file': attachment})


def packing():
    return product('c15').create({'title': 'Family packing'})


def add_module(r, title, items):
    c15 = product('c15')
    r = c15.act(r, 'add-module', {'title': title})
    module_id = r['modules'][-1]['id']
    for label, quantity, rule in items:
        r = c15.act(r, 'add-module-item', {'moduleId': module_id, 'label': label, 'quantity': quantity, 'rule': rule})
    return r


def test_packing_merges_canonical_labels_and_preserves_quantity_rules():
    c15 = product('c15')
    r = add_module(packing(), 'Basics', [('Socks', 2, 'person'), ('Camera', 1, 'fixed')])
    r = add_module(r, 'Hiking', [('  socks ', 3, 'person'), ('Socks', 1, 'day')])
    r = c15.act(r, 'new-trip', {'title': 'Hill trip', 'people': 2, 'days': 4})
    assert len(r['items']) == 2
    socks = next(i for i in r['items'] if i['label'] == 'Socks')
    assert socks['quantity'] == 10  # max(2,3) per person plus one per day
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
    assert all(i['checked'] is False for i in r['items'])
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
    raw, _, _ = c17.export(r, 'csv')
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    assert all(not row['title'].startswith(('=', '+', '-', '@')) for row in rows)
    r2 = c17.act(library(), 'preview-import', {'content': raw.decode()})
    assert r2['entries'] == []
    assert len(r2['importPreview']['entries']) == 3
    r2 = c17.act(r2, 'commit-import', {})
    assert [e['title'] for e in r2['entries']] == [e['title'] for e in r['entries']]
    assert all(e['notes'] == '@note' for e in r2['entries'])
    r2 = c17.act(r2, 'preview-import', {'content': raw.decode()})
    assert len(r2['importPreview']['rejected']) == 3
    restored = c17.act(library(), 'restore-backup', {'content': json.dumps(r)})
    assert [e['title'] for e in restored['entries']] == [e['title'] for e in r['entries']]


def test_domain_actions_never_mutate_input_records_on_error_or_success():
    for code, record, action, body in [('c14', travel(), 'add-entry', {'title': 'Bad', 'start': 'bad'}), ('c15', packing(), 'add-module', {'title': 'Basics'}), ('c17', library(), 'add-entry', {'title': 'Novel', 'type': 'book'})]:
        before = deepcopy(record)
        try:
            product(code).act(record, action, body)
        except HTTPException:
            pass
        assert record == before


@pytest.mark.parametrize('code,body', [('c14', {'title': 'Trip', 'timezone': []}), ('c15', {'title': []}), ('c17', {'title': []})])
def test_invalid_create_fields_produce_validation_errors(code, body):
    with pytest.raises(HTTPException) as error:
        product(code).create(body)
    assert error.value.status_code == 422


def test_calendar_import_preserves_line_folding_and_rejects_malformed_csv_rows():
    c14 = product('c14')
    r = c14.act(travel(), 'add-entry', {'title': '旅行' * 70, 'start': '2026-10-01T09:00:00+09:00', 'end': '2026-10-01T12:00:00+09:00', 'timezone': 'Asia/Tokyo', 'notes': 'Line 1\nLine 2, with; punctuation'})
    raw, _, _ = c14.export(r, 'ics')
    assert all(len(line) <= 75 for line in raw.split(b'\r\n'))
    restored = c14.act(travel(), 'preview-import', {'format': 'ics', 'content': raw.decode()})
    assert restored['importPreview']['entries'][0]['title'] == r['entries'][0]['title']
    assert restored['importPreview']['entries'][0]['notes'] == r['entries'][0]['notes']
    malformed = 'title,start,end,timezone\nFlight,2026-10-01T09:00:00+09:00,2026-10-01T12:00:00+09:00,Asia/Tokyo,extra'
    preview = c14.act(travel(), 'preview-import', {'format': 'csv', 'content': malformed})['importPreview']
    assert not preview['entries'] and len(preview['rejected']) == 1


def test_isbn_unicode_numerals_are_rejected_without_internal_errors():
    c17 = product('c17')
    for value in ['²030640615', '０306406152', '978０306406157']:
        with pytest.raises(HTTPException) as error:
            c17.act(library(), 'add-entry', {'title': 'Invalid', 'isbn': value})
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
    for code in ['c14', 'c15', 'c17']:
        spec = json.loads((Path(__file__).parents[2] / 'shared' / 'products' / (code + '.json')).read_text())
        assert spec['code'] == code.upper()
        assert spec['create'][0]['name'] == 'title'
        assert all(f['type'] != 'json' for a in spec['actions'] for f in a['fields'])
        assert len({a['id'] for a in spec['actions']}) == len(spec['actions'])


def test_travel_csv_rejects_blank_timezone_instead_of_guessing():
    c14 = product('c14')
    raw = 'title,start,end,timezone\nFlight,2026-10-01T09:00:00+09:00,2026-10-01T12:00:00+09:00,\n'
    preview = c14.act(travel(), 'preview-import', {'format': 'csv', 'content': raw})['importPreview']
    assert preview['entries'] == []
    assert len(preview['rejected']) == 1


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
    # Valid production entry objects; the collection is well below its 5,000-entry limit.
    record['entries'] = [c17.entry({'title': f'Book {i}', 'notes': 'n' * 4000}) for i in range(520)]
    content = json.dumps(record, ensure_ascii=False, separators=(',', ':'))
    assert 2 * 1024 * 1024 < len(content.encode()) < 12 * 1024 * 1024
    restored = c17.act(library(), 'restore-backup', {'content': content})
    assert len(restored['entries']) == 520
    assert all(e['notes'] == 'n' * 4000 for e in restored['entries'])


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


def test_catalog_preview_rejects_huge_numbers_without_losing_valid_rows():
    c17 = product('c17')
    raw = 'type,title,volume\ncomic,Valid,2\ncomic,Too large,' + '9' * 5000
    preview = c17.act(library(), 'preview-import', {'content': raw})['importPreview']
    assert [e['title'] for e in preview['entries']] == ['Valid']
    assert len(preview['rejected']) == 1 and preview['rejected'][0]['row'] == 3


@pytest.mark.parametrize('position', ['before', 'after'])
def test_calendar_cancellation_method_never_becomes_an_active_itinerary(position):
    c14 = product('c14')
    event = 'BEGIN:VEVENT\nUID:cancelled-1\nSUMMARY:Flight\nDTSTART:20261001T120000Z\nDTEND:20261001T130000Z\nEND:VEVENT\n'
    method = 'METHOD:CANCEL\n'
    raw = 'BEGIN:VCALENDAR\nVERSION:2.0\n' + (method + event if position == 'before' else event + method) + 'END:VCALENDAR'
    record = c14.act(travel(), 'preview-import', {'format': 'ics', 'content': raw})
    assert record['importPreview']['entries'] == []
    assert len(record['importPreview']['rejected']) == 1
    assert 'cancel' in record['importPreview']['rejected'][0]['reason'].lower()
    assert c14.act(record, 'commit-import', {})['entries'] == []


@pytest.mark.parametrize('query', ['0-306-40615-2', '0306406152', '978-0-306-40615-7', '9780306406157'])
def test_catalog_search_matches_equivalent_valid_isbn_forms(query):
    c17 = product('c17')
    record = c17.act(library(), 'add-entry', {'title': 'Physics', 'isbn': '0-306-40615-2'})
    results = c17.act(record, 'query', {'search': query})['queryResults']
    assert len(results) == 1 and results[0]['title'] == 'Physics'
    assert c17.act(record, 'query', {'search': 'physics'})['queryResults'][0]['id'] == results[0]['id']
