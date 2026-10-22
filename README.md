# Automation Manager

Plataforma web para registrar scripts de Python, programarlos con cron, ejecutarlos y ver el resultado de cada corrida. Proyecto de portfolio enfocado en backend + DevOps: arquitectura de API prolija, containerización con propósito y CI/CD real.

## Estado actual: Fase 4 — Auth y notificaciones

- CRUD de automatizaciones (`Automation`), **scopeado por usuario**: cada uno ve, edita, borra y corre solo las suyas.
- Las automatizaciones activas corren solas según su cron (scheduler con APScheduler), o manualmente vía `POST /automations/{id}/run`.
- Historial de ejecuciones (`Execution`) con status, logs y timestamps.
- Cada ejecución corre dentro de un container Docker descartable, con timeout, límites de memoria/CPU, y filesystem de solo lectura.
- Secretos (variables de entorno) por automatización, encriptados en la base, inyectados al container solo en el momento de ejecutar.
- **Autenticación con JWT**: `POST /auth/register` y `POST /auth/login`. Todos los endpoints de `/automations` requieren un token válido.
- **Notificación por webhook** (p.ej. Slack) cuando una ejecución termina en `failed` — opcional, no-op si no está configurada.

Fase siguiente (spec completa en `prompt_claude_code.md`): infraestructura con Docker Compose / Terraform / GitHub Actions.

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

**Modelos (`models/`) vs. schemas (`schemas/`), separados a propósito.** El modelo ORM define cómo se guardan los datos; el schema define el contrato JSON de la API. Son cosas que cambian por razones distintas: `User` tiene `hashed_password` desde la Fase 4, pero ese campo no existe en `UserRead` — es estructuralmente imposible que se filtre en una respuesta, no depende de que alguien se acuerde de excluirlo a mano. Además, un objeto de SQLAlchemy ni siquiera es serializable a JSON directamente (tiene metadata interna del ORM, como `_sa_instance_state`) — el schema es el traductor obligatorio entre "objeto Python interno" y "JSON que viaja por HTTP", en los dos sentidos: valida lo que entra (por ejemplo, rechaza un cron inválido con 422 antes de tocar la base) y filtra lo que sale.

**`subprocess` en la Fase 1, Docker desde la Fase 3.** El objetivo de la Fase 1 era validar el flujo completo (registrar → ejecutar → ver resultado) con la menor cantidad de piezas móviles. `subprocess` corre el script en un proceso del sistema operativo separado del proceso de la API — más aislado que un `exec()` en el mismo intérprete, pero lejos de ser seguro para código arbitrario de terceros (mismo filesystem, mismos permisos, mismas dependencias que la API). El `timeout` ya era obligatorio desde esa fase (`EXECUTION_TIMEOUT_SECONDS`): sin él, un script con un loop infinito cuelga el request HTTP completo, porque la ejecución es síncrona y bloqueante. En la Fase 3 el mecanismo de ejecución cambió por completo (ver más abajo), pero **la firma de `run_automation_script(db, automation)` nunca se tocó** — ni el router ni el scheduler se enteraron del cambio, solo `executor.py`.

**Un container Docker por ejecución, no un pool de workers reutilizados.** Un container nuevo y descartable por cada corrida es más simple de razonar (nada persiste entre ejecuciones, no hay estado compartido que limpiar) y más seguro (una ejecución comprometida no contamina la siguiente). El costo es el overhead de arrancar un container cada vez — aceptable para este proyecto; un pool de workers reutilizados reduciría ese overhead a cambio de mucha más complejidad (y de tener que limpiar el estado entre usos a mano), algo que recién se justificaría con un volumen de ejecuciones mucho mayor.

