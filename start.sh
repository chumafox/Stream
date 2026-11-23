#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(
        "Нужен Python 3.11+: на 3.9/3.10 не работают тайм-ауты asyncio этого проекта."
    )
PY

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c "import fastapi, uvicorn, websockets, dotenv" 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/python run.py "$@"
