# Prompt para Claude Code — Sistema de gestión de automatizaciones

Copiá y pegá esto en Claude Code para arrancar el proyecto.

---

Quiero que me ayudes a construir un sistema de gestión de automatizaciones: una plataforma web donde un usuario puede registrar scripts de Python, programarlos con cron, ejecutarlos (manual o automáticamente) y ver el resultado de cada corrida. Es un proyecto de portfolio para mostrar backend + DevOps completo, así que priorizá buenas prácticas de arquitectura sobre atajos.

## Alcance (MVP primero, después iteramos en capas)

**Fase 1 — Core**
- Backend en FastAPI + PostgreSQL.
- Modelo `Automation`: id, user_id, nombre, código del script (texto), expresión cron, activo/inactivo, fecha de creación.
- Modelo `Execution`: id, automation_id, estado (pending/running/success/failed), log de salida, fecha de inicio, fecha de fin.
- Endpoints CRUD para automatizaciones.
- Endpoint para ejecutar una automatización manualmente: por ahora corré el script en un `subprocess` de Python (sin container todavía), capturá stdout/stderr y guardá el resultado en `Execution`.
- Tests con pytest para los endpoints principales.

**Fase 2 — Scheduler**
- Integrar APScheduler para que las automatizaciones con cron configurado corran solas, no solo manualmente.

**Fase 3 — Aislamiento**
- Mover la ejecución del script a un container Docker separado por seguridad (código arbitrario de usuarios no debería correr suelto en el proceso de la API). Con timeout configurable.
- Variables de entorno / secretos por automatización, guardados encriptados, inyectados al script en runtime — nunca hardcodeados en el código del script.

**Fase 4 — Auth y notificaciones**
- Autenticación con JWT (registro/login).
- Notificación por mail (o webhook a Slack/Teams) cuando una ejecución falla.

**Fase 5 — Infraestructura**
- Dockerfile + docker-compose para levantar todo (API, worker, PostgreSQL) en local.
- Terraform para desplegar en Azure (usaré Azure for Students, así que la infra tiene que poder crearse y destruirse limpiamente con `terraform apply` / `terraform destroy`).
- GitHub Actions: pipeline que corra tests y build de la imagen en cada push.

## Cómo quiero trabajar

- Andá fase por fase. No avances a la siguiente fase hasta que la anterior esté funcionando y yo la haya revisado.
- Al terminar cada fase, mostrame cómo probarla (comandos concretos, curl o pytest).
- Explicame las decisiones de arquitectura importantes a medida que las tomás (por qué subprocess vs container, por qué APScheduler vs Celery, etc.) — quiero poder defenderlas en una entrevista técnica.
- Usá buenas prácticas: variables de entorno para config (nunca credenciales hardcodeadas), migraciones de base de datos (Alembic), estructura de carpetas clara (routers, models, services, tests separados).
- Escribí un README que se vaya actualizando con cada fase: qué hace el proyecto, cómo levantarlo local, arquitectura general.

## Contexto sobre mí

Soy estudiante de Ingeniería Informática, con experiencia en Python, Terraform y Azure de una pasantía en automatización de procesos. Sé Git, Docker a nivel básico, y quiero reforzar específicamente: diseño de APIs REST prolijas, containerización con propósito (no solo `docker-compose up`), y CI/CD real. Preferí explicarme el porqué de las cosas antes de escribir el código, no solo tirarme el resultado final.

Empecemos por la Fase 1: armá la estructura del proyecto y el modelo de datos.
