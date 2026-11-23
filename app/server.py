from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Позволяет запускать и `python run.py`, и `uvicorn app.server:app`.
load_dotenv()

from app import __version__  # noqa: E402
from app.config import Settings  # noqa: E402
from app.livetranslate import (  # noqa: E402
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
logger = logging.getLogger("streambridge")
check_lock = asyncio.Lock()

app = FastAPI(title="StreamBridge", version=__version__)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def local_origin(origin: str | None) -> bool:
    if not origin:
        return True
    return (urlparse(origin).hostname or "").lower() in LOCAL_HOSTNAMES


def forbid_cross_origin(request: Request) -> JSONResponse | None:
    """POST без JSON-preflight (simple request) не должен дёргать облако с чужой страницы."""
    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site in {"cross-site", "same-site"} and not local_origin(request.headers.get("origin")):
        return JSONResponse({"ok": False, "message": "Разрешены только локальные запросы"}, status_code=403)
    if not local_origin(request.headers.get("origin")):
        return JSONResponse({"ok": False, "message": "Разрешены только локальные запросы"}, status_code=403)
    return None


class ConfigUpdate(BaseModel):
    api_key: str = Field(default="", max_length=512)
    region: str = "intl"
    workspace_id: str = Field(default="", max_length=128)


class ClientConfig(BaseModel):
    type: str
    voice_clone_mode: str = "once"
    phrases: dict[str, str] = Field(default_factory=dict)
    voice_id: str = Field(default="", max_length=128)


@app.middleware("http")
async def local_security_headers(request: Request, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; media-src 'self' blob:; "
        "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return settings.public_dict()


@app.post("/api/config", response_model=None)
async def update_config(update: ConfigUpdate, request: Request) -> dict[str, Any] | JSONResponse:
    blocked = forbid_cross_origin(request)
    if blocked is not None:
        return blocked
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
    logger.info("Настройки API обновлены: region=%s, workspace=%s", settings.region, bool(settings.workspace_id))
    return {"ok": True, **settings.public_dict()}


@app.post("/api/config/check", response_model=None)
async def check_config(request: Request) -> dict[str, Any] | JSONResponse:
    blocked = forbid_cross_origin(request)
    if blocked is not None:
        return blocked
    if check_lock.locked():
        return {"ok": False, "message": "Проверка уже выполняется. Дождитесь её завершения."}
    async with check_lock:
        client = LiveTranslateClient(settings, SessionOptions(), _discard_event)
        try:
            await client.connect()
            logger.info("Проверка подключения к модели успешна")
            return {"ok": True, "message": "Соединение и настройки модели приняты"}
        except LiveTranslateError as exc:
            logger.warning("Проверка подключения не прошла: %s", exc)
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
    return local_origin(websocket.headers.get("origin"))


@app.websocket("/ws/translate")
async def translate_socket(websocket: WebSocket) -> None:
    if not valid_browser_origin(websocket):
        await websocket.close(code=1008, reason="Local origin required")
        return
    await websocket.accept()
    client: LiveTranslateClient | None = None
    tasks: set[asyncio.Task[Any]] = set()
    ready = False

    try:
        raw_config = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        config = ClientConfig.model_validate_json(raw_config)
        if config.type != "config":
            raise ValueError("Первое сообщение должно иметь type=config")
        options = SessionOptions(
            voice_clone_mode=config.voice_clone_mode,
            phrases=config.phrases,
            voice_id=config.voice_id,
        )
        options.validate()

        client = LiveTranslateClient(settings, options, lambda event: safe_send(websocket, event))
        await client.connect()
        logger.info(
            "Сессия перевода открыта: model=%s, clone=%s, terms=%d",
            settings.model,
            options.voice_clone_mode,
            len(options.phrases),
        )
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
        ready = True

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
                    message = normalize_event(event).get("message")
                    logger.warning("Ошибка провайдера в сессии: %s", message)
                    await safe_send(
                        websocket,
                        {"type": "server_error", "message": message, "recoverable": True},
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
                        {
                            "type": "server_error",
                            "message": "Тайм-аут при получении последней фразы",
                            "recoverable": False,
                        },
                    )
            else:
                model_task.cancel()
        else:
            model_task.result()
            browser_task.cancel()
    except WebSocketDisconnect:
        pass
    except TimeoutError:
        await safe_send(
            websocket,
            {
                "type": "server_error",
                "message": "Браузер не прислал настройки сессии за 15 секунд",
                "recoverable": False,
            },
        )
    except (ValueError, json.JSONDecodeError) as exc:
        await safe_send(
            websocket, {"type": "server_error", "message": str(exc), "recoverable": False}
        )
    except LiveTranslateError as exc:
        logger.warning("Сессия остановлена: %s", exc)
        await safe_send(
            websocket, {"type": "server_error", "message": str(exc), "recoverable": ready}
        )
    except Exception as exc:
        logger.exception("Непредвиденная ошибка сессии")
        await safe_send(
            websocket,
            {
                "type": "server_error",
                "message": f"Сессия остановлена: {exc}",
                "recoverable": ready,
            },
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
        if ready:
            logger.info("Сессия перевода закрыта")
