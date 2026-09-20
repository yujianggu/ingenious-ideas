import pytest
from fastapi.testclient import TestClient
from app.main import create_app


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1'])
@pytest.mark.parametrize('port', [5116, 8116])
def test_local_web_and_app_preflight_are_allowed(tmp_path, monkeypatch, host, port):
    monkeypatch.delenv('B06_CORS_ORIGINS', raising=False)
    client = TestClient(create_app(tmp_path))
    origin = f'http://{host}:{port}'
    response = client.options('/api/auth/login', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'authorization,content-type',
    })
    assert response.status_code == 200, response.text
    assert response.headers['access-control-allow-origin'] == origin
    assert 'authorization' in response.headers['access-control-allow-headers'].lower()


@pytest.mark.parametrize('host', ['localhost', '127.0.0.1'])
@pytest.mark.parametrize('port', [5119, 8119])
def test_other_idea_web_and_app_preflight_are_rejected(tmp_path, monkeypatch, host, port):
    monkeypatch.delenv('B06_CORS_ORIGINS', raising=False)
    client = TestClient(create_app(tmp_path))
    response = client.options('/api/auth/login', headers={
        'Origin': f'http://{host}:{port}', 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'authorization,content-type',
    })
    assert response.status_code == 400
    assert 'access-control-allow-origin' not in response.headers
