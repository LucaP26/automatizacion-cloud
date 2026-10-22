#!/bin/sh
# Entrypoint de la imagen de la app. Hace como root las dos cosas que lo
# necesitan y después corre el comando real como el usuario sin privilegios
# `app`:
#
# 1. Crea el directorio compartido de ejecuciones (EXECUTION_WORKDIR) y se
#    lo da a `app`. Docker lo crea como root:root si no existe en el host
#    al montarlo, y `app` no podría escribir los scripts ahí.
# 2. Le da a `app` el grupo dueño del socket de Docker. Ese GID depende del
#    host (lo asigna el paquete de Docker al instalarse), así que no se
#    puede fijar en la imagen: se lee del socket montado en runtime.
set -e

if [ "$(id -u)" = "0" ]; then
    if [ -n "$EXECUTION_WORKDIR" ]; then
        mkdir -p "$EXECUTION_WORKDIR"
        chown app:app "$EXECUTION_WORKDIR"
    fi

    if [ -S /var/run/docker.sock ]; then
        exec setpriv --reuid=app --regid=app --groups="$(stat -c %g /var/run/docker.sock)" "$@"
    fi
    exec setpriv --reuid=app --regid=app --clear-groups "$@"
fi

exec "$@"
