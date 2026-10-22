# Automation Manager

Plataforma web para registrar scripts de Python, programarlos con cron, ejecutarlos y ver el resultado de cada corrida. Proyecto de portfolio enfocado en backend + DevOps: arquitectura de API prolija, containerización con propósito y CI/CD real.

## Estado actual: Fase 5 — Infraestructura (proyecto completo)

- CRUD de automatizaciones (`Automation`), **scopeado por usuario**: cada uno ve, edita, borra y corre solo las suyas.
- Las automatizaciones activas corren solas según su cron (scheduler con APScheduler), o manualmente vía `POST /automations/{id}/run`.
- Historial de ejecuciones (`Execution`) con status, logs y timestamps.
- Cada ejecución corre dentro de un container Docker descartable, con timeout, límites de memoria/CPU, y filesystem de solo lectura.
- Secretos (variables de entorno) por automatización, encriptados en la base, inyectados al container solo en el momento de ejecutar.
- **Autenticación con JWT**: `POST /auth/register` y `POST /auth/login`. Todos los endpoints de `/automations` requieren un token válido.
- **Notificación por webhook** (p.ej. Slack) cuando una ejecución termina en `failed` — opcional, no-op si no está configurada.
- **Todo el sistema en Docker Compose**: Postgres, migraciones, API y un **worker separado** que corre el scheduler. Un `docker compose up -d --build` levanta todo.
- **Despliegue en Azure con Terraform**: una VM Linux que se bootstrapea sola con cloud-init y levanta el mismo `docker-compose.yml`. `terraform apply` / `terraform destroy` crean y borran todo.
- **CI con GitHub Actions** en cada push: pytest + migraciones sobre base vacía, build de las imágenes + smoke test end-to-end del stack de compose, y `terraform fmt`/`validate`.

## Arquitectura

