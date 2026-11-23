from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import websockets

from app.config import Settings


INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
MAX_AUDIO_FRAME_BYTES = 64_000
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,127}$")


class LiveTranslateError(RuntimeError):
    """A provider or protocol error safe to show in the local UI."""


@dataclass
class SessionOptions:
    target_language: str = "zh"
    voice_clone_mode: str = "once"
    phrases: dict[str, str] = field(default_factory=dict)
    voice_id: str = ""

    def validate(self) -> None:
        if self.target_language != "zh":
            raise ValueError("Эта сборка настроена на китайский язык результата: zh")
        if self.voice_clone_mode not in {"once", "always", "never", "off"}:
            raise ValueError("voice_clone_mode должен быть once, always, never или off")
        self.voice_id = self.voice_id.strip()
        if self.voice_clone_mode == "never":
            if not VOICE_ID_RE.fullmatch(self.voice_id):
                raise ValueError(
                    "Для режима never нужен voice ID, созданный через Voice Cloning API "
                    "с target_model текущей модели перевода"
                )
        if len(self.phrases) > 100:
            raise ValueError("Можно передать не более 100 терминов за сеанс")
        for source, target in self.phrases.items():
            if not source.strip() or not target.strip():
                raise ValueError("Термин и перевод не должны быть пустыми")
            if len(source) > 200 or len(target) > 200:
                raise ValueError("Термин и перевод должны быть короче 200 символов")


def build_session(options: SessionOptions) -> dict[str, Any]:
    options.validate()
    voice = "Tina" if options.voice_clone_mode == "off" else "default"
    if options.voice_clone_mode == "never":
        voice = options.voice_id
    translation: dict[str, Any] = {"language": options.target_language}
    if options.phrases:
        translation["corpus"] = {"phrases": options.phrases}

    session: dict[str, Any] = {
        "input_modalities": ["audio"],
        "output_modalities": ["text", "audio"],
        "audio": {
            "input": {
                "format": {"type": "pcm", "sample_rate": INPUT_SAMPLE_RATE},
                "turn_detection": {"type": "speaker_detection", "threshold": 0.5},
            },
            "output": {"voice": voice},
        },
        "translation": translation,
    }
    if options.voice_clone_mode != "off":
        session["enable_voice_clone"] = True
        session["voice_clone_options"] = {"frequency": options.voice_clone_mode}
    return session


