"""B01: versioned knowledge and human-reviewed, deterministic excerpt replies."""
from copy import deepcopy
import csv
import io
import re
from urllib.parse import urlsplit

from app.store import ident, now, require, fail


STOPWORDS = set('a an and are as at be by can could do does for from how i in is it many me my of on or our please the their this to us we what when where which who why will with would you your'.split())


def _text(body, key, limit=4000, optional=False):
    value = body.get(key, '')
    require(isinstance(value, str), f'{key} must be text')
    value = value.strip()
    require((optional or bool(value)) and len(value) <= limit, f'{key} must contain {0 if optional else 1}–{limit} characters')
    return value


def _url(body):
    value = _text(body, 'url', 2000)
    require(not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in body['url']), 'Source URL cannot contain control characters')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username
                 and not parsed.password and (port is None or 1 <= port <= 65535))
    except ValueError:
        valid = False
    require(valid and not any(c.isspace() for c in value), 'Use a valid http or https source URL without credentials')
    return value


def _find(items, value, label):
    item = next((item for item in items if item['id'] == value), None)
    require(item is not None, f'{label} not found', 404)
    return item


def _tokens(value):
    return {word[:-1] if len(word) > 4 and word.endswith('s') else word
            for word in re.findall(r'[^\W_]+', value.lower(), re.UNICODE)
            if len(word) > 2 and word not in STOPWORDS}


def _invalidate(record):
    record['knowledgeVersion'] += 1
    for ticket in record['tickets']:
        if ticket['status'] != 'open':
            _remember_closure(ticket)
            ticket.update(status='needs-refresh', approvedAt=None, closedAt=None)


def _remember_closure(ticket):
    """Preserve handling receipts, including tickets created before closure history existed."""
    if not ticket.get('closedAt'):
        return
    history = ticket.setdefault('closureHistory', [])
    if not any(item['closedAt'] == ticket['closedAt'] for item in history):
        history.append({key: deepcopy(ticket[key]) for key in (
            'closedAt', 'resolutionNote', 'approvedAt', 'knowledgeVersion', 'draft', 'citations')})


def _current(record, ticket):
    if ticket['knowledgeVersion'] != record['knowledgeVersion'] or not ticket['citations']:
        return False
    for citation in ticket['citations']:
        source = next((s for s in record['sources'] if s['id'] == citation['sourceId']), None)
        if not source or not source['active'] or source['version'] != citation['sourceVersion']:
            return False
        if citation['quote'] not in source['content']:
            return False
    return True


def _retrieve(record, question):
    terms = _tokens(question)
    if not terms:
        return []
    minimum = min(2, len(terms))
    matches = []
    for source_index, source in enumerate(record['sources']):
        if not source['active']:
            continue
        # Match bounded paragraphs/sentences without rewriting any source text.
        passages = re.split(r'(?<=[.!?])\s+|\n+', source['content'])
        for passage_index, passage in enumerate(passages):
            passage = passage.strip()
            if not passage or len(passage) > 1200:
                continue
            overlap = terms & _tokens(passage)
            if len(overlap) >= minimum:
                matches.append((len(overlap), source_index, passage_index, source, passage))
    matches.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [dict(sourceId=s['id'], title=s['title'], url=s['url'], sourceVersion=s['version'],
                 quote=passage, matchedTerms=sorted(terms & _tokens(passage)))
            for _, _, _, s, passage in matches[:3]]


def create(body):
    return dict(title=_text(body, 'title', 160), escalationQueue=_text(body, 'escalationQueue', 160, True) or 'Support owner',
                knowledgeVersion=0, sources=[], tickets=[],
                method='Deterministic keyword retrieval of supplied English excerpts; no AI or automatic sending.')