```
app/
  main.py           # arma la app FastAPI y registra routers
  worker.py         # proceso aparte que corre el scheduler (python -m app.worker)
  config.py         # settings desde variables de entorno (pydantic-settings)
  database.py       # engine/session de SQLAlchemy, Base declarativa, get_db()
  models/           # entidades ORM (User, Automation, Execution)
  schemas/          # contratos Pydantic de entrada/salida (independientes del ORM)
  routers/          # capa HTTP: valida con schemas, delega en services
  services/         # lógica de negocio: ejecución de scripts (executor.py) y scheduling (scheduler.py)
alembic/            # migraciones de base de datos
tests/              # pytest, contra una base de datos Postgres separada
Dockerfile          # imagen de la app (API, worker y migraciones: misma imagen, distinto comando)
docker/             # sandbox.Dockerfile (donde corre el código de usuarios) + entrypoint.sh
docker-compose.yml  # stack completo: db, migrate, api, worker
infra/terraform/    # VM en Azure + red + cloud-init
.github/workflows/  # CI
scripts/            # smoke-test.sh (end-to-end contra un stack levantado)
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

**Hallazgo de APScheduler que vale la pena documentar:** `scheduler.add_job(..., replace_existing=True)` no deduplica si se llama *antes* de `scheduler.start()` — antes de arrancar, los jobs quedan en una lista interna (`_pending_jobs`) que no aplica `replace_existing` entre sí misma, y al arrancar, duplicados sobreviven todos. Por eso el scheduler arranca *antes* de cargar los jobs, no después (hoy en `app/worker.py`; hasta la Fase 4, en el `lifespan` de `main.py`).

**El scheduler usa la base real, no `get_db()`.** El job que dispara un cron corre en un hilo aparte, con su propia sesión (`SessionLocal` de `app.database` directo) — no pasa por la inyección de dependencias de FastAPI, así que `app.dependency_overrides` no lo alcanza en los tests. Por eso el test que verifica un cron disparando solo (`tests/test_scheduler_integration.py`) no usa las fixtures `client`/`db_session` de siempre: usa la base real configurada y limpia a mano lo que crea, en vez de depender del rollback automático del resto de la suite.

**`bcrypt` en vez de `passlib`.** `passlib` es una capa de abstracción más amplia (soporta decenas de esquemas de hashing) que hace tiempo no recibe mantenimiento activo. `bcrypt` a secas es la librería específica para este único propósito, simple y probada — no hace falta la abstracción extra para un proyecto que solo necesita un esquema. Un detalle a tener en cuenta: bcrypt ignora cualquier byte de la contraseña después del byte 72, así que `UserCreate` valida `max_length=72` en el schema — un 422 claro es mejor que un hash que en secreto solo considera el primer pedazo de la contraseña.

**`PyJWT` directo, con una clave separada de la de los secretos.** `JWT_SECRET_KEY` (para firmar tokens) es una clave distinta de `SECRET_KEY` (la Fernet que encripta los secretos de las automatizaciones, Fase 3) — son dos usos criptográficos distintos (firma/verificación vs. encriptación simétrica), y nunca conviene reusar una misma clave para propósitos distintos: si una se filtra, la otra sigue segura.

**`OAuth2PasswordBearer` + `OAuth2PasswordRequestForm`, el patrón estándar de FastAPI.** `POST /auth/login` recibe `username`/`password` como form data (no JSON) — es el flujo "password" de OAuth2 que FastAPI espera para integrarse con el botón "Authorize" de `/docs`: pegás el token ahí una vez y Swagger UI lo manda solo en cada request de prueba siguiente. En este proyecto "username" es el email, no hay un campo de username separado.

**404, no 403, cuando un usuario pide un recurso de otro.** `_get_owned_automation_or_404` filtra por `id` *y* por `user_id` en la misma query, y devuelve 404 tanto si el id no existe como si existe pero es de otro usuario. Un 403 ("no tenés permiso") confirmaría que el id existe, aunque sea de otro — información que no le corresponde a quien pregunta. Con 404 en los dos casos, no hay forma de distinguir "no existe" de "no es tuyo".

**El `user_id` nunca lo manda el cliente.** En las fases anteriores, `AutomationCreate` pedía `user_id` en el body — cualquiera podía crear una automatización a nombre de otro con solo cambiar un número. Ahora `user_id` sale siempre de `current_user.id` (resuelto del JWT), y el schema ni siquiera tiene ese campo.

**Las notificaciones se disparan desde `executor.py`, no desde el router.** Si el trigger estuviera en el router (en el endpoint `POST /run`), una ejecución disparada por el scheduler (un cron, no un request HTTP) nunca notificaría — habría que duplicar la lógica en dos lugares. Como ya quedó establecido desde la Fase 1 que `run_automation_script` es el único punto de entrada para "ejecutar y persistir el resultado" (lo llaman tanto el router como el scheduler), ahí es el único lugar donde hace falta agregar la notificación.

**El webhook de notificaciones es opt-in, no un requisito.** Si `NOTIFICATION_WEBHOOK_URL` no está configurada, `notify_execution_failed` no hace nada — ni rompe el arranque de la app ni la ejecución. Y si el webhook configurado falla (caído, timeout), se loguea y se sigue: una notificación que no llegó no debería hacer que la ejecución en sí se reporte como un error distinto al que realmente pasó.

**Los secretos se redactan del log antes de mandarlo al webhook, y se desencriptan una sola vez.** El `output_log` puede contener un secreto si el script lo imprimió (es el patrón que esta misma documentación sugiere para probar que un secreto llegó bien al container). Antes de armar el mensaje para Slack, se reemplaza cualquier aparición literal del *valor* de cada secreto por un placeholder. No es perfecto — si el script lo transforma antes de imprimirlo (base64, por ejemplo) no lo detecta — pero cubre el caso común sin tener que parsear o interpretar el log. `run_automation_script` desencripta los secretos una sola vez, dentro de la misma red de seguridad que protege toda la ejecución, y reusa ese mismo dict tanto para inyectarlo al container como para la notificación — no dos desencriptaciones separadas. Esto evita tres problemas que convivían en la primera versión: (1) la segunda desencriptación podía explotar *fuera* de esa red de seguridad y convertir un fallo ya persistido correctamente en un 500 sin manejar; (2) era trabajo duplicado; (3) si alguien editaba los secretos de la automatización mientras el container seguía corriendo, una segunda lectura tardía podía traer el valor *nuevo* de la fila, y la redacción fallaría en tapar el secreto *viejo* que de verdad quedó impreso en el log.

**Límites conocidos, documentados en vez de resueltos en esta fase.** `/auth/login` no tiene rate limiting ni lockout — nada impide un ataque de fuerza bruta contra una cuenta puntual (sí se cierra el timing side-channel que permite *enumerar* emails, pero no el intento ilimitado de contraseñas). Agregar eso de forma real necesitaría un limiter con estado compartido entre requests (Redis, típicamente) — infraestructura nueva, fuera del alcance de esta fase. De forma similar, `verify_password` trata *cualquier* hash inválido como "no coincide" (ver más abajo) en vez de distinguir "contraseña nunca configurada" de "dato corrupto" — para una cuenta real eso ameritaría un modelo de datos explícito (`password_set: bool`, o un flujo de reseteo forzado), no solo atrapar la excepción.

**Trade-off consciente: la notificación es síncrona, puede agregar hasta 5s a una corrida manual fallida.** `POST /automations/{id}/run` espera a que `requests.post(...)` al webhook responda (o haga timeout) antes de devolver la respuesta HTTP. La alternativa — dispararla en un hilo de fondo, fire-and-forget — evita ese bloqueo, pero le mete no-determinismo a los tests (verificar "se llamó al webhook" deja de ser instantáneo) a cambio de resolver un caso de borde: solo importa cuando la corrida es manual, falló, y el webhook está lento o caído. Mismo criterio que ya se aplicó con el scheduler (sin Celery): simplicidad por sobre la pieza de infraestructura que resolvería el caso de borde.

**Login corre el hash de verificación aunque el email no exista.** `bcrypt.checkpw` es deliberadamente lento (ese es el punto de bcrypt). Si solo se llamara cuando el usuario existe, un email no registrado respondería mucho más rápido que uno registrado con la contraseña incorrecta — una diferencia de tiempo medible que le permite a quien ataca enumerar qué emails están registrados, aunque el cuerpo de la respuesta sea siempre el mismo 401 genérico. Por eso se compara igual contra un hash fijo (`_DUMMY_HASH`) cuando el email no existe: el costo de bcrypt se paga en los dos casos.

### Decisiones de la Fase 5 (infraestructura)

**El scheduler sale de la API a un worker propio.** Hasta la Fase 4, APScheduler corría adentro del proceso de la API. Eso ata la API a una sola réplica: con `uvicorn --workers 2` (o dos containers de la API) habría dos schedulers y cada cron dispararía dos veces, que era el trade-off ya documentado al elegir APScheduler sobre Celery. Ahora el scheduler vive en `app/worker.py`, un proceso aparte con la misma imagen y otro comando. La API puede escalar; el worker es la única instancia que agenda.

**El worker se entera de los cambios releyendo la base, no por mensajes.** Al separar los procesos, la API ya no puede llamar a `schedule_automation()` en la memoria del worker. Las alternativas eran un canal de mensajes (Redis pub/sub, o `LISTEN/NOTIFY` de Postgres) o reconciliar periódicamente. Se eligió lo segundo: cada `SCHEDULER_SYNC_INTERVAL_SECONDS` (30 por default) el worker lee las automatizaciones activas y hace un diff contra lo que tiene agendado. Agrega las nuevas, reagenda las que cambiaron de cron y saca las borradas o desactivadas. La tabla `automations` ya era la fuente de verdad del schedule desde la Fase 2, así que esto no agrega estado nuevo, y el worker se recupera solo de cualquier desincronización (una caída de la base, un cambio hecho a mano en SQL). El costo son hasta 30s de demora entre editar una automatización y que rija el cron nuevo. `LISTEN/NOTIFY` bajaría esa demora a casi cero, pero necesita una conexión dedicada escuchando y reconexión ante caídas. Y *igual* haría falta el sync completo al arrancar, porque los NOTIFY que llegan con el worker caído se pierden.

**`POST /run` sigue ejecutando en la API.** La ejecución manual es síncrona: el cliente espera la `Execution` en la respuesta. Mandarla al worker obligaría a agregar una cola y un endpoint de polling, un cambio de contrato de la API. Por eso **los dos** containers (API y worker) lanzan containers sandbox.

**Docker-outside-of-Docker: el socket del host, no Docker-in-Docker.** API y worker montan `/var/run/docker.sock` y crean los containers sandbox como *hermanos* suyos en el daemon del host. Docker-in-Docker (un daemon corriendo adentro de otro container) necesita `--privileged`, que es peor, y duplica el cache de imágenes. Hay que ser honesto con lo que implica: **acceso al socket de Docker equivale a root en el host**. Si alguien compromete la API, puede lanzar un container que monte `/`. Es aceptable acá porque la API ya es el componente de confianza que decide qué se ejecuta. La mitigación real sería un proxy del socket que solo permita las llamadas que usa el executor (p.ej. `tecnativa/docker-socket-proxy`), o mover la ejecución a otro host.

**El bug que trae Docker-outside-of-Docker, y cómo se resolvió.** El executor escribe el script en un tempdir y lo monta en el sandbox (`volumes={tmp_dir: ...}`). Pero ese bind mount lo resuelve el **daemon del host**, que busca el path en *su* filesystem y no en el del container de la API, que es donde el tempdir existe de verdad. Con la API corriendo en compose, el sandbox habría montado un directorio vacío. La solución es `EXECUTION_WORKDIR`: compose monta `/tmp/automation-runs` **con el mismo path** en el host y en los containers de la API y del worker, y el executor crea sus tempdirs ahí adentro. Así, el path que ve la API es el mismo que ve el daemon. Con la API corriendo directo en el host (desarrollo), la variable queda vacía y se usa el temp del sistema, como antes. El smoke test del CI ejercita justamente este camino.

**La imagen arranca como root y baja privilegios en el entrypoint.** `docker/entrypoint.sh` hace como root dos cosas que lo necesitan y después corre la app como el usuario `app` (UID 10002) con `setpriv`. Primero, crea el directorio de ejecuciones y se lo da a `app`, porque Docker lo crea como `root:root` si no existe en el host. Segundo, le da a `app` el grupo dueño del socket de Docker, leyendo el GID del socket montado. Ese GID lo asigna el paquete de Docker al instalarse, así que cambia entre máquinas (en la VM es el grupo `docker`, en Docker Desktop es 0) y no se puede fijar en el `Dockerfile`. Con el socket montado, correr como no-root no es una barrera de seguridad fuerte (ver arriba), pero igual limita lo que puede hacer un bug común en la app, como escribir en el filesystem del container.

**Una imagen, tres roles.** API, worker y migraciones usan la misma imagen (`automation-manager:latest`) con distinto comando. Es un solo build y un solo artefacto, y garantiza que el worker y la API corran exactamente el mismo código (los dos usan `executor.py`). La imagen es single-stage: `psycopg[binary]` y las demás dependencias vienen como wheels precompilados, no hay nada que compilar, así que un stage de build aparte no achicaría nada.

**Migraciones como servicio one-shot (`migrate`), no en el arranque de la API.** Si la API corriera `alembic upgrade head` al arrancar, dos réplicas lo harían a la vez contra la misma base. `migrate` corre una vez por `up`, y API y worker tienen `depends_on: condition: service_completed_successfully` sobre él, así que nunca arrancan contra un schema viejo. Mismo truco para la imagen del sandbox: el servicio `sandbox-image` existe solo para que `docker compose up --build` la construya. Antes era un paso manual del que había que acordarse.

**Postgres sin puerto publicado.** El servicio `db` no tiene `ports`: solo se llega a él desde la red interna de compose. En la VM no queda expuesto a internet ni siquiera por un error en el NSG, y en local no choca con otro Postgres que ya use el 5432.

**`stop_grace_period: 45s` en el worker.** Al apagarse (`docker compose down`, un deploy), el worker espera a que termine la ejecución en curso (`scheduler.shutdown(wait=True)`) para que la `Execution` no quede colgada en `running`. Docker manda SIGKILL a los 10s por default, que es menos que el timeout de una ejecución (30s).

**Azure: una VM con compose, no Container Apps ni App Service.** El executor necesita un daemon de Docker para lanzar sandboxes, y los servicios PaaS de containers de Azure no exponen uno. Container Apps hubiera obligado a reescribir el executor contra Azure Container Instances (otro SDK, credenciales, ~30s de arranque por ejecución) y a pagar más. Con una VM, local y Azure corren el **mismo** `docker-compose.yml`: lo que pasa el smoke test en CI es lo que se despliega. El costo es que el sistema operativo queda a nuestro cargo (parches, reinicios), algo que un PaaS resolvería.

**`Standard_B2ats_v2` en `chilecentral`, no `B1s` en `eastus`.** Azure for Students restringe por policy en qué regiones se puede desplegar (`az policy assignment list`), y `eastus` no está entre ellas. De las permitidas, ninguna ofrece la serie B v1 (`B1s`) para esta suscripción (`az vm list-skus --location <región>`). `B2ats_v2` es su reemplazo en la serie B v2: misma RAM (1 GB), 2 vCPU AMD x86 y soporte de disco Premium. `chilecentral` es la región permitida más cercana. Las dos cosas son variables de Terraform, así que cambiarlas no requiere tocar código.

**Postgres en un container de la VM, no Azure Database for PostgreSQL.** No suma costo y `terraform destroy` se lleva todo. A cambio, no hay backups gestionados ni alta disponibilidad: si se borra la VM, se borran los datos. Para un portfolio es aceptable; con datos reales, lo primero a cambiar sería pasar a Flexible Server.

**Los secretos de la app se generan en la VM, nunca pasan por Terraform.** La primera vez que arranca la VM, cloud-init genera `POSTGRES_PASSWORD`, `SECRET_KEY` y `JWT_SECRET_KEY` con `openssl`/`os.urandom` y los escribe en un `.env` con permisos `600`. Si se pasaran como variables de Terraform, quedarían en texto plano en el `terraform.tfstate`, porque `sensitive = true` solo los oculta del output de la consola, no del state. Si el `.env` ya existe, no se regenera: rotar `SECRET_KEY` dejaría ilegibles los secretos que ya están encriptados en la base.

**State de Terraform local, no remoto.** Para un equipo, lo correcto es un backend remoto (un Storage Account en Azure) que comparta el state y lo lockee. Pero ese Storage Account tendría que crearse antes y por fuera de este Terraform (el problema del huevo y la gallina), y `terraform destroy` ya no dejaría la suscripción vacía. Para una persona sola con Azure for Students, el state local es más simple, y `*.tfstate` está en `.gitignore`. El `.terraform.lock.hcl` sí se commitea: fija las versiones exactas de los providers, igual que `requirements.txt`.

**Red mínima: SSH y la API, solo desde tu IP.** El NSG abre solo el 22 y el 8000, y solo para `allowed_source_cidr` (una validación de Terraform rechaza `0.0.0.0/0`). La API va por HTTP sin TLS, así que exponerla a todo internet mandaría contraseñas y tokens en texto plano. El próximo paso para abrirla al público sería un reverse proxy con HTTPS: Caddy en el compose, más un `domain_name_label` en la IP pública para tener un hostname.

**CI en tres jobs que se complementan.** `tests` corre pytest con un Postgres como service container y además aplica las migraciones de Alembic sobre una base vacía. Como los tests usan `create_all()`, sin ese paso una migración rota pasaría el CI. `e2e` construye las imágenes, levanta el compose con el **mismo** `.env` mínimo que genera cloud-init en la VM y corre `scripts/smoke-test.sh`: registro → login → crear → ejecución manual en la API → ejecución por cron en el worker. Es el único test que prueba la app *dentro* de sus containers (red de compose, socket, directorio compartido, worker separado). `terraform` corre `fmt -check` y `validate` sin credenciales de Azure. El `apply` real queda manual a propósito: un `apply` automático en cada push gastaría crédito de Azure for Students y obligaría a guardar credenciales de Azure en GitHub. En el CI no hay ningún secreto real: las claves se generan descartables en cada corrida.

## Cómo levantarlo en local

### Opción A: todo con Docker Compose (recomendado)

Necesita solo Docker Desktop corriendo.

```powershell
copy .env.example .env   # y completar SECRET_KEY / JWT_SECRET_KEY (ver más abajo cómo generarlas)
docker compose up -d --build
```

Construye las imágenes (app + sandbox), espera a que Postgres esté sano, corre las migraciones y levanta la API (`http://localhost:8000/docs`) y el worker. Para verificar todo el flujo de punta a punta:

