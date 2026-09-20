"""B09: manually verified company research and reviewed template email drafts."""
from copy import deepcopy
import csv
import io
import re
from urllib.parse import urlsplit

from app.store import ident, now, require, fail


CATEGORIES = ('overview', 'industry', 'location', 'size', 'operations', 'initiative', 'product')


def _text(body, key, limit=4000, optional=False):
    value = body.get(key, '')
    require(isinstance(value, str), f'{key} must be text')
    value = value.strip()
    require((optional or bool(value)) and len(value) <= limit, f'{key} must contain {0 if optional else 1}–{limit} characters')
    return value


def _url(body, key='url'):
    value = _text(body, key, 2000)
    require(not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in body[key]), f'{key} cannot contain control characters')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username
                 and not parsed.password and (port is None or 1 <= port <= 65535))
    except ValueError:
        valid = False
    require(valid and not any(c.isspace() for c in value), f'{key} must be an http or https URL without credentials')
    return value


def _find(items, value, label):
    item = next((item for item in items if item['id'] == value), None)
    require(item is not None, f'{label} not found', 404)
    return item


def _normalized(value):
    return ' '.join(value.split())


def _profile(body):
    return dict(companyName=_text(body, 'companyName', 160), website=_url(body, 'website'),
                icp=_text(body, 'icp', 2000), offer=_text(body, 'offer', 2000))


def _verified(record):
    return [f for f in record['facts'] if f['status'] == 'verified'
            and any(s['id'] == f['sourceId'] and s['version'] == f['sourceVersion'] for s in record['sources'])]


def _assess(record):
    # The operator supplies comma-separated ICP phrases. This is lexical evidence, not a prediction.
    keywords = list(dict.fromkeys(k.strip().lower() for k in re.split(r'[,;\n]+', record['icp']) if k.strip()))
    statements = ' '.join(f['statement'] for f in _verified(record)).lower()
    matched = [keyword for keyword in keywords if re.search(r'(?<!\w)' + re.escape(keyword) + r'(?!\w)', statements)]
    record['assessment'] = dict(method='Literal ICP phrase overlap in human-verified facts; no inferred qualification.',
        matchedKeywords=matched, missingKeywords=[k for k in keywords if k not in matched],
        verifiedFacts=len(_verified(record)))


def _invalidate(record):
    record['researchVersion'] += 1
    record['research'].update(status='needs-review', approvedAt=None)
    if record['email']:
        record['email'].update(status='needs-refresh', approvedAt=None)
    _assess(record)


def _research_current(record):
    return (record['research']['status'] == 'approved'
            and record['research']['version'] == record['researchVersion']
            and bool(_verified(record)))


def _fact_values(record, body):
    source = _find(record['sources'], body.get('sourceId'), 'Source')
    quote = _text(body, 'quote', 4000)
    require(_normalized(quote) in _normalized(source['content']), 'Supporting quote must occur in the current source text')
    category = _text(body, 'category', 40)
    require(category in CATEGORIES, 'Choose a supported company fact category')
    return dict(title=_text(body, 'statement', 2000), statement=_text(body, 'statement', 2000), quote=quote,
                category=category, sourceId=source['id'], sourceTitle=source['title'], sourceUrl=source['url'],
                sourceVersion=source['version'], status='unverified', verifiedAt=None)


def create(body):
    record = dict(title=_text(body, 'title', 160), **_profile(body), sources=[], facts=[], researchVersion=0,
        research=dict(status='needs-review', version=None, approvedAt=None, fit='', notes='', factIds=[], reviews=[]),
        email=None, method='Company-level manual research and deterministic email templates; no AI, scraping, contacts, or sending.')
    _assess(record)
    return record


