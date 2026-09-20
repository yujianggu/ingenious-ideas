import hashlib
import json
import pathlib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / 'images'
MANIFEST = BASE / 'image-manifest.json'
OUT.mkdir(exist_ok=True)

def now():
    return datetime.now(timezone.utc).isoformat()

def save(data):
    data['updated_at'] = now()
    temp = MANIFEST.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(MANIFEST)

def validate(path):
    raw = path.read_bytes()
    if not (raw.startswith(b'\xff\xd8\xff') or raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith((b'GIF87a', b'GIF89a')) or (raw[:4] == b'RIFF' and raw[8:12] == b'WEBP')):
        raise ValueError('Unrecognized image signature')
    result = subprocess.run(['/usr/bin/sips', '-g', 'pixelWidth', '-g', 'pixelHeight', str(path)], capture_output=True, text=True)
    if result.returncode or 'pixelWidth: ' not in result.stdout or 'pixelHeight: ' not in result.stdout or '<nil>' in result.stdout:
        raise ValueError('Image decoder validation failed')
    dims = {}
    for line in result.stdout.splitlines():
        if 'pixelWidth:' in line or 'pixelHeight:' in line:
            k, v = line.strip().split(': ')
            dims[k] = int(v)
    if not all(v > 0 for v in dims.values()):
        raise ValueError('Invalid image dimensions')
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), **dims}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect encountered; paused without following')

data = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {'images': [], 'failures': []}
data['status'] = 'running'
items = {x['source_url']: x for x in data['images']}
opener = urllib.request.build_opener(NoRedirect)
last_request = 0
while True:
    products = json.loads((BASE / '活动商品.json').read_text())['products']
    urls = {}
    for p in products:
        url = p.get('image')
        if not url:
            continue
        if url.startswith('//'):
            url = 'https:' + url
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'h2.appsimg.com' or parsed.username or parsed.password or parsed.port:
            data['status'] = 'blocked'
            data['failures'].append({'source_url': url, 'error': 'URL outside approved host', 'at': now()})
            save(data)
            raise SystemExit('URL outside approved host; stopped')
        urls.setdefault(url, [])
        if p.get('product_ref') not in urls[url]:
            urls[url].append(p.get('product_ref'))
    data['snapshot_product_count'] = len(products)
    data['snapshot_unique_images'] = len(urls)
    pending = []
    for url, refs in urls.items():
        existing = items.get(url)
        if existing:
            path = BASE / existing['file']
            try:
                check = validate(path)
                if check['sha256'] != existing['sha256']:
                    raise ValueError('Hash mismatch')
                existing['product_refs'] = refs
                continue
            except (OSError, ValueError):
                data['images'].remove(existing)
                del items[url]
        pending.append((url, refs))
    save(data)
    if not pending:
        data['status'] = 'caught_up'
        save(data)
        print(json.dumps({'status': 'caught_up', 'success': len(items), 'products': len(products)}, ensure_ascii=False), flush=True)
        break
    for url, refs in pending:
        time.sleep(max(0, 3.05 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'PublicProductImageArchive/1.0', 'Accept': 'image/*'})
            with opener.open(req, timeout=30) as response:
                content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
                if not content_type.startswith('image/'):
                    raise ValueError('Non-image response; possible verification')
                raw = response.read(20_000_001)
                if len(raw) > 20_000_000:
                    raise ValueError('Image exceeds 20 MB limit')
            ext = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif' }.get(content_type, '.img')
            path = OUT / (hashlib.sha256(url.encode()).hexdigest()[:24] + ext)
            temp = path.with_suffix('.part')
            temp.write_bytes(raw)
            try:
                check = validate(temp)
            except Exception:
                temp.unlink(missing_ok=True)
                raise
            temp.replace(path)
            record = {'source_url': url, 'product_refs': refs, 'file': str(path.relative_to(BASE)), 'content_type': content_type, 'downloaded_at': now(), **check}
            data['images'].append(record)
            items[url] = record
            save(data)
            if len(items) % 15 == 0:
                print(json.dumps({'status': 'progress', 'success': len(items), 'snapshot_unique_images': len(urls)}, ensure_ascii=False), flush=True)
        except Exception as exc:
            data['status'] = 'blocked'
            data['failures'].append({'source_url': url, 'error': str(exc), 'at': now()})
            save(data)
            print(json.dumps({'status': 'blocked', 'success': len(items), 'error': str(exc)}, ensure_ascii=False), flush=True)
            raise SystemExit(2)