**El código del script se monta como volumen de solo lectura, nunca se bakea en la imagen.** El `Dockerfile` (`docker/sandbox.Dockerfile`) no copia nada del código de las automatizaciones — es una imagen base fija (`python:3.12-slim`, usuario sin privilegios) que se construye una sola vez. En cada ejecución, el código se escribe a un archivo temporal del host y se monta en `/sandbox` con `mode="ro"`. Si se bakeara el código en la imagen, cada automatización necesitaría su propia imagen (y reconstruirla en cada edición) — totalmente innecesario cuando Docker ya soporta montar archivos en runtime.

**Filesystem de solo lectura + `tmpfs` para `/tmp`.** El container corre con `read_only=True` (todo el filesystem raíz es inmutable) salvo un `/tmp` efímero en memoria (`tmpfs`) para que librerías que necesiten escribir archivos temporales no fallen. Ni el código del script ni nada del sistema de archivos del container puede modificarse desde dentro — verificado en `tests/test_sandbox.py` y a mano (un intento de reescribir `/sandbox/script.py` tira `OSError: Read-only file system`).

**Secretos encriptados con Fernet, nunca en el `script_code`.** Antes de esta fase, si una automatización necesitaba una API key, la única opción era pegarla directo en el texto del script — texto plano en la base de datos. Ahora cada automatización tiene un campo `secrets_encrypted` (encriptado con `cryptography.fernet.Fernet`, clave en `SECRET_KEY`), que solo se desencripta en el momento de lanzar el container, inyectado como variables de entorno. El schema `AutomationRead` nunca incluye `secrets` ni `secrets_encrypted` — mismo patrón que `hashed_password` en `User`: estructuralmente imposible que se filtre en una respuesta.

**Hallazgo de plataforma al manejar el timeout del container:** `container.wait(timeout=...)` del SDK de Docker, al vencer el timeout, tira `requests.exceptions.ReadTimeout` en Linux/Mac (socket Unix) pero `requests.exceptions.ConnectionError` en Windows con Docker Desktop (transporte vía named pipe) — hay que atrapar los dos para que el timeout funcione igual en cualquier plataforma. Verificado empíricamente en ambos casos.

**Trade-off consciente: la red del container queda habilitada por default.** Muchas automatizaciones reales necesitan llamar a una API externa (el ejemplo que venimos usando: un reporte del clima). Deshabilitar la red por default rompería ese caso de uso legítimo. El aislamiento de esta fase se enfoca en filesystem, memoria, CPU y tiempo — no en red. Si en el futuro se necesitara bloquear red para automatizaciones específicas, `network_disabled=True` es un parámetro más a exponer por automatización, no un cambio de arquitectura.

**Un `User` mínimo ya en Fase 1, completado en la Fase 4.** `Automation` necesita un `user_id` con integridad referencial real (foreign key), no un entero suelto sin garantías. Se creó una tabla `users` mínima (`id`, `email`, `created_at`) desde el principio, sin agregar campos de auth que todavía no tenían ningún endpoint que los usara. En la Fase 4 se le agregó `hashed_password` con su propia migración, en 3 pasos (nullable → rellenar filas existentes → restringir a `NOT NULL`) para no romper los usuarios de prueba que ya existían — el patrón correcto para agregar una columna obligatoria a una tabla con filas, sea en desarrollo o en producción.

**Postgres desde el arranque, nunca SQLite.** El proyecto es un ejercicio de mostrar prácticas reales de backend/DevOps, y Postgres es lo que se termina desplegando en Azure (Fase 5). Usar SQLite en dev/test y Postgres en producción esconde diferencias de tipos y comportamiento (enums, timezones, constraints) que después aparecen como bugs sorpresa recién en producción. Los tests corren contra una base Postgres separada (`TEST_DATABASE_URL`), nunca contra la de desarrollo — cada test corre dentro de una transacción que se revierte (`rollback`) al terminar, así quedan aislados entre sí sin importar el orden en que corran.

