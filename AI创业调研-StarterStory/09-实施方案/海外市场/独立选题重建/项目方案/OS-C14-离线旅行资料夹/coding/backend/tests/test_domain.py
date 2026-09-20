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
    (exported, mime, name) = c14.export(r, 'ics')
    reread = c14.act(travel(), 'preview-import', {'format': 'ics', 'content': exported.decode()})
    assert len(reread['importPreview']['entries']) == 1
    assert mime.startswith('text/calendar')

def test_calendar_rejects_recurrence_dst_ambiguity_and_impossible_local_time():
    c14 = product('c14')
    template = 'BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nSUMMARY:Meet\nDTSTART;TZID=America/New_York:{start}\nDTEND;TZID=America/New_York:{end}\n{extra}END:VEVENT\nEND:VCALENDAR'
    for (start, end, extra) in [('20261101T013000', '20261101T023000', ''), ('20260308T023000', '20260308T033000', ''), ('20261001T120000', '20261001T130000', 'RRULE:FREQ=DAILY\n')]:
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
    (archive, _, _) = c14.export(r, 'zip')
    z = zipfile.ZipFile(io.BytesIO(archive))
    assert z.read(next((n for n in z.namelist() if n.endswith('ticket.pdf')))) == raw
    (data, _, _) = c14.export(r, 'csv')
    r2 = c14.act(travel(), 'preview-import', {'format': 'csv', 'content': data.decode()})
    assert r2['importPreview']['entries'][0]['reference'] == 'AB12'
    for attachment in [{'name': '../x.pdf', 'contentType': 'application/pdf', 'base64': base64.b64encode(raw).decode()}, {'name': 'fake.pdf', 'contentType': 'application/pdf', 'base64': base64.b64encode(b'<script>x</script>').decode()}, {'name': 'big.txt', 'contentType': 'text/plain', 'base64': base64.b64encode(b'x' * (500 * 1024 + 1)).decode()}]:
        with pytest.raises(HTTPException):
            c14.act(r, 'add-attachment', {'file': attachment})

def test_domain_actions_never_mutate_input_records_on_error_or_success():
    for (code, record, action, body) in [('c14', travel(), 'add-entry', {'title': 'Bad', 'start': 'bad'})]:
        before = deepcopy(record)
        try:
            product(code).act(record, action, body)
        except HTTPException:
            pass
        assert record == before

@pytest.mark.parametrize('code,body', [('c14', {'title': 'Trip', 'timezone': []})])
def test_invalid_create_fields_produce_validation_errors(code, body):
    with pytest.raises(HTTPException) as error:
        product(code).create(body)
    assert error.value.status_code == 422

def test_calendar_import_preserves_line_folding_and_rejects_malformed_csv_rows():
    c14 = product('c14')
    r = c14.act(travel(), 'add-entry', {'title': '旅行' * 70, 'start': '2026-10-01T09:00:00+09:00', 'end': '2026-10-01T12:00:00+09:00', 'timezone': 'Asia/Tokyo', 'notes': 'Line 1\nLine 2, with; punctuation'})
    (raw, _, _) = c14.export(r, 'ics')
    assert all((len(line) <= 75 for line in raw.split(b'\r\n')))
    restored = c14.act(travel(), 'preview-import', {'format': 'ics', 'content': raw.decode()})
    assert restored['importPreview']['entries'][0]['title'] == r['entries'][0]['title']
    assert restored['importPreview']['entries'][0]['notes'] == r['entries'][0]['notes']
    malformed = 'title,start,end,timezone\nFlight,2026-10-01T09:00:00+09:00,2026-10-01T12:00:00+09:00,Asia/Tokyo,extra'
    preview = c14.act(travel(), 'preview-import', {'format': 'csv', 'content': malformed})['importPreview']
    assert not preview['entries'] and len(preview['rejected']) == 1

def test_consumer_specs_reference_supported_actions_and_explicit_business_fields():
    from pathlib import Path
    for code in ['c14']:
        spec = json.loads((Path(__file__).parents[2] / 'shared' / 'products' / (code + '.json')).read_text())
        assert spec['code'] == code.upper()
        assert spec['create'][0]['name'] == 'title'
        assert all((f['type'] != 'json' for a in spec['actions'] for f in a['fields']))
        assert len({a['id'] for a in spec['actions']}) == len(spec['actions'])

def test_travel_csv_rejects_blank_timezone_instead_of_guessing():
    c14 = product('c14')
    raw = 'title,start,end,timezone\nFlight,2026-10-01T09:00:00+09:00,2026-10-01T12:00:00+09:00,\n'
    preview = c14.act(travel(), 'preview-import', {'format': 'csv', 'content': raw})['importPreview']
    assert preview['entries'] == []
    assert len(preview['rejected']) == 1

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
