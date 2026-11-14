# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm

# PIP_NO_CACHE_DIR se omite a propósito: la red del server va muy lenta a PyPI,
# así que pip debe poder usar la caché. Esa caché se persiste entre builds con
# el `--mount=type=cache` del RUN de abajo (BuildKit), de modo que un rebuild no
# vuelve a descargar los wheels aunque se invalide la capa.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

# Este server no tiene salida IPv6 (sin ruta por defecto), pero PyPI/Debian
# resuelven a IPv4 e IPv6 y glibc por defecto prefiere IPv6: cada descarga
# intenta IPv6, se cuelga y cae a IPv4 a rastras. Preferir IPv4 deja apt y pip
# rápidos. El host tiene el mismo ajuste en su /etc/gai.conf.
RUN echo 'precedence ::ffff:0:0/96 100' >> /etc/gai.conf

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --requirement requirements.txt

COPY app ./app

USER app

EXPOSE 8000

# `--forwarded-allow-ips` NO se fija aquí: uvicorn lee la variable de entorno
# FORWARDED_ALLOW_IPS (por defecto 127.0.0.1). Se define en .env con la subred
# de la red Docker del proxy externo, para no confiar en cabeceras
# X-Forwarded-* de cualquier origen. `--no-server-header` oculta uvicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--no-server-header"]
