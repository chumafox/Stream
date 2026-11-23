from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.config import Settings
from app.livetranslate import (
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    LiveTranslateClient,
    LiveTranslateError,
    SessionOptions,
    normalize_event,
)


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
settings = Settings.from_env()

app = FastAPI(title="StreamBridge", version=__version__)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ConfigUpdate(BaseModel):
    api_key: str = Field(default="", max_length=512)
    region: str = "intl"
    workspace_id: str = Field(default="", max_length=128)


class ClientConfig(BaseModel):
    type: str
    voice_clone_mode: str = "once"
    phrases: dict[str, str] = Field(default_factory=dict)


@app.middleware("http")
async def local_security_headers(request: Request, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self' ws: wss:; media-src 'self' blob:; "
        "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return settings.public_dict()


@app.post("/api/config")
async def update_config(update: ConfigUpdate) -> dict[str, Any]:
    try:
        settings.update_runtime(
            api_key=update.api_key,
            region=update.region,
            workspace_id=update.workspace_id,
        )
    except ValueError as exc:
        return {"ok": False, "message": str(exc), **settings.public_dict()}
    if not settings.configured:
        return {
            "ok": False,
            "message": "Введите API key или задайте DASHSCOPE_API_KEY в .env",
            **settings.public_dict(),
        }
    return {"ok": True, **settings.public_dict()}


@app.post("/api/config/check")
async def check_config() -> dict[str, Any]:
    client = LiveTranslateClient(settings, SessionOptions(), _discard_event)
    try:
        await client.connect()
        return {"ok": True, "message": "Соединение и настройки модели приняты"}
    except LiveTranslateError as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        await client.close()


async def _discard_event(_: dict[str, Any]) -> None:
    return None


async def safe_send(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except (RuntimeError, WebSocketDisconnect):
        return False


def valid_browser_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    return (urlparse(origin).hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}


@app.websocket("/ws/translate")
async def translate_socket(websocket: WebSocket) -> None:
    if not valid_browser_origin(websocket):
        await websocket.close(code=1008, reason="Local origin required")
        return
    await websocket.accept()
    client: LiveTranslateClient | None = None
    tasks: set[asyncio.Task[Any]] = set()

    try:
        raw_config = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        config = ClientConfig.model_validate_json(raw_config)
        if config.type != "config":
            raise ValueError("Первое сообщение должно иметь type=config")
        options = SessionOptions(
            voice_clone_mode=config.voice_clone_mode,
            phrases=config.phrases,
        )
        options.validate()

        client = LiveTranslateClient(settings, options, lambda event: safe_send(websocket, event))
        await client.connect()
        if not await safe_send(
            websocket,
            {
                "type": "server_ready",
                "input_sample_rate": INPUT_SAMPLE_RATE,
                "output_sample_rate": OUTPUT_SAMPLE_RATE,
                "model": settings.model,
                "voice_clone_mode": options.voice_clone_mode,
            },
        ):
            return

        async def browser_to_model() -> bool:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return False
                audio = message.get("bytes")
                if audio:
                    await client.send_audio(audio)
                    continue
                text = message.get("text")
                if not text:
                    continue
                try:
                    control = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if control.get("type") == "finish":
                    await client.finish()
                    return True

        async def model_to_browser() -> None:
            while True:
                event = await client.receive()
                if event.get("type") == "error":
                    await safe_send(
                        websocket,
                        {"type": "server_error", "message": normalize_event(event).get("message")},
                    )
                    return
                normalized = normalize_event(event)
                if not await safe_send(websocket, normalized):
                    return
                if event.get("type") == "session.finished":
                    return

        browser_task = asyncio.create_task(browser_to_model())
        model_task = asyncio.create_task(model_to_browser())
        tasks = {browser_task, model_task}
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        if browser_task in done:
            finishing = browser_task.result()
            if finishing and not model_task.done():
                try:
                    await asyncio.wait_for(model_task, timeout=45)
                except TimeoutError:
                    await safe_send(
                        websocket,
                        {"type": "server_error", "message": "Тайм-аут при получении последней фразы"},
                    )
            else:
                model_task.cancel()
        else:
            model_task.result()
            browser_task.cancel()
    except WebSocketDisconnect:
        pass
    except (ValueError, json.JSONDecodeError) as exc:
        await safe_send(websocket, {"type": "server_error", "message": str(exc)})
    except LiveTranslateError as exc:
        await safe_send(websocket, {"type": "server_error", "message": str(exc)})
    except Exception as exc:
        await safe_send(
            websocket,
            {"type": "server_error", "message": f"Сессия остановлена: {exc}"},
        )
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if client is not None:
            await client.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass
