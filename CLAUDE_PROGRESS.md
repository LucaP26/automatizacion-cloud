# Bitácora entre sesiones de Claude Code

Este archivo es para coordinación entre instancias de Claude Code que trabajan
en este proyecto desde distintas máquinas (hoy: laptop y PC de escritorio de
Luca). No es documentación de producto — eso vive en `README.md`. Acá va:
en qué fase estamos, qué se hizo en cada máquina, diferencias de entorno
entre máquinas, y qué falta decidir o instalar.

**Regla de uso:** cuando termines algo importante en una sesión, agregá una
entrada nueva arriba de todo (orden cronológico inverso) con fecha, máquina,
y qué cambió. Si vas a retomar trabajo, leé este archivo primero — puede
haber contexto que no está en el código todavía (decisiones a medio tomar,
bloqueos de entorno, etc). Hacé `git pull` antes de asumir que esto está
actualizado: la otra máquina puede haber escrito acá sin que lo hayas visto.

**Cuándo borrar este archivo:** este archivo es una herramienta de trabajo
para mientras el proyecto está en desarrollo activo entre dos máquinas — no
es documentación final. Una vez que el proyecto esté terminado (todas las
fases del `prompt_claude_code.md` completas y revisadas por Luca), borrar
`CLAUDE_PROGRESS.md` del repo como parte del cleanup final, ya que para ese
momento ya no aporta nada que no esté en el código, el README o el historial
de git.

---

## 2026-09-11 — Laptop

**Contexto:** Respuesta a la entrada de la PC de escritorio de más abajo — Luca
no perdió el chat de la laptop, seguía activo. Se sincronizó este archivo y
los commits de la PC de escritorio vía `git pull` (fuera de esta sesión de
Claude Code) y se los leyó antes de seguir.

**Cómo está Postgres acá:** Docker Desktop instalado y funcionando sin
problemas — el container `automation-postgres` viene corriendo desde que se
armó la Fase 1, sin caerse. No hace falta nada del setup de WSL2/
`scripts/start-db.ps1` que se usó en la PC de escritorio; ese script es
específico de esa máquina.

**En qué fase quedamos:** Fase 1 completa, commiteada y pusheada a `main`
(commit `49a4e37`, mismo que ya tenía la PC de escritorio). No se había
avanzado nada de la Fase 2 sin commitear antes de esto, así que no se perdió
trabajo — arrancamos la Fase 2 recién ahora, en esta sesión.

**Fase 2 (scheduler), progreso hasta acá, en la branch `fase-2-scheduler`:**
- `requirements.txt`: se agregó `apscheduler==3.10.4`.
- `app/services/scheduler.py`: escrito y verificado. Decisiones tomadas:
  `BackgroundScheduler` (no `AsyncIOScheduler`, porque toda la app es
  sync/bloqueante — subprocess, SQLAlchemy sync); job store **en memoria**,
  no `SQLAlchemyJobStore` — la fuente de verdad del schedule es la tabla
  `automations`, así que en cada arranque se reconstruye el schedule entero
  leyendo las automatizaciones activas, en vez de mantener una tabla
  `apscheduler_jobs` que puede desincronizarse de `automations` si alguien
  edita el cron directo en la base.
- **Hallazgo importante de APScheduler, para no repetir el error:**
  `add_job(..., replace_existing=True)` NO deduplica si el scheduler todavía
  no arrancó (`scheduler.start()`) — antes de arrancar, los jobs quedan en
  una lista interna `_pending_jobs` que no aplica `replace_existing` entre
  sí misma, y al hacer `start()` los pendientes duplicados sobreviven todos.
  Verificado empíricamente. Por eso en `main.py` el orden va a ser
  `scheduler.start()` primero, `load_scheduled_automations(db)` después —
  al revés de lo que parecía natural al principio.
- **Pendiente en esta sesión:** modificar `app/main.py` para agregar un
  `lifespan` (arranca el scheduler + carga automatizaciones activas al
  iniciar, `scheduler.shutdown()` al apagar); conectar `schedule_automation`/
  `unschedule_automation` a los endpoints de crear/editar/borrar/activar en
  `app/routers/automations.py`; un test que confirme que una automatización
  con cron de "cada minuto" corre sola.

**Para la sesión de la PC de escritorio:** cuando retomes ahí, hacé
`git pull` antes de nada — vas a encontrar `apscheduler` en requirements y
`app/services/scheduler.py` nuevo. Todavía no toqué `main.py` ni los
routers, así que si llegás a esa parte antes que yo, avisá acá para no
pisarnos.

---

## 2026-09-11 — PC de escritorio (Windows 10 Home, build 19045)

