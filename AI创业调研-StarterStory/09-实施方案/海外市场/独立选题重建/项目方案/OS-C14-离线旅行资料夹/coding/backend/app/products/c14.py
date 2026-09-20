"""Offline travel dossier: explicit dates, deterministic imports, original documents."""
import base64
import binascii
import csv
import io
import re
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from app.store import require, fail, ident, now

FIELDS = ['title', 'kind', 'start', 'end', 'timezone', 'location', 'reference', 'notes']
KINDS = {'flight', 'hotel', 'train', 'car', 'activity', 'other'}
LIMIT = 500 * 1024


def string(value, label, required=False, maximum=4000):
    require(isinstance(value, str), f'{label} must be text')
    value = value.strip()
    require(len(value) <= maximum and (value or not required), f'{label} is required and must be at most {maximum} characters' if required else f'{label} is too long')
    return value


def zone(value):
    value = string(value, 'Timezone', True, 100)
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        fail(422, 'Use an IANA timezone such as Asia/Tokyo or Europe/London')


def date(value, tz):
    value = string(value, 'Date/time', True, 100)
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        fail(422, 'Date/time must be ISO 8601, including an explicit UTC offset')
    require(d.tzinfo is not None and d.utcoffset() is not None, 'Date/time must include an explicit UTC offset')
    require(d.utcoffset() == d.astimezone(tz).utcoffset(), 'UTC offset does not match the named timezone at this date/time')
    return d


def entry(body, default_zone='UTC'):
    require(isinstance(body, dict), 'Entry must be an object')
    title = string(body.get('title', ''), 'Entry title', True, 200)
    tz = zone(body.get('timezone') or default_zone)
    start, end = date(body.get('start', ''), tz), date(body.get('end', ''), tz)
    require(end >= start, 'End must not be before start')
    kind = body.get('kind') or 'other'
    require(isinstance(kind, str) and kind in KINDS, 'Unknown itinerary kind')
    return dict(id=ident(), title=title, kind=kind, start=start.isoformat(), end=end.isoformat(), timezone=tz.key,
                location=string(body.get('location', ''), 'Location', maximum=500), reference=string(body.get('reference', ''), 'Reference', maximum=200), notes=string(body.get('notes', ''), 'Notes'))


def fingerprint(e):
    return (e['title'].casefold(), datetime.fromisoformat(e['start']).astimezone(timezone.utc).isoformat(), datetime.fromisoformat(e['end']).astimezone(timezone.utc).isoformat(), e['location'].casefold())


def create(body):
    return dict(title=string(body.get('title', ''), 'Trip title', True, 200), destination=string(body.get('destination', ''), 'Destination', maximum=200), timezone=zone(body.get('timezone', 'UTC')).key, notes=string(body.get('notes', ''), 'Notes'), entries=[], attachments=[], importPreview={'entries': [], 'rejected': []})


def csv_safe(value):
    s = str(value)
    return "'" + s if s.startswith(('=', '+', '-', '@', '\t', '\r', "'")) else s


def csv_read(value):
    return value[1:] if isinstance(value, str) and len(value) > 1 and value[0] == "'" and value[1] in "=+-@\t\r'" else value


def ics_unescape(s):
    return re.sub(r'\\([nN,;\\])', lambda m: '\n' if m[1] in 'nN' else m[1], s)


def calendar_date(params, value):
    require(params.get('VALUE', 'DATE-TIME') == 'DATE-TIME', 'All-day dates need explicit start/end times before import')
    try:
        if value.endswith('Z'):
            require('TZID' not in params, 'UTC values must not also specify TZID')
            return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc), 'UTC'
        tz = zone(params.get('TZID', ''))
        naive = datetime.strptime(value, '%Y%m%dT%H%M%S')
        aware = naive.replace(tzinfo=tz)
        require(aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == naive, 'Nonexistent local time at daylight-saving transition')
        require(aware.utcoffset() == naive.replace(tzinfo=tz, fold=1).utcoffset(), 'Ambiguous local time: use a UTC value')
        return aware, tz.key
    except ValueError:
        fail(422, 'Invalid calendar date/time')


