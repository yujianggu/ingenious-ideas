"""Mixed book/comic/magazine collection with validated bibliographic imports."""
import csv
import io
import json
import re
import unicodedata
from copy import deepcopy
from fastapi import HTTPException
from app.store import require, fail, ident

FIELDS = ['type', 'title', 'volume', 'issue', 'isbn', 'location', 'notes']
TYPES = {'book', 'comic', 'magazine'}


def text(value, label, required=False, maximum=200):
    require(isinstance(value, str), f'{label} must be text')
    value = value.strip()
    require(len(value) <= maximum and (value or not required), f'{label} must contain {1 if required else 0}–{maximum} characters')
    return value


def canonical(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def number(value, label, optional=True):
    if optional and (value is None or value == ''):
        return None
    require(type(value) is int and 1 <= value <= 100000, f'{label} must be a whole number from 1 to 100000')
    return value


def isbn(value):
    raw = text(value, 'ISBN', maximum=30)
    value = re.sub(r'[\s-]', '', raw).upper()
    if not value:
        return ''
    if len(value) == 10:
        require(value.isascii() and value[:9].isdigit() and (value[-1].isdigit() or value[-1] == 'X'), 'Invalid ISBN-10')
        digits = [int(x) for x in value[:9]] + [10 if value[-1] == 'X' else int(value[-1])]
        require(sum((10 - n) * d for n, d in enumerate(digits)) % 11 == 0, 'ISBN-10 checksum is invalid')
        base = '978' + value[:9]
        return base + str((-sum(int(x) * (1 if n % 2 == 0 else 3) for n, x in enumerate(base))) % 10)
    require(len(value) == 13 and value.isascii() and value.isdigit() and value.startswith(('978', '979')), 'ISBN must contain 10 or 13 digits, with a valid ISBN prefix')
    require(sum(int(x) * (1 if n % 2 == 0 else 3) for n, x in enumerate(value)) % 10 == 0, 'ISBN-13 checksum is invalid')
    return value


def entry(body):
    require(isinstance(body, dict), 'Catalog entry must be an object')
    kind = body.get('type', 'book')
    require(isinstance(kind, str) and kind in TYPES, 'Choose book, comic or magazine')
    result = dict(id=ident(), type=kind, title=text(body.get('title', ''), 'Title', True), volume=number(body.get('volume'), 'Volume'), issue=number(body.get('issue'), 'Issue'), isbn=isbn(body.get('isbn', '')), location=text(body.get('location', ''), 'Location'), notes=text(body.get('notes', ''), 'Notes', maximum=4000))
    parts = [result['title'], kind]
    if result['volume'] is not None:
        parts.append(f"vol. {result['volume']}")
    if result['issue'] is not None:
        parts.append(f"issue {result['issue']}")
    result['displayTitle'] = ' · '.join(parts)
    return result


def duplicate(e, entries):
    key = (e['type'], canonical(e['title']), e['volume'], e['issue'])
    return any((e['isbn'] and e['isbn'] == x['isbn']) or (x['type'], canonical(x['title']), x['volume'], x['issue']) == key for x in entries)


def sort_entries(entries):
    entries.sort(key=lambda e: (canonical(e['title']), e['type'], e['volume'] or 0, e['issue'] or 0))


def create(body):
    return dict(title=text(body.get('title', ''), 'Collection title', True), entries=[], importPreview={'entries': [], 'rejected': []}, queryResults=[], gapResults=[])


def csv_safe(value):
    s = '' if value is None else str(value)
    return "'" + s if s.startswith(('=', '+', '-', '@', '\t', '\r', "'")) else s


def csv_read(value):
    return value[1:] if isinstance(value, str) and len(value) > 1 and value[0] == "'" and value[1] in "=+-@\t\r'" else value


def csv_number(value, label):
    if value is None or value == '':
        return None
    require(isinstance(value, str) and value.isascii() and value.isdigit(), f'{label} must be a positive whole number')
    significant = value.lstrip('0') or '0'
    require(len(significant) <= 6, f'{label} must be a whole number from 1 to 100000')
    return number(int(significant), label)


def preview(r, body):
    content = text(body.get('content', ''), 'CSV content', True, 1024 * 1024)
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')), strict=True)
        require(reader.fieldnames is not None and all(k in reader.fieldnames for k in ['type', 'title']), 'CSV requires type,title; optional volume,issue,isbn,location,notes')
        require(len(reader.fieldnames) == len(set(reader.fieldnames)), 'CSV has duplicate column names')
        raw = list(reader)
    except csv.Error as e:
        fail(422, f'Invalid CSV: {e}')
    require(len(raw) <= 1000, 'Import at most 1000 entries at once')
    accepted, rejected = [], []
    for n, row in enumerate(raw, 2):
        try:
            require(None not in row and all(v is not None for v in row.values()), 'Row has missing or extra CSV fields')
            value = {k: csv_read(v) for k, v in row.items()}
            value['volume'] = csv_number(value.get('volume'), 'Volume')
            value['issue'] = csv_number(value.get('issue'), 'Issue')
            e = entry(value)
            require(not duplicate(e, r['entries'] + accepted), 'Duplicate ISBN or type/title/volume/issue')
            accepted.append(e)
        except HTTPException as error:
            rejected.append(dict(row=n, title=row.get('title', ''), reason=error.detail))
    return dict(entries=accepted, rejected=rejected)


def act(record, action, body):
    r = deepcopy(record)
    if action == 'add-entry':
        e = entry(body)
        require(not duplicate(e, r['entries']), 'Duplicate ISBN or type/title/volume/issue')
        r['entries'].append(e)
    elif action in {'move-entry', 'update-notes', 'remove-entry'}:
        e = next((e for e in r['entries'] if e['id'] == body.get('entryId')), None)
        require(e is not None, 'Catalog entry not found', 404)
        if action == 'move-entry':
            e['location'] = text(body.get('location', ''), 'Location')
        elif action == 'update-notes':
            e['notes'] = text(body.get('notes', ''), 'Notes', maximum=4000)
        else:
            r['entries'].remove(e)
    elif action == 'query':
        search = canonical(text(body.get('search', ''), 'Search'))
        try:
            isbn_search = isbn(search)
        except HTTPException:
            isbn_search = ''  # Ordinary title/notes queries need not be valid ISBNs.
        location = canonical(text(body.get('location', ''), 'Location'))
        kind = body.get('type', '')
        require(isinstance(kind, str) and kind in TYPES | {''}, 'Unknown catalog type')
        r['queryResults'] = [deepcopy(e) for e in r['entries'] if (not search or search in canonical(' '.join([e['title'], e['isbn'], e['notes']])) or (isbn_search and e['isbn'] == isbn_search)) and (not location or location == canonical(e['location'])) and (not kind or kind == e['type'])]
        r['query'] = dict(search=search, location=location, type=kind)
        return r
    elif action == 'find-gaps':
        title = text(body.get('title', ''), 'Series title', True)
        kind = body.get('type')
        require(isinstance(kind, str) and kind in {'comic', 'magazine'}, 'Gap queries apply to comics and magazines')
        volume = number(body.get('volume'), 'Volume')
        start, end = number(body.get('fromIssue', 1), 'First issue', False), number(body.get('toIssue'), 'Last issue', False)
        require(end >= start and end - start <= 1000, 'Choose an increasing issue range of at most 1001 issues')
        owned = {e['issue'] for e in r['entries'] if canonical(e['title']) == canonical(title) and e['type'] == kind and e['volume'] == volume}
        r['gapResults'] = [dict(title=title, type=kind, volume=volume, issue=i, status='Missing in this collection') for i in range(start, end + 1) if i not in owned]
        r['gapQuery'] = dict(title=title, type=kind, volume=volume, fromIssue=start, toIssue=end, note='Expected range supplied by you; this does not verify publisher release history.')
        return r
    elif action == 'preview-import':
        r['importPreview'] = preview(r, body)
        return r
    elif action == 'commit-import':
        for e in r['importPreview']['entries']:
            require(not duplicate(e, r['entries']), 'Collection changed since preview; create a fresh preview')
            r['entries'].append(e)
        r['importPreview']['entries'] = []
    elif action == 'restore-backup':
        content = text(body.get('content', ''), 'Backup content', True, 16 * 1024 * 1024)
        try:
            b = json.loads(content)
        except (ValueError, RecursionError):
            fail(422, 'Choose a valid collection JSON backup')
        require(isinstance(b, dict) and b.get('code', 'C17') == 'C17', 'Choose a C17 collection backup')
        entries = b.get('entries')
        require(isinstance(entries, list) and len(entries) <= 5000, 'Backup supports up to 5000 entries')
        result = []
        for raw in entries:
            e = entry(raw)
            require(not duplicate(e, result), 'Backup contains duplicate catalog entries')
            result.append(e)
        r['title'], r['entries'] = text(b.get('title', ''), 'Collection title', True), result
        r['importPreview'] = {'entries': [], 'rejected': []}
    else:
        fail(422, 'Unknown collection action')
    require(len(r['entries']) <= 5000, 'A collection supports up to 5000 entries')
    sort_entries(r['entries'])
    # Results are explicit snapshots: clear after mutations so stale results are never shown.
    r['queryResults'], r['gapResults'] = [], []
    r.pop('query', None)
    r.pop('gapQuery', None)
    return r


def export(record, format):
    if format == 'csv':
        stream = io.StringIO(newline='')
        w = csv.DictWriter(stream, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: csv_safe(e[k]) for k in FIELDS} for e in record['entries'])
        return stream.getvalue().encode(), 'text/csv; charset=utf-8', 'collection.csv'
    if format == 'txt':
        lines = [record['title'], '']
        for e in record['entries']:
            lines.append(f"{e['type']}: {e['title']} | volume {e['volume'] or '—'} | issue {e['issue'] or '—'} | ISBN {e['isbn'] or '—'} | {e['location']}")
        return '\n'.join(lines).encode(), 'text/plain; charset=utf-8', 'collection.txt'
    fail(422, 'Choose csv or txt; use the JSON backup for a full restore')
