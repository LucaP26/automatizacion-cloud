# Automation Manager

Plataforma web para registrar scripts de Python, programarlos con cron, ejecutarlos y ver el resultado de cada corrida. Proyecto de portfolio enfocado en backend + DevOps: arquitectura de API prolija, containerización con propósito y CI/CD real.

## Estado actual: Fase 2 — Scheduler

- CRUD de automatizaciones (`Automation`).
- Ejecución manual vía `subprocess`, sin aislamiento todavía (eso llega en la Fase 3).
- Historial de ejecuciones (`Execution`) con status, logs y timestamps.
- **Las automatizaciones activas corren solas según su cron**, sin que nadie llame a `/run` — scheduler con APScheduler, integrado al ciclo de vida de la API.

Fases siguientes (spec completa en `prompt_claude_code.md`): aislamiento en Docker + secretos encriptados, auth JWT + notificaciones, infraestructura con Docker Compose / Terraform / GitHub Actions.

## Arquitectura

```
app/
  main.py           # arma la app FastAPI y registra routers
  config.py         # settings desde variables de entorno (pydantic-settings)
  database.py       # engine/session de SQLAlchemy, Base declarativa, get_db()
  models/           # entidades ORM (User, Automation, Execution)
  schemas/          # contratos Pydantic de entrada/salida (independientes del ORM)
  routers/          # capa HTTP: valida con schemas, delega en services
  services/         # lógica de negocio: ejecución de scripts (executor.py) y scheduling (scheduler.py)
alembic/            # migraciones de base de datos
tests/              # pytest, contra una base de datos Postgres separada
```

Cada capa habla solo con la de al lado: el router no arma queries de negocio complejas ni corre subprocess directamente, el service no sabe nada de HTTP, y los schemas de Pydantic son independientes de los modelos de SQLAlchemy.

### Decisiones de arquitectura

**Modelos (`models/`) vs. schemas (`schemas/`), separados a propósito.** El modelo ORM define cómo se guardan los datos; el schema define el contrato JSON de la API. Son cosas que cambian por razones distintas: el día que `User` tenga `hashed_password` (Fase 4), ese campo no va a existir en `UserRead` — es estructuralmente imposible que se filtre en una respuesta, no depende de que alguien se acuerde de excluirlo a mano. Además, un objeto de SQLAlchemy ni siquiera es serializable a JSON directamente (tiene metadata interna del ORM, como `_sa_instance_state`) — el schema es el traductor obligatorio entre "objeto Python interno" y "JSON que viaja por HTTP", en los dos sentidos: valida lo que entra (por ejemplo, rechaza un cron inválido con 422 antes de tocar la base) y filtra lo que sale.

**`subprocess` en esta fase, no Docker todavía.** El objetivo de la Fase 1 es validar el flujo completo (registrar → ejecutar → ver resultado) con la menor cantidad de piezas móviles. `subprocess` corre el script en un proceso del sistema operativo separado del proceso de la API — más aislado que un `exec()` en el mismo intérprete, pero lejos de ser seguro para código arbitrario de terceros (mismo filesystem, mismos permisos, mismas dependencias que la API). Por eso la Fase 3 lo reemplaza por ejecución en un container Docker aislado, con timeout y límites de recursos. El `timeout` ya es obligatorio desde esta fase (`EXECUTION_TIMEOUT_SECONDS`): sin él, un script con un loop infinito cuelga el request HTTP completo, porque en esta fase la ejecución es síncrona y bloqueante.

**Un `User` mínimo ya en Fase 1, sin password.** `Automation` necesita un `user_id` con integridad referencial real (foreign key), no un entero suelto sin garantías. Se creó una tabla `users` mínima (`id`, `email`, `created_at`) para tener esa FK desde el principio, sin agregar campos de auth (`hashed_password`) que todavía no tienen ningún endpoint que los use — eso llega recién en la Fase 4, con su propia migración.

**Postgres desde el arranque, nunca SQLite.** El proyecto es un ejercicio de mostrar prácticas reales de backend/DevOps, y Postgres es lo que se termina desplegando en Azure (Fase 5). Usar SQLite en dev/test y Postgres en producción esconde diferencias de tipos y comportamiento (enums, timezones, constraints) que después aparecen como bugs sorpresa recién en producción. Los tests corren contra una base Postgres separada (`TEST_DATABASE_URL`), nunca contra la de desarrollo — cada test corre dentro de una transacción que se revierte (`rollback`) al terminar, así quedan aislados entre sí sin importar el orden en que corran.

