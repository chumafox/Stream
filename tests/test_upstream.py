"""Интеграционный тест реального клиента против локальной имитации Qwen.

Проверяет не моки, а фактический обмен: session.created → session.update →
session.updated → append → дельты → response.done → session.finish → session.finished.
"""

import asyncio
import base64
import json

import pytest
import websockets

from app.config import Settings
from app.livetranslate import (
    LiveTranslateClient,
    LiveTranslateError,
    SessionOptions,
    normalize_event,
)


async def _serve(handler):
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_full_protocol_roundtrip_against_fake_upstream():
    seen = {"session": None, "frames": 0, "finish": False}

    async def handler(ws):
        await ws.send(json.dumps({"type": "session.created", "event_id": "e1"}))
        async for raw in ws:
            event = json.loads(raw)
            if event["type"] == "session.update":
                seen["session"] = event["session"]
                await ws.send(json.dumps({"type": "session.updated", "event_id": "e2"}))
            elif event["type"] == "input_audio_buffer.append":
                seen["frames"] += 1
                assert len(base64.b64decode(event["audio"])) == 3200
                await ws.send(
                    json.dumps(
                        {
                            "type": "conversation.item.input_audio_transcription.delta",
                            "item_id": "item-asr-1",
                            "delta": "Привет",
                        }
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "type": "response.audio_transcript.delta",
                            "response_id": "resp-1",
                            "item_id": "item-tr-1",
                            "previous_item_id": "item-asr-1",
                            "delta": "你好",
                        }
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "type": "response.audio.delta",
                            "response_id": "resp-1",
                            "item_id": "item-tr-1",
                            "delta": base64.b64encode(b"\x01\x00" * 240).decode(),
                        }
                    )
                )
                await ws.send(
                    json.dumps(
                        {
                            "type": "response.done",
                            "response": {"id": "resp-1", "status": "completed", "usage": {"total_tokens": 120}},
                        }
                    )
                )
            elif event["type"] == "session.finish":
                seen["finish"] = True
                await ws.send(json.dumps({"type": "session.finished"}))
                return

    server, url = await _serve(handler)
    events = []

    async def collect(event):
        events.append(event)

    settings = Settings(api_key="probe", ws_url_override=url)
    client = LiveTranslateClient(settings, SessionOptions(phrases={"стример": "主播"}), collect)
    try:
        await client.connect()
        await client.send_audio(b"\x00\x00" * 1600)
        for _ in range(4):
            event = await client.receive()
            await collect(normalize_event(event))
        await client.finish()
    finally:
        await client.close()
        server.close()
        await server.wait_closed()

    assert seen["session"]["translation"]["language"] == "zh"
    assert seen["session"]["translation"]["corpus"]["phrases"] == {"стример": "主播"}
    assert seen["session"]["output_modalities"] == ["text", "audio"]
    assert seen["session"]["audio"]["input"]["format"] == {"type": "pcm", "sample_rate": 16000}
    assert seen["frames"] == 1
    assert seen["finish"] is True
    kinds = [event.get("event_type") or event.get("type") for event in events]
    assert kinds == [
        "session.created",
        "session.updated",
        "conversation.item.input_audio_transcription.delta",
        "response.audio_transcript.delta",
        "model_audio",
        "response.done",
    ]
    assert events[3]["item_id"] == "item-tr-1"
    assert events[3]["previous_item_id"] == "item-asr-1"


@pytest.mark.asyncio
async def test_provider_error_during_session_is_raised():
    async def handler(ws):
        await ws.send(json.dumps({"type": "session.created"}))
        async for raw in ws:
            if json.loads(raw)["type"] == "session.update":
                await ws.send(
                    json.dumps(
                        {
                            "type": "error",
                            "error": {"code": "invalid_value", "message": "voice must be default"},
                        }
                    )
                )
                return

    server, url = await _serve(handler)
    settings = Settings(api_key="probe", ws_url_override=url)
    client = LiveTranslateClient(settings, SessionOptions(), lambda event: asyncio.sleep(0))
    try:
        with pytest.raises(LiveTranslateError, match="voice must be default"):
            await client.connect()
    finally:
        await client.close()
        server.close()
        await server.wait_closed()