def make_event_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def provider_error(event: dict[str, Any]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(event.get("message") or error or "Неизвестная ошибка LiveTranslate")


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Convert Qwen 3.8 events to stable browser messages.

    A few 3.5-compatible transcript events are accepted as well, which makes
    diagnostics easier if DASHSCOPE_MODEL is explicitly overridden.
    """
    event_type = str(event.get("type", "unknown"))
    payload: dict[str, Any] = {
        "type": "model_event",
        "event_type": event_type,
        "event_id": event.get("event_id", ""),
    }
    # item_id связывает субтитры по документированной схеме Qwen:
    # translation_item.previous_item_id == asr_item.id.
    for key in ("item_id", "previous_item_id", "speaker_id"):
        if event.get(key) is not None:
            payload[key] = event[key]

    if event_type in {"session.created", "session.updated"}:
        payload["status"] = "connected"
    elif event_type == "response.created":
        payload["status"] = "translating"
        payload["response_id"] = event.get("response_id") or event.get("response", {}).get("id", "")
    elif event_type in {
        "conversation.item.input_audio_transcription.delta",
        "conversation.item.input_audio_transcription.text",
    }:
        payload["item_id"] = event.get("item_id", "")
        payload["source_delta"] = event.get("delta") or event.get("stash") or event.get("text") or ""
    elif event_type == "conversation.item.input_audio_transcription.completed":
        payload["item_id"] = event.get("item_id", "")
        payload["source_final"] = event.get("transcript") or event.get("text") or ""
    elif event_type in {
        "response.audio_transcript.delta",
        "response.audio_transcript.text",
        "response.text.delta",
        "response.text.text",
    }:
        payload["response_id"] = event.get("response_id", "")
        payload["translation_delta"] = event.get("delta") or event.get("text") or ""
    elif event_type in {
        "response.audio_transcript.done",
        "response.text.done",
    }:
        payload["response_id"] = event.get("response_id", "")
        payload["translation_final"] = event.get("transcript") or event.get("text") or ""
    elif event_type == "response.audio.delta":
        audio = event.get("delta") or ""
        if not audio:
            return payload
        return {
            "type": "model_audio",
            "audio": audio,
            "sample_rate": OUTPUT_SAMPLE_RATE,
            "response_id": event.get("response_id", ""),
            "item_id": event.get("item_id", ""),
        }
    elif event_type == "response.done":
        response = event.get("response") or {}
        response_id = response.get("id", "")
        response_status = str(response.get("status") or "completed")
        transcripts = []
        pairs = []
        for item in response.get("output") or []:
            if item.get("id") and item.get("previous_item_id"):
                pairs.append(
                    {"item_id": item["id"], "previous_item_id": item["previous_item_id"]}
                )
            for content in item.get("content") or []:
                transcript = content.get("transcript")
                if transcript:
                    transcripts.append(str(transcript))
        payload.update(
            status="listening",
            response_id=response_id,
            response_done=True,
            response_status=response_status,
            usage=response.get("usage"),
        )
        if response_status not in {"completed", "in_progress"}:
            details = response.get("status_details") or {}
            reason = (details.get("error") or {}).get("message") or details.get("reason") or ""
            payload["response_error"] = (
                f"Модель завершила ответ со статусом {response_status}. {reason}".strip()
            )
        if pairs:
            payload["pairs"] = pairs
        if transcripts:
            payload["translation_final"] = "".join(transcripts)
    elif event_type == "session.finished":
        return {"type": "server_finished"}
    elif event_type == "error":
        payload["message"] = provider_error(event)
    return payload


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class LiveTranslateClient:
    def __init__(self, settings: Settings, options: SessionOptions, on_event: EventCallback):
        self.settings = settings
        self.options = options
        self.on_event = on_event
        self.upstream: Any = None
        self.finished = False

    async def connect(self) -> None:
        if not self.settings.api_key:
            raise LiveTranslateError("Сначала укажите DashScope API key в настройках")
        try:
            self.upstream = await websockets.connect(
                self.settings.ws_url,
                additional_headers={"Authorization": f"Bearer {self.settings.api_key}"},
                open_timeout=20,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
            )
            await self._wait_for("session.created")
            await self.upstream.send(
                json.dumps(
                    {
                        "event_id": make_event_id("session"),
                        "type": "session.update",
                        "session": build_session(self.options),
                    },
                    ensure_ascii=False,
                )
            )
            await self._wait_for("session.updated")
        except LiveTranslateError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise LiveTranslateError(self._connection_error(exc)) from exc

    async def _wait_for(self, expected_type: str) -> None:
        async def wait_loop() -> None:
            while True:
                event = await self.receive()
                if event.get("type") == "error":
                    raise LiveTranslateError(provider_error(event))
                await self.on_event(normalize_event(event))
                if event.get("type") == expected_type:
                    return

        try:
            await asyncio.wait_for(wait_loop(), timeout=25)
        except TimeoutError as exc:
            raise LiveTranslateError(f"LiveTranslate не отправил {expected_type} за 25 секунд") from exc

    async def receive(self) -> dict[str, Any]:
        if self.upstream is None:
            raise LiveTranslateError("Соединение с LiveTranslate не установлено")
        raw = await self.upstream.recv()
        try:
            event = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LiveTranslateError("LiveTranslate вернул некорректное JSON-событие") from exc
        if not isinstance(event, dict):
            raise LiveTranslateError("LiveTranslate вернул неожиданный формат события")
        return event

    async def send_audio(self, audio: bytes) -> None:
        if self.upstream is None or self.finished:
            return
        if not audio or len(audio) % 2 or len(audio) > MAX_AUDIO_FRAME_BYTES:
            raise LiveTranslateError("Некорректный PCM-фрейм от браузера")
        await self.upstream.send(
            json.dumps(
                {
                    "event_id": make_event_id("audio"),
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(audio).decode("ascii"),
                }
            )
        )

    async def finish(self) -> None:
        if self.upstream is None or self.finished:
            return
        self.finished = True
        await self.upstream.send(
            json.dumps({"event_id": make_event_id("finish"), "type": "session.finish"})
        )

    async def close(self) -> None:
        if self.upstream is not None:
            upstream, self.upstream = self.upstream, None
            try:
                await upstream.close()
            except Exception:
                pass

    @staticmethod
    def _connection_error(exc: Exception) -> str:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 401 or "401" in str(exc):
            return (
                "DashScope отклонил ключ (HTTP 401). Проверьте API key, регион и "
                "Workspace ID в настройках."
            )
        if status == 403 or "Access denied" in str(exc) or "403" in str(exc):
            return (
                "Доступ к модели запрещён. Откройте доступ к "
                "qwen3.8-livetranslate-flash-realtime в том же регионе, что и ключ."
            )
        return f"Не удалось подключиться к LiveTranslate: {exc}"