**Alembic para migraciones, no `Base.metadata.create_all()`.** `create_all()` solo crea tablas que no existen — no sabe alterar una tabla que ya tiene datos (por ejemplo, agregarle una columna nueva a `users` en la Fase 4). Alembic versiona cada cambio de schema como un script con `upgrade()`/`downgrade()`, encadenado al anterior, aplicable de forma repetible en cualquier entorno. El `--autogenerate` compara los modelos contra el estado real de la base y genera el diff, pero cada migración generada se revisa a mano antes de aplicarla — el autogenerate no siempre acierta (por ejemplo, un rename de columna lo ve como "borrar una y crear otra").

**Dependency injection con `Depends(get_db)` + `app.dependency_overrides` en tests.** Cada endpoint recibe su propia sesión de base de datos a través de `Depends(get_db)`, en vez de una sesión global compartida — así requests concurrentes no se pisan. En los tests, `app.dependency_overrides[get_db]` reemplaza esa dependencia por una versión que apunta a la base de test y la envuelve en una transacción descartable, sin tener que tocar ni una línea del código de producción.

**APScheduler en vez de Celery.** Celery necesita infraestructura aparte (un broker como Redis/RabbitMQ, al menos un proceso worker independiente) — la herramienta correcta cuando hay que distribuir trabajo entre varios workers en varias máquinas con colas reales. Para una sola instancia de API con pocas automatizaciones, eso es sobre-ingeniería: APScheduler corre *adentro* del mismo proceso, sin piezas adicionales. El trade-off consciente: si el día de mañana la API corre en más de una instancia en paralelo, cada una tendría su propio scheduler y dispararía el mismo job dos veces — un problema que Celery no tiene. Para el tamaño actual del proyecto, es la elección correcta; con más escala, dejaría de serlo.

**`BackgroundScheduler`, no `AsyncIOScheduler`.** Toda la app es síncrona (routers con `def`, no `async def`; `subprocess.run` bloqueante). `BackgroundScheduler` corre cada job en un hilo de un pool, desacoplado del event loop — consistente con el resto del código. `AsyncIOScheduler` asume que la app corre sobre asyncio; usarlo con código sync bloqueante arriesga trabar el event loop entero.

**Job store en memoria, no `SQLAlchemyJobStore`.** La fuente de verdad de qué debería estar programado es la tabla `automations` (con su `cron_expression` e `is_active`), no un almacén separado de APScheduler. En cada arranque de la API, el schedule completo se reconstruye leyendo las automatizaciones activas (`load_scheduled_automations`) — más simple que mantener una tabla `apscheduler_jobs` sincronizada aparte, y evita que las dos fuentes de verdad se desincronicen entre sí (por ejemplo, si alguien edita el cron directo en la base).

**Hallazgo de APScheduler que vale la pena documentar:** `scheduler.add_job(..., replace_existing=True)` no deduplica si se llama *antes* de `scheduler.start()` — antes de arrancar, los jobs quedan en una lista interna (`_pending_jobs`) que no aplica `replace_existing` entre sí misma, y al arrancar, duplicados sobreviven todos. Por eso en el `lifespan` de `main.py` el scheduler arranca *antes* de cargar los jobs, no después.

**El scheduler usa la base real, no `get_db()`.** El job que dispara un cron corre en un hilo aparte, con su propia sesión (`SessionLocal` de `app.database` directo) — no pasa por la inyección de dependencias de FastAPI, así que `app.dependency_overrides` no lo alcanza en los tests. Por eso el test que verifica un cron disparando solo (`tests/test_scheduler_integration.py`) no usa las fixtures `client`/`db_session` de siempre: usa la base real configurada y limpia a mano lo que crea, en vez de depender del rollback automático del resto de la suite.

**`bcrypt` en vez de `passlib`.** `passlib` es una capa de abstracción más amplia (soporta decenas de esquemas de hashing) que hace tiempo no recibe mantenimiento activo. `bcrypt` a secas es la librería específica para este único propósito, simple y probada — no hace falta la abstracción extra para un proyecto que solo necesita un esquema. Un detalle a tener en cuenta: bcrypt ignora cualquier byte de la contraseña después del byte 72, así que `UserCreate` valida `max_length=72` en el schema — un 422 claro es mejor que un hash que en secreto solo considera el primer pedazo de la contraseña.

