# Automation Manager

Plataforma web para registrar scripts de Python, programarlos con cron, ejecutarlos y ver el resultado de cada corrida. Proyecto de portfolio enfocado en backend + DevOps: arquitectura de API prolija, containerización con propósito y CI/CD real.

## Estado actual: Fase 3 — Aislamiento

- CRUD de automatizaciones (`Automation`).
- Las automatizaciones activas corren solas según su cron (scheduler con APScheduler), o manualmente vía `POST /automations/{id}/run`.
- Historial de ejecuciones (`Execution`) con status, logs y timestamps.
- **Cada ejecución corre dentro de un container Docker descartable**, no en el proceso de la API — con timeout, límites de memoria/CPU, y filesystem de solo lectura.
- **Secretos (variables de entorno) por automatización, encriptados en la base**, inyectados al container solo en el momento de ejecutar — nunca en texto plano, nunca expuestos por la API.

Fases siguientes (spec completa en `prompt_claude_code.md`): auth JWT + notificaciones, infraestructura con Docker Compose / Terraform / GitHub Actions.

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

**`subprocess` en la Fase 1, Docker desde la Fase 3.** El objetivo de la Fase 1 era validar el flujo completo (registrar → ejecutar → ver resultado) con la menor cantidad de piezas móviles. `subprocess` corre el script en un proceso del sistema operativo separado del proceso de la API — más aislado que un `exec()` en el mismo intérprete, pero lejos de ser seguro para código arbitrario de terceros (mismo filesystem, mismos permisos, mismas dependencias que la API). El `timeout` ya era obligatorio desde esa fase (`EXECUTION_TIMEOUT_SECONDS`): sin él, un script con un loop infinito cuelga el request HTTP completo, porque la ejecución es síncrona y bloqueante. En la Fase 3 el mecanismo de ejecución cambió por completo (ver más abajo), pero **la firma de `run_automation_script(db, automation)` nunca se tocó** — ni el router ni el scheduler se enteraron del cambio, solo `executor.py`.

**Un container Docker por ejecución, no un pool de workers reutilizados.** Un container nuevo y descartable por cada corrida es más simple de razonar (nada persiste entre ejecuciones, no hay estado compartido que limpiar) y más seguro (una ejecución comprometida no contamina la siguiente). El costo es el overhead de arrancar un container cada vez — aceptable para este proyecto; un pool de workers reutilizados reduciría ese overhead a cambio de mucha más complejidad (y de tener que limpiar el estado entre usos a mano), algo que recién se justificaría con un volumen de ejecuciones mucho mayor.

**El código del script se monta como volumen de solo lectura, nunca se bakea en la imagen.** El `Dockerfile` (`docker/sandbox.Dockerfile`) no copia nada del código de las automatizaciones — es una imagen base fija (`python:3.12-slim`, usuario sin privilegios) que se construye una sola vez. En cada ejecución, el código se escribe a un archivo temporal del host y se monta en `/sandbox` con `mode="ro"`. Si se bakeara el código en la imagen, cada automatización necesitaría su propia imagen (y reconstruirla en cada edición) — totalmente innecesario cuando Docker ya soporta montar archivos en runtime.

**Filesystem de solo lectura + `tmpfs` para `/tmp`.** El container corre con `read_only=True` (todo el filesystem raíz es inmutable) salvo un `/tmp` efímero en memoria (`tmpfs`) para que librerías que necesiten escribir archivos temporales no fallen. Ni el código del script ni nada del sistema de archivos del container puede modificarse desde dentro — verificado en `tests/test_sandbox.py` y a mano (un intento de reescribir `/sandbox/script.py` tira `OSError: Read-only file system`).

**Secretos encriptados con Fernet, nunca en el `script_code`.** Antes de esta fase, si una automatización necesitaba una API key, la única opción era pegarla directo en el texto del script — texto plano en la base de datos. Ahora cada automatización tiene un campo `secrets_encrypted` (encriptado con `cryptography.fernet.Fernet`, clave en `SECRET_KEY`), que solo se desencripta en el momento de lanzar el container, inyectado como variables de entorno. El schema `AutomationRead` nunca incluye `secrets` ni `secrets_encrypted` — mismo patrón que `hashed_password` en `User`: estructuralmente imposible que se filtre en una respuesta.

**Hallazgo de plataforma al manejar el timeout del container:** `container.wait(timeout=...)` del SDK de Docker, al vencer el timeout, tira `requests.exceptions.ReadTimeout` en Linux/Mac (socket Unix) pero `requests.exceptions.ConnectionError` en Windows con Docker Desktop (transporte vía named pipe) — hay que atrapar los dos para que el timeout funcione igual en cualquier plataforma. Verificado empíricamente en ambos casos.

**Trade-off consciente: la red del container queda habilitada por default.** Muchas automatizaciones reales necesitan llamar a una API externa (el ejemplo que venimos usando: un reporte del clima). Deshabilitar la red por default rompería ese caso de uso legítimo. El aislamiento de esta fase se enfoca en filesystem, memoria, CPU y tiempo — no en red. Si en el futuro se necesitara bloquear red para automatizaciones específicas, `network_disabled=True` es un parámetro más a exponer por automatización, no un cambio de arquitectura.

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
- Docker Desktop corriendo — para Postgres, y desde la Fase 3 también para
  ejecutar las automatizaciones (cada corrida es un container). Ya no es
  opcional como en fases anteriores.
  Si no tenés Docker Desktop disponible para Postgres, una alternativa es
  Postgres corriendo dentro de WSL2 (ver `CLAUDE_PROGRESS.md` para el setup
  usado en la PC de escritorio, incluido `scripts/start-db.ps1`) — pero la
  ejecución de automatizaciones sí necesita Docker Desktop en sí.

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

Después copiá `.env.example` a `.env` (`copy .env.example .env`) y generá tu propia clave de encriptación:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Pegá el resultado en `SECRET_KEY` dentro de `.env`. Nunca reuses una clave de ejemplo ni la compartas — es la que encripta los secretos de las automatizaciones.

### 4. Construir la imagen del sandbox

```powershell
docker build -f docker/sandbox.Dockerfile -t automation-sandbox:latest docker
```

Se construye una sola vez (no hace falta reconstruirla salvo que cambie `docker/sandbox.Dockerfile`). Cada ejecución de una automatización arranca un container nuevo a partir de esta imagen.

### 5. Migraciones

```powershell
alembic upgrade head
```

### 6. Levantar la API

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

# Una automatización con un secreto, que lo lee como env var
curl -X POST http://localhost:8000/automations `
  -H "Content-Type: application/json" `
  -d '{\"user_id\": 1, \"name\": \"Con secreto\", \"cron_expression\": \"*/5 * * * *\", \"secrets\": {\"API_KEY\": \"abc123\"}, \"script_code\": \"import os; print(os.environ[\\\"API_KEY\\\"])\"}'
```

O directamente desde `http://localhost:8000/docs`, con el botón "Try it out" en cada endpoint.

## Modelo de datos

**User**: `id`, `email` (único), `created_at`. Mínimo a propósito — sin campos de auth hasta la Fase 4.

**Automation**: `id`, `user_id` (FK a `users`), `name`, `script_code` (texto), `cron_expression`, `is_active`, `secrets_encrypted` (binario, nunca expuesto por la API), `created_at`, `updated_at`.

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
