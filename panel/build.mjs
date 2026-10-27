// Compila panel/panel.css con Tailwind y lo incrusta en el <style> del panel.
//
// El panel se sirve como un único index.html y la Content-Security-Policy no
// admite hojas externas de terceros, así que el CSS generado vive dentro del
// propio archivo, entre los marcadores `tailwind:begin` y `tailwind:end`.
// El despliegue no necesita Node: el resultado se guarda en el repositorio.
//
//   npm run build   # una vez
//   npm run watch   # recompila al guardar panel.css o index.html

import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, watch, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const PANEL = join(AQUI, "..", "backend", "app", "static", "index.html");
const ESCANEO = join(AQUI, ".scan", "index.html");
const SALIDA = join(AQUI, ".scan", "panel.css");
const INICIO = "/* tailwind:begin";
const FIN = "/* tailwind:end */";

function bloque(html) {
  const desde = html.indexOf(INICIO);
  const hasta = html.indexOf(FIN);
  if (desde < 0 || hasta < desde) {
    throw new Error("No se encuentran los marcadores tailwind:begin / tailwind:end en index.html");
  }
  return { desde, hasta: hasta + FIN.length };
}

function compilar() {
  const html = readFileSync(PANEL, "utf8");
  const { desde, hasta } = bloque(html);

  // Tailwind escanea una copia SIN el CSS ya generado: si leyera su propia
  // salida, cada compilación podría añadir clases que nadie usa.
  mkdirSync(dirname(ESCANEO), { recursive: true });
  writeFileSync(ESCANEO, html.slice(0, desde) + html.slice(hasta));

  const cli = join(AQUI, "node_modules", "@tailwindcss", "cli", "dist", "index.mjs");
  execFileSync(process.execPath, [cli, "-i", join(AQUI, "panel.css"), "-o", SALIDA, "--minify"], {
    cwd: AQUI,
    stdio: ["ignore", "ignore", "inherit"],
  });

  const css = readFileSync(SALIDA, "utf8").trim().replaceAll("</style", "<\\/style");
  const nuevo =
    html.slice(0, desde) +
    `${INICIO} — generado por panel/build.mjs desde panel/panel.css; no editar a mano */\n` +
    css +
    `\n${FIN}` +
    html.slice(hasta);

  if (nuevo !== html) writeFileSync(PANEL, nuevo);
  console.log(`[panel] CSS incrustado: ${(css.length / 1024).toFixed(1)} KiB`);
}

compilar();

if (process.argv.includes("--watch")) {
  let pendiente = null;
  const programar = () => {
    clearTimeout(pendiente);
    pendiente = setTimeout(() => {
      try { compilar(); } catch (error) { console.error(error.message); }
    }, 150);
  };
  watch(join(AQUI, "panel.css"), programar);
  // index.html cambia también al incrustar: se ignora el eco de la propia
  // escritura comparando el contenido escaneable.
  let ultimo = "";
  watch(PANEL, () => {
    const html = readFileSync(PANEL, "utf8");
    const { desde, hasta } = bloque(html);
    const escaneable = html.slice(0, desde) + html.slice(hasta);
    if (escaneable !== ultimo) { ultimo = escaneable; programar(); }
  });
  console.log("[panel] Vigilando panel.css e index.html…");
}