def parse_event(lines):
    props = {}
    for line in lines:
        require(':' in line, 'Invalid calendar property')
        left, value = line.split(':', 1)
        parts = left.split(';')
        name = parts[0].upper()
        require(name not in {'RRULE', 'RDATE', 'EXDATE', 'RECURRENCE-ID', 'DURATION', 'BEGIN', 'END'}, 'Recurring, duration-only, or nested events require manual entry')
        params = {}
        for part in parts[1:]:
            require('=' in part, 'Invalid calendar parameter')
            k, v = part.split('=', 1)
            params[k.upper()] = v.strip('"')
        if name in {'SUMMARY', 'DTSTART', 'DTEND', 'LOCATION', 'DESCRIPTION', 'UID', 'STATUS'}:
            require(name not in props, f'Duplicate {name} is ambiguous')
            props[name] = (params, value)
    require(all(k in props for k in ('SUMMARY', 'DTSTART', 'DTEND')), 'SUMMARY, DTSTART and DTEND are required')
    require(props.get('STATUS', ({}, ''))[1].upper() != 'CANCELLED', 'Cancelled events are not added')
    start, tz = calendar_date(*props['DTSTART'])
    end, _ = calendar_date(*props['DTEND'])
    end = end.astimezone(zone(tz))
    return dict(title=ics_unescape(props['SUMMARY'][1]), kind='other', start=start.isoformat(), end=end.isoformat(), timezone=tz, location=ics_unescape(props.get('LOCATION', ({}, ''))[1]), notes=ics_unescape(props.get('DESCRIPTION', ({}, ''))[1]), reference=ics_unescape(props.get('UID', ({}, ''))[1]))


def preview(record, body):
    content = string(body.get('content', ''), 'Import content', True, LIMIT)
    fmt = body.get('format')
    rows, rejected = [], []
    if fmt == 'csv':
        try:
            reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')), strict=True)
            require(reader.fieldnames is not None and all(k in reader.fieldnames for k in ('title', 'start', 'end', 'timezone')), 'CSV requires title,start,end,timezone columns; optional kind,location,reference,notes')
            require(len(reader.fieldnames) == len(set(reader.fieldnames)), 'CSV has duplicate column names')
            rows = [(n, {k: csv_read(v) for k, v in row.items()}) for n, row in enumerate(reader, 2)]
        except csv.Error as e:
            fail(422, f'Invalid CSV: {e}')
    elif fmt == 'ics':
        lines = re.sub(r'\r?\n[ \t]', '', content).replace('\r\n', '\n').split('\n')
        require(lines[0].strip() == 'BEGIN:VCALENDAR' and lines[-1].strip() == 'END:VCALENDAR', 'Import a complete VCALENDAR document')
        method, depth = '', 0
        for line in lines:
            name, _, value = line.partition(':')
            if name.upper() == 'BEGIN':
                depth += 1
            elif name.upper() == 'END':
                depth -= 1
            elif depth == 1 and name.upper() == 'METHOD':
                require(not method, 'Duplicate calendar METHOD is ambiguous')
                method = value.strip().upper()
        current = None
        for n, line in enumerate(lines, 1):
            if line == 'BEGIN:VEVENT':
                require(current is None, 'Nested VEVENT is invalid')
                current = []
            elif line == 'END:VEVENT':
                require(current is not None, 'Unexpected END:VEVENT')
                try:
                    require(method != 'CANCEL', 'Cancellation calendars are not added')
                    rows.append((n, parse_event(current)))
                except HTTPException as e:
                    rejected.append({'row': n, 'reason': e.detail})
                current = None
            elif current is not None:
                current.append(line)
        require(current is None, 'Unclosed VEVENT')
    else:
        fail(422, 'Choose csv or ics')
    require(len(rows) + len(rejected) <= 200, 'Import at most 200 entries at once')
    accepted, seen = [], {fingerprint(e) for e in record['entries']}
    for row, candidate in rows:
        try:
            require(None not in candidate and all(v is not None for v in candidate.values()), 'Row has missing or extra CSV fields')
            if fmt == 'csv':
                require(bool(candidate.get('timezone', '').strip()), 'CSV timezone is required; no timezone is inferred')
            e = entry(candidate, record['timezone'])
            require(fingerprint(e) not in seen, 'Duplicate itinerary entry')
            seen.add(fingerprint(e))
            accepted.append(e)
        except HTTPException as error:
            rejected.append({'row': row, 'reason': error.detail})
    return {'entries': accepted, 'rejected': rejected, 'format': fmt}