**`PyJWT` directo, con una clave separada de la de los secretos.** `JWT_SECRET_KEY` (para firmar tokens) es una clave distinta de `SECRET_KEY` (la Fernet que encripta los secretos de las automatizaciones, Fase 3) — son dos usos criptográficos distintos (firma/verificación vs. encriptación simétrica), y nunca conviene reusar una misma clave para propósitos distintos: si una se filtra, la otra sigue segura.

**`OAuth2PasswordBearer` + `OAuth2PasswordRequestForm`, el patrón estándar de FastAPI.** `POST /auth/login` recibe `username`/`password` como form data (no JSON) — es el flujo "password" de OAuth2 que FastAPI espera para integrarse con el botón "Authorize" de `/docs`: pegás el token ahí una vez y Swagger UI lo manda solo en cada request de prueba siguiente. En este proyecto "username" es el email, no hay un campo de username separado.

**404, no 403, cuando un usuario pide un recurso de otro.** `_get_owned_automation_or_404` filtra por `id` *y* por `user_id` en la misma query, y devuelve 404 tanto si el id no existe como si existe pero es de otro usuario. Un 403 ("no tenés permiso") confirmaría que el id existe, aunque sea de otro — información que no le corresponde a quien pregunta. Con 404 en los dos casos, no hay forma de distinguir "no existe" de "no es tuyo".

**El `user_id` nunca lo manda el cliente.** En las fases anteriores, `AutomationCreate` pedía `user_id` en el body — cualquiera podía crear una automatización a nombre de otro con solo cambiar un número. Ahora `user_id` sale siempre de `current_user.id` (resuelto del JWT), y el schema ni siquiera tiene ese campo.

**Las notificaciones se disparan desde `executor.py`, no desde el router.** Si el trigger estuviera en el router (en el endpoint `POST /run`), una ejecución disparada por el scheduler (un cron, no un request HTTP) nunca notificaría — habría que duplicar la lógica en dos lugares. Como ya quedó establecido desde la Fase 1 que `run_automation_script` es el único punto de entrada para "ejecutar y persistir el resultado" (lo llaman tanto el router como el scheduler), ahí es el único lugar donde hace falta agregar la notificación.

**El webhook de notificaciones es opt-in, no un requisito.** Si `NOTIFICATION_WEBHOOK_URL` no está configurada, `notify_execution_failed` no hace nada — ni rompe el arranque de la app ni la ejecución. Y si el webhook configurado falla (caído, timeout), se loguea y se sigue: una notificación que no llegó no debería hacer que la ejecución en sí se reporte como un error distinto al que realmente pasó.

**Los secretos se redactan del log antes de mandarlo al webhook.** El `output_log` puede contener un secreto si el script lo imprimió (es el patrón que esta misma documentación sugiere para probar que un secreto llegó bien al container). Antes de armar el mensaje para Slack, se reemplaza cualquier aparición literal del *valor* de cada secreto por un placeholder. No es perfecto — si el script lo transforma antes de imprimirlo (base64, por ejemplo) no lo detecta — pero cubre el caso común sin tener que parsear o interpretar el log.

**Trade-off consciente: la notificación es síncrona, puede agregar hasta 5s a una corrida manual fallida.** `POST /automations/{id}/run` espera a que `requests.post(...)` al webhook responda (o haga timeout) antes de devolver la respuesta HTTP. La alternativa — dispararla en un hilo de fondo, fire-and-forget — evita ese bloqueo, pero le mete no-determinismo a los tests (verificar "se llamó al webhook" deja de ser instantáneo) a cambio de resolver un caso de borde: solo importa cuando la corrida es manual, falló, y el webhook está lento o caído. Mismo criterio que ya se aplicó con el scheduler (sin Celery): simplicidad por sobre la pieza de infraestructura que resolvería el caso de borde.

