from fastapi.testclient import TestClient

from app import server


client = TestClient(server.app)


def test_index_and_assets_are_served():
    response = client.get("/")
    assert response.status_code == 200
    assert "StreamBridge" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/pcm-capture-worklet.js").status_code == 200


def test_config_does_not_leak_secret(monkeypatch):
    monkeypatch.setattr(server.settings, "api_key", "secret")
    monkeypatch.setattr(server.settings, "key_source", "env")
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert "secret" not in response.text


def test_runtime_config_can_be_saved_without_persistence(monkeypatch):
    monkeypatch.setattr(server.settings, "api_key", "")
    monkeypatch.setattr(server.settings, "key_source", "none")
    response = client.post(
        "/api/config",
        json={"api_key": "temporary", "region": "intl", "workspace_id": ""},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert server.settings.api_key == "temporary"
    assert server.settings.key_source == "runtime"
    assert "temporary" not in response.text


def test_invalid_workspace_is_reported():
    response = client.post(
        "/api/config",
        json={"api_key": "temporary", "region": "intl", "workspace_id": "bad/value"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "Workspace" in response.json()["message"]


def test_external_host_is_rejected():
    response = client.get("/", headers={"host": "example.com"})
    assert response.status_code == 400