```powershell
bash scripts/smoke-test.sh
```

Logs: `docker compose logs -f api worker`. Bajar todo: `docker compose down` (con `-v` también borra los datos de Postgres).

### Opción B: desarrollo con la API y el worker en el host

Útil para `--reload` y para correr `pytest`. Postgres sigue en Docker. Los pasos 1 a 6 de abajo son para esta opción.

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
pip install -r requirements-dev.txt
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

### 6. Levantar la API y el worker

```powershell
uvicorn app.main:app --reload
```

En otra terminal, el worker (sin él, las automatizaciones no corren solas por cron; `POST /run` sí funciona):

```powershell
python -m app.worker
```

La API queda en `http://localhost:8000`, docs interactivas (Swagger, autogeneradas desde los schemas de Pydantic) en `http://localhost:8000/docs`.

## Deploy en Azure (Terraform)

Prerrequisitos: [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.9, [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) y una clave SSH (`ssh-keygen -t ed25519` si no tenés una).

```powershell
az login
cd infra/terraform
copy terraform.tfvars.example terraform.tfvars   # completar subscription_id y allowed_source_cidr
terraform init
terraform plan
terraform apply
```

`terraform apply` crea un resource group con una VNet, un NSG, una IP pública y una VM Ubuntu 24.04 (`Standard_B2ats_v2` en `chilecentral`; ver "Decisiones de la Fase 5" para el porqué). Al arrancar, cloud-init instala Docker, clona este repo, genera los secretos y corre `docker compose up -d --build`. El apply termina en unos 2 minutos, pero la API tarda unos 5 minutos más en quedar arriba, porque la VM está construyendo las imágenes. Para ver el progreso:

```powershell
ssh azureuser@<public_ip>             # o: terraform output ssh_command
cloud-init status --wait              # en la VM; logs en /var/log/cloud-init-output.log
```

Para probarla desde tu máquina:

```powershell
bash scripts/smoke-test.sh $(terraform output -raw api_url)
```

**Bajar todo** (para no dejar la VM prendida gastando crédito):

```powershell
terraform destroy
```

Se lleva el resource group entero, datos incluidos. Para desplegar otra branch, poné `git_ref = "mi-branch"` en `terraform.tfvars`. El bootstrap corre solo en el primer arranque, así que un `apply` sobre una VM que ya existe no redespliega. Para actualizar el código: `ssh` a la VM, después `cd /opt/automation-manager && sudo git pull && sudo docker compose up -d --build`.

## CI

`.github/workflows/ci.yml` corre en cada push y en cada PR con tres jobs: `tests` (pytest + migraciones), `e2e` (build de las imágenes + smoke test del compose) y `terraform` (`fmt` + `validate`). El porqué de cada uno está en "Decisiones de la Fase 5".

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