def attachment(body):
    f = body.get('file')
    require(isinstance(f, dict), 'Choose an original file')
    name = string(f.get('name', ''), 'File name', True, 150)
    require(not any(c in name for c in '/\\\x00') and name not in {'.', '..'} and not any(ord(c) < 32 for c in name), 'Unsafe file name')
    mime = f.get('contentType')
    allowed = {'application/pdf': ('.pdf',), 'image/png': ('.png',), 'image/jpeg': ('.jpg', '.jpeg'), 'text/plain': ('.txt',), 'text/calendar': ('.ics',)}
    require(isinstance(mime, str) and mime in allowed and name.lower().endswith(allowed[mime]), 'Choose a PDF, PNG, JPEG, TXT or ICS file with matching type and extension')
    encoded = f.get('base64')
    require(isinstance(encoded, str) and len(encoded) <= 4 * ((LIMIT + 2) // 3), 'File limit is 500 KiB')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        fail(422, 'Invalid base64 file')
    require(0 < len(data) <= LIMIT, 'File must contain 1 byte to 500 KiB')
    if mime == 'application/pdf':
        require(data.startswith(b'%PDF-'), 'PDF signature is missing')
    elif mime == 'image/png':
        require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'PNG signature is missing')
    elif mime == 'image/jpeg':
        require(data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'), 'JPEG signature is missing')
    else:
        try:
            decoded = data.decode('utf-8')
        except UnicodeDecodeError:
            fail(422, 'Text attachments must be UTF-8')
        require('\x00' not in decoded, 'Text attachment contains binary data')
        if mime == 'text/calendar':
            require('BEGIN:VCALENDAR' in decoded and 'END:VCALENDAR' in decoded, 'Calendar file is incomplete')
    return dict(id=ident(), name=name, contentType=mime, base64=base64.b64encode(data).decode(), size=len(data))


def act(record, action, body):
    r = deepcopy(record)
    if action == 'add-entry':
        e = entry(body, r['timezone'])
        require(fingerprint(e) not in {fingerprint(x) for x in r['entries']}, 'Duplicate itinerary entry')
        r['entries'].append(e)
    elif action == 'remove-entry':
        require(any(e['id'] == body.get('entryId') for e in r['entries']), 'Entry not found', 404)
        r['entries'] = [e for e in r['entries'] if e['id'] != body['entryId']]
    elif action == 'preview-import':
        r['importPreview'] = preview(r, body)
    elif action == 'commit-import':
        require(isinstance(r.get('importPreview'), dict), 'Preview an import first')
        seen = {fingerprint(e) for e in r['entries']}
        for e in r['importPreview']['entries']:
            if fingerprint(e) not in seen:
                r['entries'].append(e)
                seen.add(fingerprint(e))
        r['importPreview']['entries'] = []
    elif action == 'add-attachment':
        require(len(r['attachments']) < 20, 'A dossier supports up to 20 original files')
        r['attachments'].append(attachment(body))
    elif action == 'remove-attachment':
        require(any(a['id'] == body.get('attachmentId') for a in r['attachments']), 'Attachment not found', 404)
        r['attachments'] = [a for a in r['attachments'] if a['id'] != body['attachmentId']]
    else:
        fail(422, 'Unknown travel action')
    require(len(r['entries']) <= 500, 'A dossier supports up to 500 itinerary entries')
    r['entries'].sort(key=lambda e: datetime.fromisoformat(e['start']).astimezone(timezone.utc))
    return r


def ics_escape(value):
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '').replace(';', '\\;').replace(',', '\\,')


def fold_line(value):
    lines, current = [], ''
    for character in value:
        if len((current + character).encode()) > 75:
            lines.append(current)
            current = ' '
        current += character
    return '\r\n'.join(lines + [current])


def export(record, format):
    if format == 'csv':
        stream = io.StringIO(newline='')
        w = csv.DictWriter(stream, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: csv_safe(e[k]) for k in FIELDS} for e in record['entries'])
        return stream.getvalue().encode(), 'text/csv; charset=utf-8', 'itinerary.csv'
    if format == 'ics':
        lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Travel Dossier//EN', 'CALSCALE:GREGORIAN']
        for e in record['entries']:
            lines += ['BEGIN:VEVENT', 'UID:' + e['id'] + '@travel-dossier', 'DTSTAMP:' + datetime.fromisoformat(record.get('updatedAt', now())).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'SUMMARY:' + ics_escape(e['title']), 'DTSTART:' + datetime.fromisoformat(e['start']).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'DTEND:' + datetime.fromisoformat(e['end']).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'LOCATION:' + ics_escape(e['location']), 'DESCRIPTION:' + ics_escape(e['notes']), 'END:VEVENT']
        lines += ['END:VCALENDAR']
        return ('\r\n'.join(fold_line(x) for x in lines) + '\r\n').encode(), 'text/calendar; charset=utf-8', 'itinerary.ics'
    if format == 'txt':
        lines = [record['title'], record['destination'], record['notes'], '']
        for e in record['entries']:
            lines += [e['title'], f"{e['start']} — {e['end']} ({e['timezone']})", e['location'], e['reference'], e['notes'], '']
        lines += ['Original attachments:'] + [f"{a['name']} ({a['size']} bytes)" for a in record['attachments']]
        return '\n'.join(lines).encode(), 'text/plain; charset=utf-8', 'itinerary.txt'
    if format == 'zip':
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('itinerary.txt', export(record, 'txt')[0])
            z.writestr('itinerary.ics', export(record, 'ics')[0])
            for a in record['attachments']:
                z.writestr('originals/' + a['id'] + '-' + a['name'], base64.b64decode(a['base64']))
        return buf.getvalue(), 'application/zip', 'travel-dossier.zip'
    fail(422, 'Choose csv, ics, txt or zip')
