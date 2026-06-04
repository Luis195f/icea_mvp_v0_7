#!/usr/bin/env bash
set -euo pipefail


# --- ICEA FAIL-FAST: verificación de permisos de volúmenes (no-root) ---
ICEA__PERM_DIRS="/app/backend/models /opt/venv"
for _d in $ICEA__PERM_DIRS; do
  if [ ! -d "$_d" ]; then
    echo "ERROR ICEA: directorio requerido no existe: $_d" >&2
    echo "Sugerencia: revise el Dockerfile/volúmenes y el servicio init-volumes (chown) antes de arrancar." >&2
    exit 1
  fi
  _tf="$_d/.icea_write_test.$$"
  if ! (umask 077 && touch "$_tf") 2>/dev/null; then
    echo "ERROR ICEA: permisos bloqueados. No se puede escribir en $_d con el usuario no-root (10001)." >&2
    echo "Sugerencia: verifique que el servicio init-volumes haya finalizado correctamente y que el volumen no esté con ownership root." >&2
    echo "Acción: ejecute: docker compose -f docker-compose.prod.yml logs init-volumes  (y reintente)." >&2
    exit 1
  fi
  rm -f "$_tf" >/dev/null 2>&1 || true
done
# --- FIN FAIL-FAST ---

# Optional: install enterprise extras at container start.
# Default is false to keep the MVP image small + fast.
if [ "${ICEA_INSTALL_OPTIONAL_DEPS:-false}" = "true" ]; then
  echo "[icea] Installing optional enterprise dependencies..."
  pip install --no-cache-dir -r requirements-optional.txt
fi

python manage.py migrate --noinput

# Optional: seed demo if requested
if [ "${ICEA_SEED_DEMO:-false}" = "true" ]; then
  python manage.py seed_demo --rows "${ICEA_DEMO_ROWS:-800}" --name "${ICEA_DEMO_NAME:-icea-demo}" --model-version "${ICEA_DEMO_VERSION:-v1}"
fi

# Default: WSGI (gunicorn). Enterprise option: ASGI (daphne/uvicorn) for realtime.
if [ "${ICEA_RUN_ASGI:-false}" = "true" ]; then
  if command -v daphne >/dev/null 2>&1; then
    echo "[icea] Starting ASGI server via daphne..."
    daphne -b 0.0.0.0 -p 8000 config.asgi:application
  elif command -v uvicorn >/dev/null 2>&1; then
    echo "[icea] Starting ASGI server via uvicorn..."
    uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers "${UVICORN_WORKERS:-2}"
  else
    echo "[icea] ICEA_RUN_ASGI=true but no ASGI server found (install daphne/uvicorn). Falling back to gunicorn."
    gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-2}" --timeout "${GUNICORN_TIMEOUT:-60}"
  fi
else
  gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-2}" --timeout "${GUNICORN_TIMEOUT:-60}"
fi
