import base64
import json
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.livetranslate import (
    LiveTranslateClient,
    SessionOptions,
    build_session,
    normalize_event,
)


async def _discard(_):
    return None


def test_build_russian_to_chinese_clone_once_session():
    session = build_session(
        SessionOptions(voice_clone_mode="once", phrases={"стример": "主播"})
    )
    assert session["input_modalities"] == ["audio"]
    assert session["output_modalities"] == ["text", "audio"]
    assert session["audio"]["input"]["format"] == {"type": "pcm", "sample_rate": 16000}
    assert session["audio"]["output"]["voice"] == "default"
    assert session["enable_voice_clone"] is True
    assert session["voice_clone_options"] == {"frequency": "once"}
    assert session["translation"] == {
        "language": "zh",
        "corpus": {"phrases": {"стример": "主播"}},
    }


def test_standard_voice_does_not_enable_cloning():
    session = build_session(SessionOptions(voice_clone_mode="off"))
    assert session["audio"]["output"]["voice"] == "Tina"
    assert "enable_voice_clone" not in session
    assert "voice_clone_options" not in session


@pytest.mark.parametrize("mode", ["never", "auto", "", None])
def test_invalid_clone_mode_is_rejected(mode):
    with pytest.raises(ValueError):
        build_session(SessionOptions(voice_clone_mode=mode))


def test_audio_event_is_forwarded_without_reencoding():
    audio = base64.b64encode(b"\x01\x00\xff\xff").decode()
    event = normalize_event(
        {"type": "response.audio.delta", "delta": audio, "response_id": "resp-1"}
    )
    assert event == {
        "type": "model_audio",
        "audio": audio,
        "sample_rate": 24000,
        "response_id": "resp-1",
    }


def test_qwen38_transcript_events():
    source = normalize_event(
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "item_id": "item-1",
            "delta": "Привет",
        }
    )
    assert source["source_delta"] == "Привет"
    assert source["item_id"] == "item-1"

    target = normalize_event(
        {
            "type": "response.audio_transcript.delta",
            "response_id": "resp-1",
            "delta": "你好",
        }
    )
    assert target["translation_delta"] == "你好"
    assert target["response_id"] == "resp-1"


def test_response_done_extracts_transcript_and_usage():
    result = normalize_event(
        {
            "type": "response.done",
            "response": {
                "id": "resp-1",
                "usage": {"total_tokens": 42},
                "output": [
                    {"content": [{"type": "audio", "transcript": "你好，朋友"}]}
                ],
            },
        }
    )
    assert result["translation_final"] == "你好，朋友"
    assert result["usage"] == {"total_tokens": 42}
    assert result["status"] == "listening"


def test_provider_error_message_is_unwrapped():
    result = normalize_event(
        {"type": "error", "error": {"code": "invalid_value", "message": "Bad voice"}}
    )
    assert result["message"] == "Bad voice"


@pytest.mark.asyncio
async def test_send_audio_builds_append_event():
    client = LiveTranslateClient(Settings(api_key="secret"), SessionOptions(), _discard)
    client.upstream = AsyncMock()
    await client.send_audio(b"\x01\x02")
    raw = client.upstream.send.await_args.args[0]
    event = json.loads(raw)
    assert event["type"] == "input_audio_buffer.append"
    assert base64.b64decode(event["audio"]) == b"\x01\x02"


@pytest.mark.asyncio
async def test_finish_is_sent_only_once():
    client = LiveTranslateClient(Settings(api_key="secret"), SessionOptions(), _discard)
    client.upstream = AsyncMock()
    await client.finish()
    await client.finish()
    assert client.upstream.send.await_count == 1
    event = json.loads(client.upstream.send.await_args.args[0])
    assert event["type"] == "session.finish"
