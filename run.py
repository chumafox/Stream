from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402


LOG_FILE = Path(__file__).resolve().parent / "streambridge.log"


def configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])


def main() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit(
            "Нужен Python 3.11 или новее: в более старых версиях не работают тайм-ауты "
            "asyncio в этом проекте. Установите Python 3.11+ и запустите снова."
        )

    parser = argparse.ArgumentParser(description="StreamBridge RU → ZH")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Для защиты API key сервис разрешается запускать только на localhost")

    configure_logging()
    logging.getLogger("streambridge").info(
        "Запуск StreamBridge: %s:%s, Python %s", args.host, args.port, sys.version.split()[0]
    )
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
