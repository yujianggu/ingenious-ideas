"""Reusable packing modules and independently frozen trip checklists."""
import csv
import io
import json
import unicodedata
from copy import deepcopy
from app.store import require, fail, ident, now

RULES = {'fixed', 'person', 'day', 'person-day'}


def text(value, label, required=True, maximum=200):
    require(isinstance(value, str), f'{label} must be text')
    value = value.strip()
    require(len(value) <= maximum and (value or not required), f'{label} must contain {1 if required else 0}–{maximum} characters')
    return value


def canonical(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def number(value, label, minimum=1, maximum=1000):
    require(type(value) is int and minimum <= value <= maximum, f'{label} must be a whole number from {minimum} to {maximum}')
    return value


def boolean(value, label):
    require(type(value) is bool, f'{label} must be true or false')
    return value


def module_item(body):
    require(isinstance(body, dict), 'Module item must be an object')
    rule = body.get('rule', 'fixed')
    require(isinstance(rule, str) and rule in RULES, 'Choose fixed, person, day or person-day')
    return dict(id=ident(), label=text(body.get('label', ''), 'Item label'), quantity=number(body.get('quantity', 1), 'Quantity'), rule=rule)


def create(body):
    return dict(title=text(body.get('title', ''), 'Packing workspace title'), modules=[], items=[], currentTrip=None, history=[])


def find_module(r, module_id):
    found = next((m for m in r['modules'] if m['id'] == module_id), None)
    require(found is not None, 'Module not found', 404)
    return found


def merge(modules, people, days):
    grouped = {}
    factors = {'fixed': 1, 'person': people, 'day': days, 'person-day': people * days}
    for module in modules:
        for item in module['items']:
            key = canonical(item['label'])
            merged = grouped.setdefault(key, dict(id=ident(), label=item['label'], quantity=0, checked=False, rules=[]))
            rule = next((x for x in merged['rules'] if x['rule'] == item['rule']), None)
            if rule is None:
                rule = dict(rule=item['rule'], baseQuantity=item['quantity'], modules=[module['title']])
                merged['rules'].append(rule)
            else:
                rule['baseQuantity'] = max(rule['baseQuantity'], item['quantity'])
                if module['title'] not in rule['modules']:
                    rule['modules'].append(module['title'])
    for item in grouped.values():
        for rule in item['rules']:
            rule['quantity'] = rule['baseQuantity'] * factors[rule['rule']]
        item['quantity'] = sum(x['quantity'] for x in item['rules'])
    return list(grouped.values())


def restore_modules(value):
    require(isinstance(value, list) and len(value) <= 50, 'Backup supports up to 50 modules')
    result, titles = [], set()
    for m in value:
        require(isinstance(m, dict), 'Invalid backup module')
        title = text(m.get('title', ''), 'Module title')
        require(canonical(title) not in titles, 'Duplicate module title in backup')
        titles.add(canonical(title))
        raw_items = m.get('items')
        require(isinstance(raw_items, list) and len(raw_items) <= 200, 'Module supports up to 200 items')
        items, keys = [], set()
        for raw in raw_items:
            i = module_item(raw)
            key = (canonical(i['label']), i['rule'])
            require(key not in keys, 'Duplicate item and quantity rule in backup module')
            keys.add(key)
            items.append(i)
        result.append(dict(id=ident(), title=title, selected=boolean(m.get('selected', True), 'Selected'), items=items))
    return result


def restore_trip(value, raw_items):
    require(isinstance(value, dict), 'Invalid trip snapshot')
    modules = restore_modules(value.get('modules'))
    people = number(value.get('people'), 'People', maximum=100)
    days = number(value.get('days'), 'Days', maximum=365)
    expected = merge(modules, people, days)
    require(isinstance(raw_items, list) and len(raw_items) == len(expected), 'Backup checklist does not match its frozen modules')
    by_label = {}
    for i in raw_items:
        require(isinstance(i, dict), 'Invalid checklist item')
        key = canonical(text(i.get('label', ''), 'Checklist label'))
        require(key not in by_label, 'Duplicate checklist item')
        by_label[key] = i
    for i in expected:
        saved = by_label.get(canonical(i['label']))
        require(saved is not None and type(saved.get('quantity')) is int and saved['quantity'] == i['quantity'], 'Backup quantity does not match its frozen rules')
        i['checked'] = boolean(saved.get('checked'), 'Checked')
    trip = dict(id=ident(), title=text(value.get('title', ''), 'Trip title'), people=people, days=days, createdAt=text(value.get('createdAt', now()), 'Created date', maximum=100), modules=modules, status='packed' if expected and all(i['checked'] for i in expected) else 'packing')
    return trip, expected


def restore(r, body):
    content = text(body.get('content', ''), 'Backup content', maximum=16 * 1024 * 1024)
    try:
        b = json.loads(content)
    except (ValueError, RecursionError):
        fail(422, 'Choose a valid packing JSON backup')
    require(isinstance(b, dict) and b.get('code', 'C15') == 'C15', 'Choose a C15 packing backup')
    r['title'] = text(b.get('title', ''), 'Workspace title')
    r['modules'] = restore_modules(b.get('modules'))
    r['currentTrip'], r['items'] = None, []
    if b.get('currentTrip') is not None:
        r['currentTrip'], r['items'] = restore_trip(b['currentTrip'], b.get('items'))
    else:
        require(b.get('items', []) == [], 'Checklist requires a trip snapshot')
    history = b.get('history', [])
    require(isinstance(history, list) and len(history) <= 100, 'Backup supports up to 100 archived trips')
    r['history'] = []
    for old in history:
        require(isinstance(old, dict), 'Invalid archived trip')
        trip, items = restore_trip(old, old.get('items'))
        r['history'].append({**trip, 'items': items})
    return r


def act(record, action, body):
    r = deepcopy(record)
    if action == 'add-module':
        require(len(r['modules']) < 50, 'A workspace supports up to 50 modules')
        title = text(body.get('title', ''), 'Module title')
        require(canonical(title) not in {canonical(m['title']) for m in r['modules']}, 'Module title already exists')
        r['modules'].append(dict(id=ident(), title=title, selected=True, items=[]))
    elif action == 'add-module-item':
        module = find_module(r, body.get('moduleId'))
        item = module_item(body)
        require(len(module['items']) < 200, 'A module supports up to 200 items')
        require(not any(canonical(i['label']) == canonical(item['label']) and i['rule'] == item['rule'] for i in module['items']), 'This item and quantity rule already exist in the module; remove it before replacing it')
        module['items'].append(item)
    elif action == 'remove-module-item':
        module = find_module(r, body.get('moduleId'))
        label, rule = text(body.get('label', ''), 'Item label'), body.get('rule')
        require(any(canonical(i['label']) == canonical(label) and i['rule'] == rule for i in module['items']), 'Module item not found', 404)
        module['items'] = [i for i in module['items'] if not (canonical(i['label']) == canonical(label) and i['rule'] == rule)]
    elif action == 'set-module-selection':
        find_module(r, body.get('moduleId'))['selected'] = boolean(body.get('selected'), 'Selected')
    elif action == 'remove-module':
        module = find_module(r, body.get('moduleId'))
        r['modules'].remove(module)
    elif action == 'new-trip':
        title = text(body.get('title', ''), 'Trip title')
        people = number(body.get('people', 1), 'People', maximum=100)
        days = number(body.get('days', 1), 'Days', maximum=365)
        modules = deepcopy([m for m in r['modules'] if m['selected']])
        require(modules and any(m['items'] for m in modules), 'Select at least one module containing items')
        if r['currentTrip'] is not None:
            require(len(r['history']) < 100, 'A workspace supports 100 archived trips; create another workspace')
            r['history'].append({**deepcopy(r['currentTrip']), 'items': deepcopy(r['items'])})
        r['currentTrip'] = dict(id=ident(), title=title, people=people, days=days, modules=modules, createdAt=now(), status='packing')
        r['items'] = merge(modules, people, days)
    elif action == 'toggle-item':
        require(r['currentTrip'] is not None, 'Create a trip first')
        item = next((i for i in r['items'] if i['id'] == body.get('itemId')), None)
        require(item is not None, 'Packing item not found', 404)
        item['checked'] = boolean(body.get('checked'), 'Checked')
        r['currentTrip']['status'] = 'packed' if all(i['checked'] for i in r['items']) else 'packing'
    elif action == 'reset-checks':
        require(r['currentTrip'] is not None, 'Create a trip first')
        for i in r['items']:
            i['checked'] = False
        r['currentTrip']['status'] = 'packing'
    elif action == 'restore-backup':
        return restore(r, body)
    else:
        fail(422, 'Unknown packing action')
    return r


def export(record, format):
    if format == 'csv':
        stream = io.StringIO(newline='')
        w = csv.writer(stream)
        w.writerow(['label', 'quantity', 'checked'])
        for i in record['items']:
            label = i['label']
            if label.startswith(('=', '+', '-', '@', '\t', '\r', "'")):
                label = "'" + label
            w.writerow([label, i['quantity'], str(i['checked']).lower()])
        return stream.getvalue().encode(), 'text/csv; charset=utf-8', 'packing-checklist.csv'
    if format == 'txt':
        title = record['currentTrip']['title'] if record['currentTrip'] else record['title']
        content = title + '\n\n' + '\n'.join(f"[{'x' if i['checked'] else ' '}] {i['label']} × {i['quantity']}" for i in record['items'])
        return content.encode(), 'text/plain; charset=utf-8', 'packing-checklist.txt'
    fail(422, 'Choose csv or txt; use the JSON backup for a full restore')