**Alembic para migraciones, no `Base.metadata.create_all()`.** `create_all()` solo crea tablas que no existen — no sabe alterar una tabla que ya tiene datos (por ejemplo, agregarle una columna nueva a `users` en la Fase 4). Alembic versiona cada cambio de schema como un script con `upgrade()`/`downgrade()`, encadenado al anterior, aplicable de forma repetible en cualquier entorno. El `--autogenerate` compara los modelos contra el estado real de la base y genera el diff, pero cada migración generada se revisa a mano antes de aplicarla — el autogenerate no siempre acierta (por ejemplo, un rename de columna lo ve como "borrar una y crear otra").

**Dependency injection con `Depends(get_db)` + `app.dependency_overrides` en tests.** Cada endpoint recibe su propia sesión de base de datos a través de `Depends(get_db)`, en vez de una sesión global compartida — así requests concurrentes no se pisan. En los tests, `app.dependency_overrides[get_db]` reemplaza esa dependencia por una versión que apunta a la base de test y la envuelve en una transacción descartable, sin tener que tocar ni una línea del código de producción.

**APScheduler en vez de Celery.** Celery necesita infraestructura aparte (un broker como Redis/RabbitMQ, al menos un proceso worker independiente) — la herramienta correcta cuando hay que distribuir trabajo entre varios workers en varias máquinas con colas reales. Para una sola instancia de API con pocas automatizaciones, eso es sobre-ingeniería: APScheduler corre *adentro* del mismo proceso, sin piezas adicionales. El trade-off consciente: si el día de mañana la API corre en más de una instancia en paralelo, cada una tendría su propio scheduler y dispararía el mismo job dos veces — un problema que Celery no tiene. Para el tamaño actual del proyecto, es la elección correcta; con más escala, dejaría de serlo.

**`BackgroundScheduler`, no `AsyncIOScheduler`.** Toda la app es síncrona (routers con `def`, no `async def`; `subprocess.run` bloqueante). `BackgroundScheduler` corre cada job en un hilo de un pool, desacoplado del event loop — consistente con el resto del código. `AsyncIOScheduler` asume que la app corre sobre asyncio; usarlo con código sync bloqueante arriesga trabar el event loop entero.

**Job store en memoria, no `SQLAlchemyJobStore`.** La fuente de verdad de qué debería estar programado es la tabla `automations` (con su `cron_expression` e `is_active`), no un almacén separado de APScheduler. En cada arranque de la API, el schedule completo se reconstruye leyendo las automatizaciones activas (`load_scheduled_automations`) — más simple que mantener una tabla `apscheduler_jobs` sincronizada aparte, y evita que las dos fuentes de verdad se desincronicen entre sí (por ejemplo, si alguien edita el cron directo en la base).

**Hallazgo de APScheduler que vale la pena documentar:** `scheduler.add_job(..., replace_existing=True)` no deduplica si se llama *antes* de `scheduler.start()` — antes de arrancar, los jobs quedan en una lista interna (`_pending_jobs`) que no aplica `replace_existing` entre sí misma, y al arrancar, duplicados sobreviven todos. Por eso en el `lifespan` de `main.py` el scheduler arranca *antes* de cargar los jobs, no después.

**El scheduler usa la base real, no `get_db()`.** El job que dispara un cron corre en un hilo aparte, con su propia sesión (`SessionLocal` de `app.database` directo) — no pasa por la inyección de dependencias de FastAPI, así que `app.dependency_overrides` no lo alcanza en los tests. Por eso el test que verifica un cron disparando solo (`tests/test_scheduler_integration.py`) no usa las fixtures `client`/`db_session` de siempre: usa la base real configurada y limpia a mano lo que crea, en vez de depender del rollback automático del resto de la suite.

## Cómo levantarlo en local

