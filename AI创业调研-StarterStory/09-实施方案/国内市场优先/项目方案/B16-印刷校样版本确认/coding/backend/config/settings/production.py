import os
from django.core.exceptions import ImproperlyConfigured
from .base import *

if not os.environ.get("DJANGO_SECRET_KEY"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")
if os.environ.get("B16_ALLOW_OTP_BYPASS"):
    raise ImproperlyConfigured("OTP bypass is forbidden in production")
DEBUG = False
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True

# Fail closed at startup; configuration acceptance is not a cloud contract test.
DEPLOYMENT_ENV = 'production'
if PRIVATE_STORE != 'oss':
    raise ImproperlyConfigured('Production requires a private OSS store')
for name in ['B16_OSS_ENDPOINT', 'B16_OSS_BUCKET', 'B16_OSS_REGION', 'OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET', 'B16_CODE_PROVIDER', 'B16_CODE_PROVIDER_ENDPOINT', 'B16_CODE_PROVIDER_API_KEY']:
    if not os.environ.get(name):
        raise ImproperlyConfigured(name + ' is required')
from urllib.parse import urlsplit
for value in [OSS_ENDPOINT, CODE_PROVIDER_ENDPOINT]:
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ImproperlyConfigured('Service endpoints must use HTTPS without embedded credentials')
if CODE_PROVIDER != 'identity.code_providers.HTTPCodeProvider':
    raise ImproperlyConfigured('Production requires configured HTTP code delivery; development adapters are forbidden')
if os.environ.get('B16_SCANNER_KIND') != 'clamav':
    raise ImproperlyConfigured('Production requires explicit ClamAV scanner configuration')
if not isinstance(FILE_SCANNER_COMMAND, list) or not FILE_SCANNER_COMMAND or not all(isinstance(part, str) for part in FILE_SCANNER_COMMAND):
    raise ImproperlyConfigured('B16_SCANNER_COMMAND must be a ClamAV command array')
scanner_path = Path(FILE_SCANNER_COMMAND[0])
if not scanner_path.is_absolute() or scanner_path.name != 'clamscan' or not os.access(scanner_path, os.X_OK) or FILE_SCANNER_COMMAND.count('{file}') != 1:
    raise ImproperlyConfigured('Production requires an executable clamscan and one file placeholder')
databases = [part.removeprefix('--database=') for part in FILE_SCANNER_COMMAND if part.startswith('--database=')]
if len(databases) != 1 or not Path(databases[0]).exists():
    raise ImproperlyConfigured('ClamAV database path is required')