**Login corre el hash de verificación aunque el email no exista.** `bcrypt.checkpw` es deliberadamente lento (ese es el punto de bcrypt). Si solo se llamara cuando el usuario existe, un email no registrado respondería mucho más rápido que uno registrado con la contraseña incorrecta — una diferencia de tiempo medible que le permite a quien ataca enumerar qué emails están registrados, aunque el cuerpo de la respuesta sea siempre el mismo 401 genérico. Por eso se compara igual contra un hash fijo (`_DUMMY_HASH`) cuando el email no existe: el costo de bcrypt se paga en los dos casos.

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

Generá también una `JWT_SECRET_KEY` **distinta** (nunca la misma que `SECRET_KEY` — son dos usos criptográficos diferentes):

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

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
# Registrarse
curl -X POST http://localhost:8000/auth/register `
  -H "Content-Type: application/json" `
  -d '{\"email\": \"demo@example.com\", \"password\": \"password123\"}'

# Login (OAuth2 "password flow": form data, no JSON -- "username" es el email)
curl -X POST http://localhost:8000/auth/login `
  -d "username=demo@example.com&password=password123"
# -> devuelve {"access_token": "...", "token_type": "bearer"}. Guardalo:
$TOKEN = "el-access-token-de-la-respuesta-anterior"

# Crear una automatización (ya no se manda user_id -- sale del token)
curl -X POST http://localhost:8000/automations `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{\"name\": \"Saludo\", \"script_code\": \"print(1+1)\", \"cron_expression\": \"*/5 * * * *\"}'

# Ejecutarla manualmente (reemplazar 1 por el id devuelto arriba)
curl -X POST http://localhost:8000/automations/1/run -H "Authorization: Bearer $TOKEN"

# Ver el historial de ejecuciones
curl http://localhost:8000/automations/1/executions -H "Authorization: Bearer $TOKEN"

# Una automatización con un secreto, que lo lee como env var
curl -X POST http://localhost:8000/automations `
  -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{\"name\": \"Con secreto\", \"cron_expression\": \"*/5 * * * *\", \"secrets\": {\"API_KEY\": \"abc123\"}, \"script_code\": \"import os; print(os.environ[\\\"API_KEY\\\"])\"}'
```

O directamente desde `http://localhost:8000/docs`: registrate y logueate con "Try it out" en `/auth/login`, copiá el `access_token`, y pegalo en el botón **Authorize** (arriba a la derecha) — Swagger UI lo manda solo en cada request siguiente, sin tener que copiarlo a mano en cada endpoint.

## Modelo de datos

**User**: `id`, `email` (único), `hashed_password`, `created_at`.

**Automation**: `id`, `user_id` (FK a `users`), `name`, `script_code` (texto), `cron_expression`, `is_active`, `secrets_encrypted` (binario, nunca expuesto por la API), `created_at`, `updated_at`.

**Execution**: `id`, `automation_id` (FK a `automations`), `status` (`pending` / `running` / `success` / `failed`), `output_log`, `started_at`, `finished_at`.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/auth/register` | Crea un usuario |
| `POST` | `/auth/login` | Devuelve un JWT (form data: `username`=email, `password`) |
| `POST` | `/automations` | Crea una automatización para el usuario autenticado — todos los de abajo requieren token |
| `GET` | `/automations` | Lista solo las del usuario autenticado |
| `GET` | `/automations/{id}` | Obtiene una puntual (404 si no existe o no es tuya) |
| `PATCH` | `/automations/{id}` | Actualiza parcialmente (ídem) |
| `DELETE` | `/automations/{id}` | Borra (ídem) |
| `POST` | `/automations/{id}/run` | Corre el script ahora mismo (síncrono) y devuelve la `Execution` resultante |
| `GET` | `/automations/{id}/executions` | Historial de ejecuciones de una automatización |
| `GET` | `/automations/{id}/executions/{execution_id}` | Una ejecución puntual |
| `GET` | `/health` | Health check |
