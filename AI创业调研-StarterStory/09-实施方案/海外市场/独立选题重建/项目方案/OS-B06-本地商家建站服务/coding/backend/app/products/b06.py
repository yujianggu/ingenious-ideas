"""Local cleaning websites: editable drafts, published snapshots and real inquiries."""
import copy
import csv
import io
import re
from html import escape
from urllib.parse import quote

from app.store import fail, ident, now, require


def _text(value, label, limit=1000, required=True):
    require(isinstance(value, str), f'{label} must be text')
    value = value.strip()
    require((bool(value) or not required) and len(value) <= limit, f'{label} must contain {"1" if required else "0"}-{limit} characters')
    require(not any(ord(ch) < 32 and ch not in '\n\t' for ch in value), f'{label} contains unsupported characters')
    return value


def _email(value):
    value = _text(value, 'Email', 254)
    require(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value), 'Enter a valid email address')
    return value


def _phone(value):
    value = _text(value, 'Phone', 40, False)
    require(not value or re.fullmatch(r'[+0-9() .-]{5,40}', value), 'Phone must contain a callable number')
    return value


def _areas(value):
    require(isinstance(value, str), 'Enter service areas, one per line')
    result = list(dict.fromkeys(_text(v, 'Service area', 100) for v in value.splitlines() if v.strip()))
    require(1 <= len(result) <= 30, 'Enter 1-30 service areas')
    return result


def _site(body, previous=None):
    site = copy.deepcopy(previous or {})
    for name, label, limit, required in [('title', 'Business name', 120, True), ('tagline', 'Headline', 160, False),
            ('about', 'About your business', 3000, True), ('hours', 'Business hours', 200, False),
            ('streetAddress', 'Public business address', 250, False)]:
        if name in body or previous is None:
            site[name] = _text(body.get(name, ''), label, limit, required)
    for name, validator, default in [('email', _email, ''), ('phone', _phone, ''), ('areas', _areas, '')]:
        if name in body or previous is None:
            site[name] = validator(body.get(name, default))
    site.setdefault('services', [])
    return site


def create(body):
    site = _site(body)
    return dict(title=site['title'], site=site, status='draft', publication=None, publicationVersion=0,
                publicPath='', inquiries=[], draftNotice='Draft edits become public only after Publish.')


def _find(items, item_id, label):
    result = next((item for item in items if item['id'] == item_id), None)
    require(result is not None, f'{label} not found', 404)
    return result


def act(record, action, body):
    record = copy.deepcopy(record)
    if action == 'update-site':
        record['site'] = _site(body, record['site'])
        record['title'] = record['site']['title']
    elif action in ('add-service', 'update-service'):
        services = record['site']['services']
        require(action != 'add-service' or len(services) < 20, 'A website supports up to 20 services')
        item = dict(title=_text(body.get('title'), 'Service name', 100),
                    description=_text(body.get('description'), 'Service description', 1500),
                    priceNote=_text(body.get('priceNote', ''), 'Price or quote note', 150, False))
        if action == 'add-service':
            services.append(dict(id=ident(), **item))
        else:
            _find(services, body.get('serviceId'), 'Service').update(item)
    elif action == 'remove-service':
        service = _find(record['site']['services'], body.get('serviceId'), 'Service')
        record['site']['services'].remove(service)
    elif action == 'publish':
        require(body.get('publicConsent') is True, 'Confirm that the business details are intended for public display')
        require(record['site']['services'], 'Add at least one service before publishing')
        record['publicationVersion'] += 1
        record['publication'] = dict(site=copy.deepcopy(record['site']), version=record['publicationVersion'], publishedAt=now())
        record['status'] = 'published'
        record['publicPath'] = '/api/public/sites/' + quote(record['id'], safe='')
    elif action == 'unpublish':
        record['publication'] = None
        record['status'] = 'draft'
        record['publicPath'] = ''
    elif action == 'follow-up':
        inquiry = _find(record['inquiries'], body.get('inquiryId'), 'Inquiry')
        status = body.get('status')
        require(status in ('new', 'contacted', 'quoted', 'booked', 'closed'), 'Choose a valid inquiry status')
        note = _text(body.get('note'), 'Follow-up note', 2000)
        inquiry['status'] = status
        inquiry['history'].append(dict(status=status, note=note, time=now()))
    else:
        fail(404, 'Unknown cleaning-site action')
    return record