def act(record, action, body):
    record = deepcopy(record)
    if action in ('add-source', 'update-source'):
        require(body.get('authorized') is True, 'Confirm that you are authorized to use this knowledge source')
        values = dict(title=_text(body, 'title', 160), url=_url(body), content=_text(body, 'content', 50000),
                      active=True, authorizedAt=now())
        if action == 'add-source':
            require(len(record['sources']) < 100, 'This desk supports up to 100 sources')
            record['sources'].append(dict(id=ident(), version=1, history=[], **values))
        else:
            source = _find(record['sources'], body.get('sourceId'), 'Source')
            source['history'].append({key: deepcopy(value) for key, value in source.items() if key != 'history'})
            source.update(values, version=source['version'] + 1)
        _invalidate(record)
    elif action == 'retire-source':
        source = _find(record['sources'], body.get('sourceId'), 'Source')
        require(source['active'], 'Source is already retired')
        source['history'].append({key: deepcopy(value) for key, value in source.items() if key != 'history'})
        source.update(active=False, version=source['version'] + 1)
        _invalidate(record)
    elif action == 'add-ticket':
        require(len(record['tickets']) < 1000, 'This desk supports up to 1,000 tickets')
        question = _text(body, 'question', 4000)
        record['tickets'].append(dict(id=ident(), title=question[:100], question=question,
            customerLabel=_text(body, 'customerLabel', 160, True), status='open', draft='', citations=[],
            knowledgeVersion=None, approvedAt=None, closedAt=None, escalationReason='', escalationNote='',
            resolutionNote='', reviews=[], closureHistory=[], createdAt=now()))
    elif action in ('draft-response', 'edit-response', 'review-response', 'close-ticket', 'record-escalation'):
        ticket = _find(record['tickets'], body.get('ticketId'), 'Ticket')
        if action == 'draft-response':
            citations = _retrieve(record, ticket['question'])
            _remember_closure(ticket)
            ticket.update(citations=citations, knowledgeVersion=record['knowledgeVersion'], approvedAt=None,
                          closedAt=None, resolutionNote='')
            if citations:
                draft = 'Relevant excerpts for review:\n\n' + '\n\n'.join(f'[{i}] {c["quote"]}' for i, c in enumerate(citations, 1))
                ticket.update(status='draft', draft=draft, escalationReason='')
            else:
                ticket.update(status='needs-human', draft='', escalationReason=(
                    'No sufficient matching excerpt was found in active authorized sources. '
                    f'Escalate to {record["escalationQueue"]}; add supporting knowledge before drafting again.'))
        elif action == 'edit-response':
            require(_current(record, ticket), 'Retrieve current supporting evidence before editing a response')
            _remember_closure(ticket)
            ticket.update(draft=_text(body, 'draft', 12000), status='draft', approvedAt=None, closedAt=None, resolutionNote='')
        elif action == 'review-response':
            require(ticket['status'] == 'draft' and _current(record, ticket), 'A current evidence-backed draft is required')
            require(body.get('confirmed') is True, 'Confirm you checked every claim against the cited sources')
            ticket.update(status='approved', approvedAt=now())
            ticket['reviews'].append(dict(approvedAt=ticket['approvedAt'], knowledgeVersion=record['knowledgeVersion'],
                                         draft=ticket['draft'], citations=deepcopy(ticket['citations'])))
        elif action == 'close-ticket':
            require(ticket['status'] == 'approved' and _current(record, ticket), 'Approve the current response before closing this ticket')
            require(body.get('confirmed') is True, 'Confirm the ticket has been handled outside this application')
            ticket.update(status='closed', closedAt=now(), resolutionNote=_text(body, 'note', 2000))
            _remember_closure(ticket)
        else:
            require(ticket['status'] == 'needs-human', 'Retrieve evidence first; escalation notes apply to tickets needing human help')
            ticket['escalationNote'] = _text(body, 'note', 2000)
    else:
        fail(404, 'Unknown knowledge desk action')
    return record


def _csv_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def export(record, format):
    require(format in ('txt', 'csv'), 'Supported response exports: txt, csv')
    tickets = [t for t in record['tickets'] if t['status'] in ('approved', 'closed') and _current(record, t)]
    require(tickets, 'There are no currently approved responses to export')
    if format == 'csv':
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['ticket', 'customer_label', 'question', 'response', 'sources', 'status', 'approved_at', 'resolution_note'])
        for ticket in tickets:
            sources = '\n'.join(f'[{i}] {c["title"]} (v{c["sourceVersion"]}): {c["url"]}' for i, c in enumerate(ticket['citations'], 1))
            writer.writerow([_csv_cell(v) for v in [ticket['id'], ticket['customerLabel'], ticket['question'], ticket['draft'],
                sources, ticket['status'], ticket['approvedAt'], ticket['resolutionNote']]])
        return stream.getvalue().encode(), 'text/csv; charset=utf-8', 'approved-responses.csv'
    blocks = [f'{record["title"]} — approved responses\nKnowledge version: {record["knowledgeVersion"]}']
    for ticket in tickets:
        sources = '\n'.join(f'[{i}] {c["title"]} (version {c["sourceVersion"]}) — {c["url"]}\n    Evidence: {c["quote"]}'
                            for i, c in enumerate(ticket['citations'], 1))
        blocks.append(f'Question: {ticket["question"]}\nStatus: {ticket["status"]}\nApproved: {ticket["approvedAt"]}\n\n'
                      f'{ticket["draft"]}\n\nSources:\n{sources}\nResolution: {ticket["resolutionNote"] or "Not yet closed"}')
    return '\n\n---\n\n'.join(blocks).encode(), 'text/plain; charset=utf-8', 'approved-responses.txt'