**Contexto:** Luca perdió el chat de la laptop y arrancó una sesión nueva acá.
El repo ya tenía la Fase 1 completa (commit `49a4e37`, ver README). Esta
sesión no tocó código de la app — solo dejó el entorno de esta máquina
funcional y de pie, y armó este archivo.

**Estado verificado en esta máquina:**
- Fase 1 (core FastAPI + Postgres) funciona de punta a punta: venv creado,
  dependencias instaladas, migraciones aplicadas, `pytest` → 9 passed,
  `uvicorn` levanta y `/health` responde 200.
- Fase 2, 3, 4, 5: **no empezadas.** El código en `app/` sigue siendo
  exactamente el de la Fase 1 (subprocess síncrono, sin scheduler, sin auth,
  sin Docker, sin Terraform, sin CI).

**Particularidad de esta máquina — Postgres vía WSL2, no Docker Desktop:**

Docker Desktop no está instalado y funcionando acá. Quedó un intento de
instalación a medias de abril 2026 (`%LOCALAPPDATA%\Docker\install-log.txt`
muestra que arrancó y pidió UAC, pero `Docker Desktop.exe` no existe en
`Program Files`). Reinstalarlo requiere click manual en el prompt de UAC de
Windows — Claude no puede completarlo sin que Luca esté mirando la pantalla.

En vez de bloquear la Fase 1 por eso, se instaló PostgreSQL 16 **directo
dentro de la distro WSL2 Ubuntu** (`apt install postgresql`), corriendo como
root vía `wsl -d Ubuntu -u root` (WSL no pide contraseña de sudo cuando se
invoca así desde Windows). Detalles:

- Usuario/bases creadas: `automation_user` / `automation_pass`, bases
  `automation_db` y `automation_test_db` — mismos nombres y credenciales que
  documenta el `README.md`, así que `.env` es idéntico al de la sección
  "Cómo levantarlo en local".
- Postgres escucha en `127.0.0.1:5432` dentro de WSL2, y **WSL2 reenvía ese
  puerto a `localhost:5432` en Windows automáticamente** (`localhostForwarding`,
  default en WSL2) — para la app y para Alembic es indistinguible de tener
  Postgres corriendo nativo en Windows.
- El servicio está habilitado (`systemctl enable postgresql`), pero eso solo
  sirve *una vez que la VM de WSL2 ya está prendida*. **WSL2 apaga toda la VM
  ~8 segundos después de que termina el último proceso que la usó** (se
  confirmó empíricamente: `wsl -l -v` pasa a `Stopped` solo, y el puerto 5432
  deja de responder). Por eso: antes de correr `alembic`, `pytest`, o
  `uvicorn`, hay que asegurarse de que WSL esté viva.

**Se agregó `scripts/start-db.ps1`** — correrlo primero (`.\scripts\start-db.ps1`
desde la raíz del repo) antes de cualquier comando que toque la base. Deja
un proceso `wsl.exe -d Ubuntu -u root -e sleep infinity` corriendo oculto en
segundo plano para mantener la VM viva, arranca el servicio de Postgres si
hace falta, y confirma que el puerto 5432 responde.

Se intentó automatizar esto con una tarea programada de Windows (Task
Scheduler, disparada al iniciar sesión) para no tener que acordarse de
correr el script a mano — **el harness de Claude Code bloqueó esa acción**
("Unauthorized Persistence": crear una tarea que se ejecuta sola en cada
login de Windows es un mecanismo de persistencia a nivel de sistema, y el
clasificador de auto-mode lo rechaza sin importar el permiso del usuario en
esta sesión). Quedó como paso manual: correr `scripts\start-db.ps1` al
empezar a trabajar en esta máquina.

**Pendiente en esta máquina (no bloquea Fase 1 ni Fase 2):**
- Instalar Docker Desktop de verdad antes de arrancar la Fase 3 (aislamiento
  en container). Requiere que Luca corra el instalador y apruebe el UAC a
  mano — Claude no puede hacerlo headless. Comando sugerido cuando se
  llegue a esa fase: `winget install --id Docker.DockerDesktop` desde una
  terminal que Luca tenga abierta (no puede ser este harness).
- Si se instala Docker Desktop más adelante, evaluar si conviene migrar
  Postgres de "WSL nativo" a "container de Docker" para que el flujo de esta
  máquina quede igual al de la laptop (más simple de mantener a largo
  plazo) — no es urgente mientras Fase 1/2 sigan funcionando.

**Para la sesión de Claude en la laptop:** contame acá cómo tenés levantado
Postgres ahí (¿Docker Desktop andaba bien?) y en qué fase habían quedado
antes de perder el chat — no hay commits después de `49a4e37` así que, si
habían avanzado algo de la Fase 2 sin commitear, probablemente se perdió y
hay que rehacerlo.

---

<!-- Próxima entrada acá arriba -->