def _published(record):
    require(record.get('status') == 'published' and record.get('publication'), 'Website not found', 404)
    return record['publication']['site']


def public_action(record, body):
    site = _published(record)
    require(body.get('consent') is True or body.get('consent') in ('on', 'true'), 'Consent to contact is required')
    require(not body.get('website'), 'Unable to accept this inquiry')
    require(len(record['inquiries']) < 10000, 'This business cannot accept more online inquiries; contact it directly')
    service = _find(site['services'], body.get('serviceId'), 'Published service')
    require(body.get('area') in site['areas'], 'Choose an available service area')
    name = _text(body.get('name'), 'Your name', 100)
    require(len(name) >= 2, 'Your name must contain at least two characters')
    message = _text(body.get('message'), 'Message', 2000)
    require(len(message) >= 10, 'Please describe your cleaning request in at least 10 characters')
    inquiry = dict(id=ident(), title=f"{name} - {service['title']}", name=name,
                   email=_email(body.get('email')), phone=_phone(body.get('phone', '')), message=message,
                   serviceId=service['id'], serviceTitle=service['title'], area=body['area'],
                   status='new', receivedAt=now(), consentAt=now(), publicationVersion=record['publication']['version'], history=[])
    record = copy.deepcopy(record)
    record['inquiries'].append(inquiry)
    return record


