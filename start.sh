#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

PYTHON_BIN=""
for candidate in \
  python3.12 \
  python3.11 \
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
  /usr/local/bin/python3.12 \
  /usr/local/bin/python3.11 \
  python3
do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Нужен Python 3.11+: на 3.9/3.10 не работают тайм-ауты asyncio этого проекта." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  "$PYTHON_BIN" -m venv .venv
fi
if ! .venv/bin/python -c "import fastapi, uvicorn, websockets, dotenv, certifi" 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/python run.py "$@"