def act(record, action, body):
    record = deepcopy(record)
    if action == 'update-profile':
        profile = _profile(body)
        if any(profile[key] != record[key] for key in ('companyName', 'website')):
            for fact in record['facts']:
                fact.update(status='unverified', verifiedAt=None)
        record.update(profile)
        _invalidate(record)
    elif action in ('add-source', 'update-source'):
        values = dict(title=_text(body, 'title', 160), url=_url(body), content=_text(body, 'content', 50000), recordedAt=now())
        if action == 'add-source':
            require(len(record['sources']) < 100, 'This company supports up to 100 sources')
            record['sources'].append(dict(id=ident(), version=1, history=[], **values))
        else:
            source = _find(record['sources'], body.get('sourceId'), 'Source')
            source['history'].append({key: deepcopy(value) for key, value in source.items() if key != 'history'})
            source.update(values, version=source['version'] + 1)
            for fact in record['facts']:
                if fact['sourceId'] == source['id']:
                    fact.update(status='stale', verifiedAt=None)
        _invalidate(record)
    elif action in ('add-fact', 'update-fact'):
        values = _fact_values(record, body)
        if action == 'add-fact':
            require(len(record['facts']) < 300, 'This company supports up to 300 facts')
            record['facts'].append(dict(id=ident(), history=[], **values))
        else:
            fact = _find(record['facts'], body.get('factId'), 'Fact')
            fact['history'].append({key: deepcopy(value) for key, value in fact.items() if key != 'history'})
            fact.update(values)
        _invalidate(record)
    elif action == 'verify-fact':
        fact = _find(record['facts'], body.get('factId'), 'Fact')
        source = _find(record['sources'], fact['sourceId'], 'Source')
        require(fact['sourceVersion'] == source['version'] and _normalized(fact['quote']) in _normalized(source['content']),
                'The source changed; update this fact and quote before verifying again')
        require(body.get('confirmed') is True, 'Confirm you checked that the company-level statement is supported by the quoted source')
        fact.update(status='verified', verifiedAt=now())
        _invalidate(record)
    elif action == 'review-research':
        facts = _verified(record)
        require(facts, 'Verify at least one sourced company fact before reviewing research')
        require(body.get('confirmed') is True, 'Confirm you reviewed all verified facts, their sources, and the ICP assessment')
        fit = _text(body, 'fit', 20)
        require(fit in ('good', 'maybe', 'poor'), 'Choose good, maybe, or poor fit')
        review = dict(version=record['researchVersion'], approvedAt=now(), fit=fit,
                      notes=_text(body, 'notes', 4000), factIds=[f['id'] for f in facts])
        record['research'].update(status='approved', **review)
        record['research']['reviews'].append(deepcopy(review))
        # A changed fit judgment also needs a fresh email review.
        if record['email']:
            record['email'].update(status='needs-refresh', approvedAt=None)
    elif action == 'draft-email':
        require(_research_current(record), 'Review the current company research before drafting an email')
        facts = _verified(record)[:3]
        citations = [dict(factId=f['id'], sourceId=f['sourceId'], title=f['sourceTitle'], url=f['sourceUrl'],
                          sourceVersion=f['sourceVersion'], quote=f['quote'], statement=f['statement']) for f in facts]
        statements = '\n'.join(f'- {f["statement"]} [{i}]' for i, f in enumerate(facts, 1))
        body_text = (f'Hello {record["companyName"]} team,\n\nYour published information states:\n{statements}\n\n'
                     f'Our offer: {record["offer"]}\n\nWould a short conversation about whether this is useful for your team make sense?')
        prior_reviews = record['email']['reviews'] if record['email'] else []
        record['email'] = dict(subject=f'A question for {record["companyName"]}', body=body_text, citations=citations,
            status='draft', researchVersion=record['researchVersion'], approvedAt=None, createdAt=now(),
            method='Fixed template using human-verified facts and the operator-supplied offer.', reviews=prior_reviews)
    elif action in ('edit-email', 'review-email'):
        email = record['email']
        require(email is not None and email['researchVersion'] == record['researchVersion']
                and email['status'] != 'needs-refresh' and _research_current(record), 'Generate an email from the current reviewed research first')
        if action == 'edit-email':
            email.update(subject=_text(body, 'subject', 200), body=_text(body, 'body', 12000), status='draft', approvedAt=None)
        else:
            require(email['status'] == 'draft', 'The current email must be a draft before review')
            require(body.get('confirmed') is True, 'Confirm every company claim is supported and the offer is accurate')
            email.update(status='approved', approvedAt=now())
            email['reviews'].append(dict(approvedAt=email['approvedAt'], researchVersion=email['researchVersion'],
                                        subject=email['subject'], body=email['body'], citations=deepcopy(email['citations'])))
    else:
        fail(404, 'Unknown company research action')
    return record


def _csv_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def export(record, format):
    require(format in ('txt', 'csv'), 'Supported research exports: txt, csv')
    require(_research_current(record), 'Review the current company research before exporting')
    facts = _verified(record)
    if format == 'csv':
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['company', 'category', 'statement', 'quote', 'source_title', 'source_url', 'source_version', 'verified_at', 'fit', 'review_notes'])
        for fact in facts:
            writer.writerow([_csv_cell(value) for value in [record['companyName'], fact['category'], fact['statement'], fact['quote'],
                fact['sourceTitle'], fact['sourceUrl'], fact['sourceVersion'], fact['verifiedAt'], record['research']['fit'], record['research']['notes']]])
        return stream.getvalue().encode(), 'text/csv; charset=utf-8', 'reviewed-company-research.csv'
    email = record['email']
    require(email and email['status'] == 'approved' and email['researchVersion'] == record['researchVersion'],
            'Review the current email before exporting the email package')
    lines = [record['companyName'], record['website'], f'ICP phrases: {record["icp"]}', f'Offer: {record["offer"]}',
             f'Human fit decision: {record["research"]["fit"]}', f'Review notes: {record["research"]["notes"]}',
             f'Research approved: {record["research"]["approvedAt"]}', '', 'Reviewed facts:']
    lines += [f'- {f["statement"]}\n  Evidence: {f["quote"]}\n  {f["sourceTitle"]}, version {f["sourceVersion"]}: {f["sourceUrl"]}' for f in facts]
    lines += ['', 'EMAIL DRAFT — not sent', f'Subject: {email["subject"]}', '', email['body'], '', 'Email citations:']
    lines += [f'[{i}] {c["title"]}, version {c["sourceVersion"]}: {c["url"]}\n    Evidence: {c["quote"]}' for i, c in enumerate(email['citations'], 1)]
    return '\n'.join(lines).encode(), 'text/plain; charset=utf-8', 'reviewed-company-email.txt'