def _html(site, endpoint=None):
    e = lambda value: escape(str(value), quote=True)
    phone = f'<a href="tel:{e(re.sub(r"[^+0-9]", "", site["phone"]))}">{e(site["phone"])}</a>' if site['phone'] else ''
    services = ''.join(f'<article><h3>{e(s["title"])}</h3><p>{e(s["description"])}</p><strong>{e(s["priceNote"])}</strong></article>' for s in site['services'])
    areas = ''.join(f'<li>{e(area)}</li>' for area in site['areas'])
    if endpoint:
        service_options = ''.join(f'<option value="{e(s["id"])}">{e(s["title"])}</option>' for s in site['services'])
        area_options = ''.join(f'<option>{e(area)}</option>' for area in site['areas'])
        inquiry = f'''<form action="{e(endpoint)}" method="post">
        <div class="fields"><label>Your name<input name="name" autocomplete="name" required minlength="2" maxlength="100"></label>
        <label>Email<input name="email" type="email" autocomplete="email" required maxlength="254"></label>
        <label>Phone (optional)<input name="phone" type="tel" autocomplete="tel" maxlength="40"></label>
        <label>Service<select name="serviceId" aria-label="Service" required>{service_options}</select></label>
        <label>Service area<select name="area" aria-label="Service area" required>{area_options}</select></label></div>
        <label>What can we help with?<textarea name="message" required minlength="10" maxlength="2000" rows="4"></textarea></label>
        <div hidden aria-hidden="true"><label>Leave this field empty<input name="website" tabindex="-1" autocomplete="off"></label></div>
        <label class="consent"><input type="checkbox" name="consent" required> I agree that {e(site['title'])} may use these details to respond to my request.</label>
        <p class="fine">Your details are shared with this business for inquiry follow-up. Please do not include sensitive information.</p>
        <button type="submit">Request a quote</button></form>'''
    else:
        inquiry = '<p>Email or call us with your location and the cleaning service you need.</p>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="description" content="{e(site['tagline'] or site['about'][:150])}"><title>{e(site['title'])} | Local cleaning</title>
    <style>*{{box-sizing:border-box}}body{{margin:0;background:#f8faf6;color:#18362e;font:17px/1.6 system-ui,sans-serif}}a{{color:inherit}}header,main,footer{{max-width:1100px;margin:auto;padding:24px}}header{{display:flex;justify-content:space-between;gap:24px;align-items:center}}nav{{display:flex;gap:24px}}h1{{font-size:clamp(2.6rem,6vw,4.7rem);line-height:1.1;max-width:850px;margin:20px 0}}h2{{font-size:2rem;line-height:1.2}}h3{{font-size:1.3rem}}.hero{{padding:64px 0 72px}}.eyebrow{{text-transform:uppercase;letter-spacing:.15em;font-size:.75rem}}p{{white-space:pre-line;overflow-wrap:anywhere}}.intro{{max-width:680px;font-size:1.15rem}}.cta,button{{display:inline-block;background:#215b45;color:white;border:0;border-radius:7px;padding:13px 22px;text-decoration:none;font:inherit;font-weight:650;cursor:pointer}}.cards,.fields{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}}article{{border:1px solid #d6e2d8;border-radius:14px;background:white;padding:26px}}section{{margin-bottom:64px}}.areas{{display:flex;gap:12px;list-style:none;padding:0;flex-wrap:wrap}}.areas li{{padding:7px 16px;border-radius:50px;background:#e4eee5}}.contact{{background:#eaf1e8;border-radius:20px;padding:32px}}label{{display:block;margin:14px 0 6px;font-weight:600}}input,select,textarea{{display:block;font:inherit;color:#18362e;border:1px solid #8caa96;border-radius:7px;background:white;padding:10px;width:100%;margin-top:7px}}textarea{{resize:vertical}}.consent{{display:flex;gap:12px;align-items:flex-start;font-size:.9rem;font-weight:400}}input[type=checkbox]{{width:20px;min-width:20px;margin-top:5px}}.fine,footer{{font-size:.8rem;color:#4b665b}}.details{{display:flex;gap:20px;flex-wrap:wrap;margin:20px 0}}a:focus,button:focus,input:focus,select:focus,textarea:focus{{outline:3px solid #e8ad53;outline-offset:3px}}@media(max-width:640px){{header{{align-items:flex-start;flex-direction:column}}.cards,.fields{{grid-template-columns:1fr}}.hero{{padding:32px 0 42px}}.contact{{padding:22px}}}}</style></head>
    <body><header><strong>{e(site['title'])}</strong><nav aria-label="Main navigation"><a href="#services">Services</a><a href="#contact">Contact</a></nav></header>
    <main><section class="hero"><span class="eyebrow">Your local cleaning team</span><h1>{e(site['tagline'] or site['title'])}</h1><p class="intro">{e(site['about'])}</p><a class="cta" href="#contact">Let's talk about your space</a></section>
    <section id="services"><h2>Cleaning that fits your needs</h2><div class="cards">{services}</div></section>
    <section><h2>Where we work</h2><ul class="areas">{areas}</ul></section>
    <section id="contact" class="contact"><h2>A fresh start begins here</h2><div class="details"><a href="mailto:{e(quote(site['email'], safe='@.'))}">{e(site['email'])}</a>{phone}</div><p>{e(site['hours'])}</p><p>{e(site['streetAddress'])}</p>{inquiry}</section></main>
    <footer>{e(site['title'])} · Contact us to confirm availability and pricing.</footer></body></html>'''


def public_view(record):
    return _html(_published(record), '/api/public/sites/' + quote(record['id'], safe='') + '/inquiries')


def export(record, format):
    if format == 'html':
        return _html(record['site']).encode(), 'text/html', 'cleaning-website.html'
    if format == 'csv':
        output = io.StringIO(newline='')
        fields = ['name', 'email', 'phone', 'serviceTitle', 'area', 'message', 'status', 'receivedAt']
        writer = csv.writer(output)
        writer.writerow(fields)
        for inquiry in record['inquiries']:
            writer.writerow([("'" + str(inquiry[key])) if str(inquiry[key]).lstrip().startswith(('=', '+', '-', '@')) else inquiry[key] for key in fields])
        return output.getvalue().encode('utf-8-sig'), 'text/csv', 'cleaning-inquiries.csv'
    fail(404, 'Unknown cleaning-site export format')
