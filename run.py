from __future__ import annotations

import argparse
import threading
import webbrowser

from dotenv import load_dotenv

load_dotenv()

import uvicorn  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="StreamBridge RU → ZH")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Для защиты API key сервис разрешается запускать только на localhost")
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
