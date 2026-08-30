# Acceso web público

El despliegue de referencia publica exclusivamente Oxidized mediante Nginx en `https://178.156.243.184`. PostgreSQL, Redis, el puerto directo de Oxidized y FastAPI permanecen sin exposición pública.

## Protecciones

- Certificado de dirección IP emitido por Let's Encrypt.
- TLS 1.2 o 1.3 y redirección de HTTP a HTTPS.
- Autenticación HTTP obligatoria mediante `/etc/oxidized-ai-manager/.htpasswd`.
- Proxy ejecutado con filesystem de solo lectura y `no-new-privileges`.

Los certificados para direcciones IP son de vigencia corta. El servidor utiliza `oxidized-cert-renew.timer` dos veces al día; Certbot detiene el proxy solo cuando debe renovar y vuelve a iniciarlo al finalizar.

## Operación

```bash
cd /opt/oxidized-ai-manager
docker compose -f docker-compose.yml -f deploy/docker-compose.public.yml ps
systemctl status oxidized-cert-renew.timer
systemctl list-timers oxidized-cert-renew.timer
```

Para cambiar la contraseña, genere un nuevo hash APR1 y reemplace de forma segura el archivo `.htpasswd`; no agregue el hash ni la contraseña al repositorio.
