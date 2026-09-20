import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from django.conf import settings
from rest_framework.exceptions import APIException


class ScanUnavailable(APIException):
    status_code = 503
    default_code = 'scan_unavailable'
    default_detail = '文件扫描暂不可用，请稍后重试'


def scan_file(path):
    template = settings.FILE_SCANNER_COMMAND
    if not template or not any('{file}' in part for part in template):
        raise ScanUnavailable()
    command = [part.replace('{file}', str(path)) for part in template]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=settings.FILE_SCAN_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ScanUnavailable()
    if result.returncode == 0:
        return 'clean'
    if result.returncode == 1:
        return 'infected'
    raise ScanUnavailable()


def validate_record(record, store):
    try:
        source = store.open(record.object_key)
    except FileNotFoundError:
        return 'rejected', 'object_missing', ''
    except ValueError:
        return 'rejected', 'size_mismatch', ''
    with source, tempfile.NamedTemporaryFile() as temp:
        digest, size = hashlib.sha256(), 0
        while chunk := source.read(64 * 1024):
            size += len(chunk)
            if size > settings.MAX_UPLOAD_BYTES:
                return 'rejected', 'size_limit', ''
            digest.update(chunk)
            temp.write(chunk)
        temp.flush()
        if size != record.size:
            return 'rejected', 'size_mismatch', ''
        if digest.hexdigest() != record.sha256:
            return 'rejected', 'digest_mismatch', ''
        if scan_file(temp.name) == 'infected':
            return 'rejected', 'malware_detected', ''
        try:
            parsed = subprocess.run([sys.executable, '-m', 'files.inspect_format', temp.name, Path(record.display_name).suffix.lower(), str(settings.MAX_PDF_PAGES), str(settings.MAX_IMAGE_PIXELS)], capture_output=True, timeout=settings.FILE_PARSE_TIMEOUT, check=False)
            if parsed.returncode != 0:
                return 'rejected', 'invalid_format', ''
            content_type = json.loads(parsed.stdout)['content_type']
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            return 'rejected', 'invalid_format', ''
    return 'ready', '', content_type
