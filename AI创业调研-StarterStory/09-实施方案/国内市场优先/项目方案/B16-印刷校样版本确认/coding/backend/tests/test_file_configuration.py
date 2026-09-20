import os
import subprocess
import sys
import json
from pathlib import Path
import pytest


def production_env(tmp_path):
    scanner = tmp_path / 'clamscan'
    scanner.write_text('#!/bin/sh\nexit 2\n')
    scanner.chmod(0o700)
    database = tmp_path / 'database'
    database.mkdir()
    (database / 'main.cvd').write_bytes(b'config-only-placeholder')
    return {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings.production', 'DJANGO_SECRET_KEY': 'configuration-test-secret-not-production', 'B16_PRIVATE_STORE': 'oss', 'B16_OSS_ENDPOINT': 'https://oss-cn-hangzhou.aliyuncs.com', 'B16_OSS_BUCKET': 'test-only-bucket', 'B16_OSS_REGION': 'cn-hangzhou', 'OSS_ACCESS_KEY_ID': 'test-only-id', 'OSS_ACCESS_KEY_SECRET': 'test-only-secret', 'B16_SCANNER_KIND': 'clamav', 'B16_SCANNER_COMMAND': json.dumps([str(scanner), '--database=' + str(database), '{file}']), 'B16_CODE_PROVIDER': 'identity.code_providers.HTTPCodeProvider', 'B16_CODE_PROVIDER_ENDPOINT': 'https://codes.example.test/dispatch', 'B16_CODE_PROVIDER_API_KEY': 'test-only-token'}

@pytest.mark.parametrize('missing', ['B16_OSS_ENDPOINT', 'B16_OSS_BUCKET', 'B16_OSS_REGION', 'OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET', 'B16_SCANNER_COMMAND', 'B16_CODE_PROVIDER', 'B16_CODE_PROVIDER_ENDPOINT', 'B16_CODE_PROVIDER_API_KEY'])
def test_production_startup_rejects_missing_service_configuration(tmp_path, missing):
    env = production_env(tmp_path)
    env.pop(missing, None)
    result = subprocess.run([sys.executable, '-c', 'import django; django.setup()'], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'ImproperlyConfigured' in result.stderr

@pytest.mark.parametrize('name,value', [('B16_PRIVATE_STORE', 'local'), ('B16_CODE_PROVIDER', 'identity.code_providers.DevelopmentCodeProvider'), ('B16_SCANNER_KIND', 'native-development'), ('B16_CODE_PROVIDER_ENDPOINT', 'http://unsafe.test'), ('B16_OSS_ENDPOINT', 'http://unsafe.test')])
def test_production_startup_rejects_development_or_insecure_adapters(tmp_path, name, value):
    env = production_env(tmp_path)
    env[name] = value
    result = subprocess.run([sys.executable, '-c', 'import django; django.setup()'], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'ImproperlyConfigured' in result.stderr


def test_production_config_acceptance_does_not_claim_remote_readiness(tmp_path):
    result = subprocess.run([sys.executable, '-c', 'import django; django.setup()'], env=production_env(tmp_path), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_code_provider_refuses_redirects_to_protect_delivery_credentials(settings):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from identity.code_providers import HTTPCodeProvider, DeliveryUnavailable
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header('Location', '/redirect-target')
            self.end_headers()
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"accepted": true}')
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.CODE_PROVIDER_ENDPOINT = f'http://127.0.0.1:{server.server_port}/send'
    settings.CODE_PROVIDER_API_KEY = 'test-delivery-secret'
    try:
        with pytest.raises(DeliveryUnavailable):
            HTTPCodeProvider().send_code('receiver@example.test', '123456', 'test-request')
        assert seen == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
