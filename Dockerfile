# Imagen de la aplicación (API, worker y migraciones usan la misma imagen,
# cambia solo el comando -- ver docker-compose.yml). No confundir con
# docker/sandbox.Dockerfile, que es donde corre el código de los usuarios.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --no-create-home --uid 10002 app

WORKDIR /app

# requirements.txt se copia y se instala ANTES que el código: así la capa
# de dependencias (la lenta) queda cacheada y un cambio en app/ no obliga a
# reinstalar todo. Solo runtime: pytest no entra en la imagen.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# La imagen arranca como root a propósito: el entrypoint necesita root para
# preparar el directorio compartido de ejecuciones y averiguar el grupo del
# socket de Docker, y después baja a `app` antes de correr la app.
ENTRYPOINT ["entrypoint.sh"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
