#!/usr/bin/env bash
# Smoke test end-to-end contra un stack ya levantado (docker compose up, o la
# VM de Azure): registra un usuario, hace login, crea una automatización, la
# corre a mano y espera a que el worker la corra sola por cron.
#
#   ./scripts/smoke-test.sh                       # contra http://localhost:8000
#   ./scripts/smoke-test.sh http://<ip-de-la-vm>:8000
#
# A diferencia de pytest (que corre la app directo en el host), esto prueba
# la app adentro de sus containers: red de compose, migraciones, socket de
# Docker, directorio compartido de ejecuciones y el worker separado.
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
EMAIL="smoke-$(date +%s)-$RANDOM@example.com"
PASSWORD="smoke-test-password"

# python3 en Linux/CI, python en Git Bash de Windows (donde "python3" puede
# ser el alias vacío de la Microsoft Store).
for candidate in python3 python; do
    if "$candidate" -c "" >/dev/null 2>&1; then PY="$candidate"; break; fi
done

# Lee JSON de stdin y evalúa una expresión de Python sobre él (`d`).
json_eval() {
    "$PY" -c "import json, sys; d = json.load(sys.stdin); print($1)"
}

echo "==> /health"
curl -fsS "$BASE_URL/health"
echo

echo "==> registro + login ($EMAIL)"
curl -fsS -X POST "$BASE_URL/auth/register" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}" >/dev/null
TOKEN=$(curl -fsS -X POST "$BASE_URL/auth/login" \
    -d "username=$EMAIL&password=$PASSWORD" | json_eval "d['access_token']")
AUTH="Authorization: Bearer $TOKEN"

echo "==> crear automatización (cron: cada minuto)"
AUTOMATION_ID=$(curl -fsS -X POST "$BASE_URL/automations" \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d '{"name": "smoke test", "script_code": "import os; print(\"hola desde el sandbox\", os.environ[\"SMOKE\"])", "cron_expression": "* * * * *", "secrets": {"SMOKE": "ok"}}' \
    | json_eval "d['id']")
echo "automation_id=$AUTOMATION_ID"

echo "==> ejecución manual (POST /run, la corre la API)"
RUN=$(curl -fsS -X POST "$BASE_URL/automations/$AUTOMATION_ID/run" -H "$AUTH")
echo "$RUN"
STATUS=$(echo "$RUN" | json_eval "d['status']")
if [ "$STATUS" != "success" ]; then
    echo "FALLO: la ejecución manual terminó en '$STATUS'" >&2
    exit 1
fi

# El worker la agenda en su próxima sincronización (<= 30s) y el cron
# dispara al cambiar el minuto (<= 60s): 120s cubre el peor caso.
echo "==> esperando la ejecución automática del worker (hasta 120s)"
for _ in $(seq 1 40); do
    # >= 2: la manual de arriba + al menos una disparada por el cron.
    SUCCESSES=$(curl -fsS "$BASE_URL/automations/$AUTOMATION_ID/executions" -H "$AUTH" \
        | json_eval "sum(1 for e in d if e['status'] == 'success')")
    if [ "${SUCCESSES:-0}" -ge 2 ]; then
        echo "OK: el worker corrió la automatización por cron"
        curl -fsS -X DELETE "$BASE_URL/automations/$AUTOMATION_ID" -H "$AUTH"
        echo "==> smoke test OK"
        exit 0
    fi
    sleep 3
done

echo "FALLO: el worker no corrió la automatización en 120s" >&2
exit 1
