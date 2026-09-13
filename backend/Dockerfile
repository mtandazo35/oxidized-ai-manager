FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY app ./app

USER app

EXPOSE 8000

# `--forwarded-allow-ips` NO se fija aquí: uvicorn lee la variable de entorno
# FORWARDED_ALLOW_IPS (por defecto 127.0.0.1). Se define en .env con la subred
# de la red Docker del proxy externo, para no confiar en cabeceras
# X-Forwarded-* de cualquier origen. `--no-server-header` oculta uvicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--no-server-header"]
