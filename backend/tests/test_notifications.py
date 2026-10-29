import datetime as dt

import httpx
import pytest

from app.notifications import (
    MAX_LISTED,
    build_stale_message,
    send_telegram,
    stale_devices,
)


pytestmark = pytest.mark.anyio

NOW = dt.datetime(2026, 10, 29, 13, 0, tzinfo=dt.timezone.utc)


def _dev(name, group="AMG", enabled=True):
    return {"name": name, "group_name": group, "enabled": enabled}


def test_stale_incluye_nunca_respaldados_y_viejos() -> None:
    devices = [_dev("A"), _dev("B"), _dev("C")]
    statuses = {
        "A": {"last_success_at": NOW - dt.timedelta(hours=1)},   # fresco
        "B": {"last_success_at": NOW - dt.timedelta(hours=30)},  # viejo
        # C: nunca
    }
    atrasados = stale_devices(devices, statuses, 24, now=NOW)
    nombres = [d["name"] for d in atrasados]
    assert "A" not in nombres
    assert set(nombres) == {"B", "C"}
    # Los "nunca" van primero (horas None), luego del más viejo al menos.
    assert atrasados[0]["name"] == "C"
    assert atrasados[0]["horas"] is None


def test_stale_ignora_deshabilitados() -> None:
    devices = [_dev("A", enabled=False)]
    assert stale_devices(devices, {}, 24, now=NOW) == []


def test_stale_umbral_justo_en_el_limite() -> None:
    devices = [_dev("A")]
    statuses = {"A": {"last_success_at": NOW - dt.timedelta(hours=24)}}
    # Exactamente en el umbral no es atrasado (<=).
    assert stale_devices(devices, statuses, 24, now=NOW) == []


def test_mensaje_agrupa_por_empresa_y_cuenta() -> None:
    devices = [_dev("A-core", "AMG"), _dev("B-core", "Buenafenet")]
    statuses = {}  # ambos nunca
    atrasados = stale_devices(devices, statuses, 24, now=NOW)
    texto = build_stale_message(atrasados, 24, total_enabled=10, now=NOW)
    assert "2 de 10" in texto
    assert "<b>AMG</b>" in texto
    assert "<b>Buenafenet</b>" in texto
    assert "A-core" in texto and "nunca" in texto
    assert "hora Ecuador" in texto


def test_mensaje_topa_la_lista_larga() -> None:
    devices = [_dev(f"core-{i:03d}") for i in range(MAX_LISTED + 15)]
    atrasados = stale_devices(devices, {}, 24, now=NOW)
    texto = build_stale_message(atrasados, 24, total_enabled=200, now=NOW)
    listados = texto.count("•")
    assert listados == MAX_LISTED
    assert "15 equipo(s) más" in texto


def test_mensaje_sin_atrasados_es_solo_cabecera() -> None:
    texto = build_stale_message([], 24, total_enabled=5, now=NOW)
    assert "0 de 5" in texto
    assert "•" not in texto


async def test_send_telegram_sin_credenciales_no_llama() -> None:
    ok, detalle = await send_telegram("", "123", "hola")
    assert ok is False
    assert "token" in detalle.lower() or "chat_id" in detalle.lower()


async def test_send_telegram_ok(monkeypatch) -> None:
    llamadas = {}

    async def fake_post(self, url, json):  # noqa: A002
        llamadas["url"] = url
        llamadas["json"] = json
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    ok, detalle = await send_telegram("TOK", "CHAT", "mensaje")
    assert ok is True
    assert "/botTOK/sendMessage" in llamadas["url"]
    assert llamadas["json"]["chat_id"] == "CHAT"


async def test_send_telegram_error_devuelve_descripcion(monkeypatch) -> None:
    async def fake_post(self, url, json):  # noqa: A002
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    ok, detalle = await send_telegram("TOK", "CHAT", "mensaje")
    assert ok is False
    assert "Unauthorized" in detalle
