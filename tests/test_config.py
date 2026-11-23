import pytest

from app.config import DEFAULT_MODEL, Settings


def test_legacy_international_endpoint():
    settings = Settings(api_key="secret", region="intl")
    assert settings.ws_url == (
        "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
        f"?model={DEFAULT_MODEL}"
    )


def test_workspace_endpoint():
    settings = Settings(api_key="secret", region="mainland", workspace_id="ws-test")
    assert settings.ws_url == (
        "wss://ws-test.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
        f"?model={DEFAULT_MODEL}"
    )


def test_custom_endpoint_drops_old_query():
    settings = Settings(
        api_key="secret",
        ws_url_override="wss://example.test/realtime?model=old",
        model="new-model",
    )
    assert settings.ws_url == "wss://example.test/realtime?model=new-model"


def test_runtime_update_does_not_replace_key_with_empty_value():
    settings = Settings(api_key="secret", key_source="env")
    settings.update_runtime(api_key="", region="mainland", workspace_id="")
    assert settings.api_key == "secret"
    assert settings.region == "mainland"
    assert settings.key_source == "env"


def test_public_config_never_exposes_key():
    public = Settings(api_key="secret", key_source="env").public_dict()
    assert public["configured"] is True
    assert "secret" not in repr(public)
    assert "api_key" not in public

def test_from_env_rejects_unknown_region(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_REGION", "unknown-region")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_ws_url_override_rejects_remote_ws_and_allows_loopback():
    with pytest.raises(ValueError):
        Settings(api_key="secret", ws_url_override="ws://example.com/ws")

    settings = Settings(api_key="secret", ws_url_override="ws://127.0.0.1:8765/ws")
    assert settings.ws_url == f"ws://127.0.0.1:8765/ws?model={DEFAULT_MODEL}"


def test_runtime_update_does_not_change_api_key_on_validation_error():
    settings = Settings(api_key="secret", region="intl", key_source="env")

    with pytest.raises(ValueError):
        settings.update_runtime(api_key="new-secret", region="invalid-region")
    assert settings.api_key == "secret"
    assert settings.key_source == "env"

    with pytest.raises(ValueError):
        settings.update_runtime(api_key="new-secret", workspace_id="invalid workspace!")
    assert settings.api_key == "secret"
    assert settings.key_source == "env"
