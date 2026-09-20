import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "test-only-b16-secret")
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "rest_framework",
    "django_otp", "django_otp.plugins.otp_totp", "django_otp.plugins.otp_static",
    "identity", "common", "proofing", "files", "jobs",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = []
WSGI_APPLICATION = "config.wsgi.application"
AUTH_USER_MODEL = "identity.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Asia/Shanghai"
STATIC_URL = "/static/"

def database_config(name):
    if os.environ.get("B16_DB_HOST"):
        return {"ENGINE": "django.db.backends.postgresql", "NAME": os.environ.get("B16_DB_NAME", name),
                "USER": os.environ["B16_DB_USER"], "PASSWORD": os.environ["B16_DB_PASSWORD"],
                "HOST": os.environ["B16_DB_HOST"], "PORT": os.environ.get("B16_DB_PORT", "5432")}
    path = Path(os.environ.get("B16_PG_CREDENTIALS", Path.home() / ".local/share/ingenious-ideas/postgresql/development-credentials.json"))
    credentials = json.loads(path.read_text())
    return {"ENGINE": "django.db.backends.postgresql", "NAME": name,
            "USER": credentials["user"], "PASSWORD": credentials["password"],
            "HOST": credentials["host"], "PORT": credentials["port"]}

DATABASES = {"default": database_config(os.environ.get("B16_DB_NAME", "b16_proofing_dev"))}
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["config.authentication.ApiSessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
}

DEPLOYMENT_ENV = 'development'
PRIVATE_STORE = os.environ.get('B16_PRIVATE_STORE', 'local')
PRIVATE_LOCAL_ROOT = Path(os.environ.get('B16_PRIVATE_LOCAL_ROOT', BASE_DIR / '.private-files'))
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_IMAGE_PIXELS = 40_000_000
FILE_SCANNER_COMMAND = json.loads(os.environ.get('B16_SCANNER_COMMAND', '[]'))
FILE_SCAN_TIMEOUT = 60
FILE_PARSE_TIMEOUT = 15
OSS_ENDPOINT = os.environ.get('B16_OSS_ENDPOINT', '')
OSS_BUCKET = os.environ.get('B16_OSS_BUCKET', '')
OSS_REGION = os.environ.get('B16_OSS_REGION', '')

CODE_PROVIDER = os.environ.get('B16_CODE_PROVIDER', '')
CODE_PROVIDER_ENDPOINT = os.environ.get('B16_CODE_PROVIDER_ENDPOINT', '')
CODE_PROVIDER_API_KEY = os.environ.get('B16_CODE_PROVIDER_API_KEY', '')
INVITATION_TTL_SECONDS = 86400
EXTERNAL_SESSION_TTL_SECONDS = 1800
SESSION_COOKIE_HTTPONLY = True

EXPORT_MAX_BYTES = 20 * 1024 * 1024
EXPORT_MAX_PAGES = 100
EXPORT_RENDER_TIMEOUT = 45
EXPORT_RENDER_MEMORY_MB = 768
EXPORT_MAX_IMAGES = 20
