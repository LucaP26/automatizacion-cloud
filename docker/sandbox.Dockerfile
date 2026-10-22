# Imagen minima donde corre el codigo arbitrario de las automatizaciones.
# No se le copia nada del proyecto -- el script de cada ejecucion se monta
# como volumen de solo lectura en runtime (ver app/services/executor.py).
FROM python:3.12-slim

# Corre como usuario sin privilegios, nunca como root: aunque el
# aislamiento principal lo da el container en si, un proceso que
# escapara del sandbox quedaria igual sin permisos de root adentro.
RUN useradd --no-create-home --uid 10001 sandbox
USER sandbox

# -u: salida sin buffer, para no perder stdout si el container se mata
# por timeout antes de que el proceso flushee.
ENTRYPOINT ["python", "-u"]