> Nota: si estás retomando este proyecto en una máquina distinta a la última
> vez, mirá [`CLAUDE_PROGRESS.md`](CLAUDE_PROGRESS.md) antes de nada — ahí
> queda registrado en qué fase se quedó cada máquina y cualquier diferencia
> de entorno (por ejemplo, una máquina puede estar usando Postgres en WSL2
> en vez de Docker Desktop).

### 1. Prerrequisitos
- Python 3.12+
- Docker Desktop corriendo (para Postgres; todavía no se usa para la API en sí)
  — si no tenés Docker Desktop disponible, una alternativa es Postgres
  corriendo dentro de WSL2 (ver `CLAUDE_PROGRESS.md` para el setup usado en
  la PC de escritorio, incluido `scripts/start-db.ps1`).

### 2. Levantar Postgres

```powershell
docker run --name automation-postgres `
  -e POSTGRES_USER=automation_user `
  -e POSTGRES_PASSWORD=automation_pass `
  -e POSTGRES_DB=automation_db `
  -p 5432:5432 `
  -d postgres:16

# Base separada para tests (mismo servidor, otra base)
docker exec automation-postgres psql -U automation_user -d automation_db -c "CREATE DATABASE automation_test_db;"
```

### 3. Entorno Python

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Si PowerShell bloquea la activación con un error de ejecución de scripts:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Después creá un archivo `.env` en la raíz con:

```
DATABASE_URL=postgresql+psycopg://automation_user:automation_pass@localhost:5432/automation_db
TEST_DATABASE_URL=postgresql+psycopg://automation_user:automation_pass@localhost:5432/automation_test_db
EXECUTION_TIMEOUT_SECONDS=30
```

### 4. Migraciones

```powershell
alembic upgrade head
```

### 5. Levantar la API

```powershell
uvicorn app.main:app --reload
```

La API queda en `http://localhost:8000`, docs interactivas (Swagger, autogeneradas desde los schemas de Pydantic) en `http://localhost:8000/docs`.

## Cómo probarlo

### Tests automatizados

```powershell
pytest
```

Corren contra `automation_test_db`, nunca contra la base de desarrollo. Por default no incluyen el test de integración real del scheduler (marcado `slow`, tarda hasta ~65s porque espera a que un cron real dispare) — para correrlo:

```powershell
pytest tests/test_scheduler_integration.py -m slow
```

### Manualmente con curl

```powershell
# Crear un usuario directo en la DB (todavía no hay endpoint, llega en Fase 4)
docker exec automation-postgres psql -U automation_user -d automation_db -c "INSERT INTO users (email, created_at) VALUES ('demo@example.com', now());"

# Crear una automatización
curl -X POST http://localhost:8000/automations `
  -H "Content-Type: application/json" `
  -d '{\"user_id\": 1, \"name\": \"Saludo\", \"script_code\": \"print(1+1)\", \"cron_expression\": \"*/5 * * * *\"}'

# Ejecutarla manualmente (reemplazar 1 por el id devuelto arriba)
curl -X POST http://localhost:8000/automations/1/run

# Ver el historial de ejecuciones
curl http://localhost:8000/automations/1/executions
```

O directamente desde `http://localhost:8000/docs`, con el botón "Try it out" en cada endpoint.

## Modelo de datos

**User**: `id`, `email` (único), `created_at`. Mínimo a propósito — sin campos de auth hasta la Fase 4.

**Automation**: `id`, `user_id` (FK a `users`), `name`, `script_code` (texto), `cron_expression`, `is_active`, `created_at`, `updated_at`.

**Execution**: `id`, `automation_id` (FK a `automations`), `status` (`pending` / `running` / `success` / `failed`), `output_log`, `started_at`, `finished_at`.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/automations` | Crea una automatización |
| `GET` | `/automations` | Lista todas |
| `GET` | `/automations/{id}` | Obtiene una puntual |
| `PATCH` | `/automations/{id}` | Actualiza parcialmente |
| `DELETE` | `/automations/{id}` | Borra |
| `POST` | `/automations/{id}/run` | Corre el script ahora mismo (síncrono) y devuelve la `Execution` resultante |
| `GET` | `/automations/{id}/executions` | Historial de ejecuciones de una automatización |
| `GET` | `/automations/{id}/executions/{execution_id}` | Una ejecución puntual |
| `GET` | `/health` | Health check |
