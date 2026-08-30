# Acceso web público

El despliegue publica el **panel de la plataforma** (y su API) mediante Nginx en
`https://<PUBLIC_HOST>`, donde `PUBLIC_HOST` es la IP o dominio del host (se
define en `.env` y el instalador renderiza `deploy/nginx.conf` desde
`deploy/nginx.conf.template`). El login es el de la plataforma (usuario en
PostgreSQL, clave cambiable desde el propio panel). PostgreSQL, Redis, el puerto
directo de Oxidized y oxidized-web permanecen sin exposición pública.

## Puesta en marcha

```bash
sudo ./install.sh --public tu-dominio-o-ip --cert
```

Esto fija `PUBLIC_HOST`, genera el `nginx.conf` para ese host, emite el
certificado (Certbot, standalone) y levanta el stack con el overlay público.
Sin `--cert`, coloque usted el certificado en
`/etc/letsencrypt/live/<PUBLIC_HOST>/` antes de exponer HTTPS.

El `.htpasswd` de Nginx quedó retirado: la autenticación la aplica el backend
con tokens JWT. La página de login y `/docs` son públicas; todos los endpoints
de datos exigen token.

## Protecciones

- Certificado de dirección IP emitido por Let's Encrypt.
- TLS 1.2 o 1.3 y redirección de HTTP a HTTPS.
- Rate limit en `/api/auth/login` (5 intentos/minuto por IP, ráfaga 5).
- Proxy ejecutado con filesystem de solo lectura y `no-new-privileges`.

Los certificados para direcciones IP son de vigencia corta. El servidor
utiliza `oxidized-cert-renew.timer` dos veces al día; Certbot detiene el proxy
solo cuando debe renovar y vuelve a iniciarlo al finalizar.

## Operación

```bash
cd /opt/oxidized-ai-manager
docker compose -f docker-compose.yml -f deploy/docker-compose.public.yml ps
systemctl status oxidized-cert-renew.timer
```

Para cambiar la clave del panel: sección "Cambiar clave" del propio panel, o
`POST /api/auth/change-password`. Si se pierde la clave, borre el usuario en
PostgreSQL (`DELETE FROM users;`) y reinicie el backend: se vuelve a sembrar
desde `ADMIN_USERNAME`/`ADMIN_PASSWORD` del `.env`.
