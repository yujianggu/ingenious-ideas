"""Optional text candidates; disabled calls perform no configuration or network I/O."""
from collections.abc import Callable
import http.client
import json
import os
from urllib.parse import urlsplit

from .reporting import calculate

ENDPOINT = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
MAX_PAYLOAD_BYTES = 16384
MAX_RESPONSE_BYTES = 65536


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    except (TypeError, ValueError):
        raise ValueError('INVALID_MODEL_RESPONSE') from None


def _config(value):
    if (not isinstance(value, dict) or set(value) != {
            'endpoint', 'model_id', 'evaluation_record', 'timeout_seconds',
            'max_tokens', 'max_total_tokens', 'retries'}):
        raise ValueError('INVALID_MODEL_CONFIG')
    # Exact URL allowlist, no userinfo, ports, query strings or alternate hosts.
    if value.get('endpoint') != ENDPOINT:
        raise ValueError('INVALID_MODEL_CONFIG')
    for field in ('model_id', 'evaluation_record'):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError('INVALID_MODEL_CONFIG')
    for field, minimum, maximum in (('retries', 0, 2), ('max_tokens', 1, 2048),
                                     ('max_total_tokens', 1, 16384)):
        if type(value.get(field)) is not int or not minimum <= value[field] <= maximum:
            raise ValueError('INVALID_MODEL_CONFIG')
    timeout = value.get('timeout_seconds')
    if type(timeout) not in (float, int) or not 0.1 <= timeout <= 30:
        raise ValueError('INVALID_MODEL_CONFIG')
    return value


def validate_candidate(result: dict, *, metrics: dict, source_ids: list[str]) -> dict:
    """Reject invented amounts/sources; candidate text still needs human review."""
    if (not isinstance(result, dict)
            or set(result) != {'text', 'source_ids', 'metrics', 'usage'}
            or not isinstance(result.get('text'), str)
            or not result['text'].strip() or len(result['text']) > 4000):
        raise ValueError('INVALID_MODEL_RESPONSE')
    ids = result['source_ids']
    if (not isinstance(ids, list) or not ids
            or any(not isinstance(item, str) or item not in source_ids for item in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('INVALID_MODEL_SOURCE')
    supplied = result['metrics']
    if (not isinstance(supplied, dict) or supplied != metrics
            or any(value is not None and type(value) is not int for value in supplied.values())):
        raise ValueError('INVALID_MODEL_METRICS')
    usage = result['usage']
    if (not isinstance(usage, dict) or set(usage) != {'total_tokens'}
            or type(usage['total_tokens']) is not int or usage['total_tokens'] < 0):
        raise ValueError('INVALID_MODEL_RESPONSE')
    if len(_encoded(result)) > MAX_RESPONSE_BYTES:
        raise ValueError('MODEL_RESPONSE_LIMIT')
    return {**result, 'state': 'candidate'}


def _https_transport(config, key, request):
    url = urlsplit(config['endpoint'])
    connection = http.client.HTTPSConnection(url.hostname, timeout=config['timeout_seconds'])
    try:
        connection.request('POST', url.path, body=_encoded(request), headers={
            'Content-Type': 'application/json', 'Authorization': f'Bearer {key}',
        })
        response = connection.getresponse()
        # http.client does not follow redirects. Reject every redirect, even same host.
        if 300 <= response.status < 400:
            raise ValueError('MODEL_REDIRECT_REJECTED')
        if response.status != 200:
            raise ValueError('MODEL_HTTP_ERROR')
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError('MODEL_RESPONSE_LIMIT')
        try:
            envelope = json.loads(raw)
            result = json.loads(envelope['choices'][0]['message']['content'])
            if not isinstance(result, dict):
                raise TypeError
            # Usage is taken from provider envelope, never from generated text.
            result['usage'] = {'total_tokens': envelope['usage']['total_tokens']}
            return result
        except (ValueError, UnicodeError, KeyError, TypeError, IndexError):
            raise ValueError('INVALID_MODEL_RESPONSE') from None
    finally:
        connection.close()


def generate_candidate(payload: dict, *, enabled: bool = False, permitted: bool = False,
                       transport: Callable[[dict], dict] | None = None) -> dict | None:
    if not enabled:
        return None
    if not permitted:
        raise ValueError('MODEL_NOT_AUTHORIZED')
    if not isinstance(payload, dict):
        raise ValueError('INVALID_MODEL_CONFIG')
    config = _config(payload.get('config'))
    key = os.environ.get('DASHSCOPE_API_KEY')
    if not key:
        raise ValueError('MODEL_KEY_REQUIRED')
    try:
        frozen = payload['input_snapshot']
        report = calculate(frozen['orders'], frozen['refunds'], frozen['ads'])
        metrics = {name: report[name] for name in ('paid_less_refund', 'ads')}
        ids = payload['source_ids']
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(item, str) or not item or len(item) > 128 for item in ids)
                or len(ids) != len(set(ids)) or payload['metrics'] != metrics):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('INVALID_MODEL_INPUT') from None
    request = {
        'model': config['model_id'], 'max_tokens': config['max_tokens'],
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': '仅润色指标解释。返回JSON: text, source_ids, metrics；'
             '金额和来源必须原样引用，不称净利润，不推断缺项。'},
            {'role': 'user', 'content': json.dumps({'metrics': metrics, 'source_ids': ids}, ensure_ascii=False)},
        ],
    }
    size = len(_encoded(request))
    if size > MAX_PAYLOAD_BYTES:
        raise ValueError('MODEL_PAYLOAD_LIMIT')
    # Conservative reservation: UTF-8 bytes bound input tokens. A timed-out request
    # may have been charged, so reserve its full allowance before each attempt.
    reserve = size + config['max_tokens']
    spent = 0
    for attempt in range(config['retries'] + 1):
        if spent + reserve > config['max_total_tokens']:
            raise ValueError('MODEL_USAGE_LIMIT')
        spent += reserve
        try:
            result = transport(request) if transport is not None else _https_transport(config, key, request)
        except TimeoutError:
            if attempt == config['retries']:
                raise ValueError('MODEL_TIMEOUT') from None
            continue
        except (OSError, http.client.HTTPException):
            raise ValueError('MODEL_TRANSPORT_ERROR') from None
        candidate = validate_candidate(result, metrics=metrics, source_ids=ids)
        if candidate['usage']['total_tokens'] > reserve or candidate['usage']['total_tokens'] > config['max_total_tokens']:
            raise ValueError('MODEL_USAGE_LIMIT')
        return candidate
    raise ValueError('MODEL_TIMEOUT')
