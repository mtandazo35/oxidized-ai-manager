# Exposición del panel con Nginx Proxy Manager

El stack corre en un VPS local y se publica con un **Nginx Proxy Manager
externo**: NPM vive en su propio proyecto Docker y no se gestiona desde este
repositorio. Aquí solo se documenta cómo conectarlo.

Se publica **únicamente el panel/API**. PostgreSQL, Redis, el 8888 de Oxidized
y oxidized-web no se exponen ni en NPM ni en la LAN.

## 1. Conectar el backend a la red de NPM

La forma limpia es una red Docker compartida: NPM alcanza al backend por nombre
y **no se publica ningún puerto** hacia la LAN.

```bash
docker network ls                  # localice la red de NPM, p. ej. npm_default
cd /opt/oxidized-ai-manager
sudo ./install.sh --proxy npm_default
```

El instalador:

- comprueba que la red exista,
- escribe en `.env` `PROXY_NETWORK` y `FORWARDED_ALLOW_IPS` (la subred de esa
  red, lo único en lo que uvicorn confiará para leer `X-Forwarded-For`),
- deja `API_BIND_ADDRESS=127.0.0.1` (el puerto ya no sale a la LAN),
- levanta el stack con `deploy/docker-compose.proxy.yml`.

Manualmente es lo mismo:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.proxy.yml up -d
```

## 2. Crear el Proxy Host en NPM

| Campo | Valor |
| --- | --- |
| Domain Names | `oxidized.sudominio.com` |
| Scheme | `http` |
| Forward Hostname / IP | `backend` ← el nombre del servicio, **no** una IP |
| Forward Port | `8000` |
| Cache Assets | off |
| Block Common Exploits | on |
| Websockets Support | off (el panel no los usa) |

En la pestaña **SSL**: certificado, *Force SSL* y *HTTP/2*. Deje **HSTS
apagado** hasta confirmar que el dominio y sus subdominios funcionan; cuando lo
active, actívelo también en la aplicación (`APP_ENABLE_HSTS=true` en `.env`),
que es la que lo emite de forma consistente.

En **Advanced**, suba el límite de cuerpo o la carga masiva `.xlsx` fallará con
413 (NPM arranca con el 1 MB por defecto de Nginx):

```nginx
client_max_body_size 8m;
```

## 3. Restringir quién llega

Cree una **Access List** en NPM con sus IP de gestión, la VPN WireGuard y la
red administrativa, y aplíquela al proxy host. NPM filtra el acceso de red; el
login de la aplicación sigue activo e identifica al usuario: son dos capas
distintas y ninguna reemplaza a la otra.

Lo recomendable es no dejar el subdominio abierto a Internet aunque tenga
HTTPS: publíquelo solo por WireGuard o Tailscale.

## 4. Certificado sin dominio público

Con un dominio interno o solo IP, Let's Encrypt por HTTP-01 no puede validar
(no hay DNS público que resolver). Opciones:

- **DNS-01** en NPM, si su proveedor de DNS está entre los soportados: funciona
  con dominios internos mientras la zona sea suya.
- **Certificado propio**, subido en NPM → *SSL Certificates* → *Add Custom*.
  El navegador avisará salvo que instale la CA en los equipos de gestión.

En ambos casos deje `APP_ENABLE_HSTS=false` mientras el certificado no sea de
confianza para los navegadores: HSTS deja al navegador clavado en HTTPS para
ese host y complica volver atrás.

## 5. Qué aporta cada capa

| Protección | Quién la pone |
| --- | --- |
| TLS, HTTP/2, redirección a HTTPS | NPM |
| Lista de acceso por IP / VPN | NPM |
| `client_max_body_size` | NPM |
| Cabeceras de seguridad (CSP, X-Frame, nosniff, Referrer, Permissions) | **la aplicación** |
| HSTS | la aplicación (`APP_ENABLE_HSTS`) |
| Rate limit de login por IP (5/min) | **la aplicación** |
| Bloqueo por cuenta (8 fallos/5 min) | la aplicación |
| Sesión y permisos | la aplicación (JWT) |

Las cabeceras y el rate limit viven en la aplicación a propósito: son lo
primero que se pierde cuando alguien recrea un proxy host en NPM.

## 6. Monitoreo desde Uptime Kuma

| URL | Qué vigila |
| --- | --- |
| `https://oxidized.sudominio.com/health/live` | El backend responde. |
| `https://oxidized.sudominio.com/health/ready` | PostgreSQL, Redis y Oxidized alcanzables. |
| `https://oxidized.sudominio.com/health/backups` | **503 si algún equipo lleva sin respaldo** más de 3× su intervalo. |

Los tres son públicos (sin token) y `/health/backups` devuelve solo recuentos,
nunca nombres de equipos. El umbral se ajusta con `BACKUP_STALENESS_FACTOR`.

Si aplica una Access List en NPM, agregue la IP de Uptime Kuma o publique un
proxy host aparte solo para `/health/*`.

## 7. Operación

```bash
cd /opt/oxidized-ai-manager
docker compose -f docker-compose.yml -f deploy/docker-compose.proxy.yml ps
docker compose -f docker-compose.yml -f deploy/docker-compose.proxy.yml logs --tail=100 backend
```

Para cambiar la clave del panel: menú de usuario, o
`POST /api/auth/change-password`. Si se pierde: `DELETE FROM users;` y reinicie
el backend, que vuelve a sembrarla desde `ADMIN_USERNAME`/`ADMIN_PASSWORD`
del `.env`.
