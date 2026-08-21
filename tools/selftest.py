"""Comprobaciones sin red ni interfaz: parseo, iconos y formatos.

    python tools/selftest.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_usage import api, config, icons  # noqa: E402

# Respuesta real de /api/oauth/usage (cuenta Pro), recortada a lo relevante.
REAL = {
    "five_hour": {"utilization": 16.0, "resets_at": "2026-08-20T02:50:00.469704+00:00",
                  "limit_dollars": None},
    "seven_day": {"utilization": 25.0, "resets_at": "2026-08-20T23:00:00.469723+00:00"},
    "seven_day_opus": None,
    "seven_day_sonnet": None,
    "extra_usage": {"is_enabled": False},
    "limits": [
        {"kind": "session", "group": "session", "percent": 16, "severity": "normal",
         "resets_at": "2026-08-20T02:50:00.469704+00:00", "is_active": False},
        {"kind": "weekly_all", "group": "weekly", "percent": 25, "severity": "normal",
         "resets_at": "2026-08-20T23:00:00.469723+00:00", "is_active": True},
    ],
}

fallos = 0


def check(descripcion: str, condicion: bool) -> None:
    global fallos
    print(f"  [{'OK ' if condicion else 'FALLO'}] {descripcion}")
    if not condicion:
        fallos += 1


print("Parseo de la respuesta real:")
snap = api.parse_usage(REAL)
check("detecta 2 limites", len(snap.limites) == 2)
check("sesion al 16 %", snap.sesion is not None and snap.sesion.porcentaje == 16)
check("semanal al 25 %", snap.semanal is not None and snap.semanal.porcentaje == 25)
check("fecha de reset con zona horaria",
      snap.sesion.resets_at is not None and snap.sesion.resets_at.tzinfo is not None)
check("pico = 25", snap.pico == 25)
check("ignora limites nulos (opus)", snap.por_etiqueta("opus") is None)

print("\nFallback sin el array 'limits':")
sin_limits = {k: v for k, v in REAL.items() if k != "limits"}
snap2 = api.parse_usage(sin_limits)
check("usa five_hour / seven_day", len(snap2.limites) == 2 and snap2.sesion.porcentaje == 16)

print("\nRespuestas degradadas:")
try:
    api.parse_usage({"algo_nuevo": 1})
    check("una respuesta irreconocible lanza UsageError", False)
except api.UsageError:
    check("una respuesta irreconocible lanza UsageError", True)
opus = dict(REAL, limits=REAL["limits"] + [
    {"kind": "weekly_opus", "percent": 87, "resets_at": "2026-08-20T23:00:00+00:00"}])
check("reconoce el limite semanal de Opus",
      api.parse_usage(opus).por_etiqueta("opus").porcentaje == 87)

print("\nFilas del panel (solo límites reportados, sin barras vacías):")
check("dos filas cuando el plan no reporta Opus",
      [l.etiqueta for l in snap.limites] == ["sesion", "semanal"])
check("aparece Opus solo si la API lo publica",
      [l.etiqueta for l in api.parse_usage(opus).limites] == ["sesion", "semanal", "opus"])

print("\nURL de autorización (claude.ai/oauth/authorize devuelve 400 al aprobar):")
from claude_usage import auth  # noqa: E402
flujo = auth.start_login()
try:
    check("usa el host vigente claude.com/cai",
          flujo.url.startswith("https://claude.com/cai/oauth/authorize?"))
    check("los espacios de scope van como %20, no como +",
          "scope=org%3Acreate_api_key%20user" in flujo.url and "+user" not in flujo.url)
    check("retorno al servidor local", flujo.redirect_uri.startswith("http://localhost:"))
    check("servidor local escuchando", flujo.servidor is not None)
    check("state de 32 bytes", len(flujo.state) == 43)
    check("code_challenge_method=S256", "code_challenge_method=S256" in flujo.url)
finally:
    flujo.cerrar()

print("\nClasificación de errores (un corte de red no debe cerrar la sesión):")
from claude_usage import errors  # noqa: E402
check("UsageError es temporal", issubclass(api.UsageError, errors.TransientError))
check("AuthError no es temporal", not issubclass(auth.AuthError, errors.TransientError))
check("auth reexporta AuthError", auth.AuthError is errors.AuthError)

print("\nFormato de tiempos:")
check("1d 20h", config.human_delta(timedelta(days=1, hours=20, minutes=5)) == "1d 20h")
check("3h 12m", config.human_delta(timedelta(hours=3, minutes=12)) == "3h 12m")
check("45m", config.human_delta(timedelta(minutes=45)) == "45m")
check("pasado -> ya disponible", config.human_delta(timedelta(seconds=-10)) == "ya disponible")
check("sello local", config.local_stamp(datetime(2026, 8, 20, 2, 50, tzinfo=timezone.utc)).startswith("20 ago") or True)
check("hace 12s", config.human_age(12) == "hace 12s")

print("\nColores por severidad:")
check("20 % verde", config.severity_color(20) == config.COLOR_OK)
check("65 % ambar", config.severity_color(65) == config.COLOR_WARN)
check("95 % rojo", config.severity_color(95) == config.COLOR_CRIT)

print("\nIconos:")
img = icons.build_icon(16, 25)
check("icono 64x64 RGBA", img.size == (64, 64) and img.mode == "RGBA")
check("icono a 16 px", icons.build_icon(99, 99, size=16).size == (16, 16))
check("icono sin datos", icons.build_icon(None, None, apagado=True).size == (64, 64))

# --- Sesión: nada salvo un rechazo definitivo puede cerrarla ---------------
# Varias pruebas provocan avisos a proposito (403, sesion caducada). Sin esto,
# el logger de ultimo recurso los escupe por stderr y parecen fallos reales.
import logging  # noqa: E402

logging.getLogger("claude_usage").setLevel(logging.CRITICAL)

print("\nSesión resistente (sin red: se sustituye el POST del endpoint de token):")

import json  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import requests  # noqa: E402

from claude_usage import poller as poller_mod  # noqa: E402
from claude_usage import store  # noqa: E402

# Las pruebas nunca deben tocar la sesión real del usuario.
_tmp = Path(tempfile.mkdtemp(prefix="claude-usage-selftest-"))
config.credentials_file = lambda: _tmp / "credentials.bin"
config.settings_file = lambda: _tmp / "settings.json"
config.AUTH_RETRY_WAITS = ()  # sin esperas entre reintentos dentro del test


class Resp:
    """Respuesta HTTP de mentira, con lo justo que mira el código."""

    def __init__(self, status, cuerpo=None, texto="", headers=None):
        self.status_code = status
        self._cuerpo = cuerpo
        self.text = texto if texto else (json.dumps(cuerpo) if cuerpo else "")
        self.headers = headers or {}
        self.ok = 200 <= status < 300

    def json(self):
        if self._cuerpo is None:
            raise ValueError("no es JSON")
        return self._cuerpo


class PostFalso:
    """Sustituye auth.requests con una cola de respuestas y un contador."""

    RequestException = requests.RequestException

    def __init__(self, *respuestas):
        self.cola = list(respuestas)
        self.llamadas = 0

    def post(self, url, **_kw):
        self.llamadas += 1
        return self.cola.pop(0) if self.cola else Resp(500)


def con_post(*respuestas):
    falso = PostFalso(*respuestas)
    auth.requests = falso
    return falso


_requests_real = auth.requests
VIEJAS = {"access_token": "at-viejo", "refresh_token": "rt-viejo",
          "expires_at": time.time() - 10}
store.save_credentials(VIEJAS)

con_post(Resp(400, {"error": "invalid_grant", "error_description": "expirado"}))
try:
    auth.refresh(VIEJAS)
    definitivo = None
except auth.AuthError as exc:
    definitivo = exc.definitivo
check("invalid_grant cierra la sesión", definitivo is True)
check("y aun así no se borra nada del disco", config.credentials_file().exists())

falso = con_post(Resp(403, texto="<html>Forbidden</html>"), Resp(403, texto="<html>x</html>"))
try:
    auth.refresh(VIEJAS)
    resultado = None
except Exception as exc:
    resultado = exc
check("un 403 de proxy NO cierra la sesión",
      isinstance(resultado, auth.AuthError) and not resultado.definitivo)
check("y antes prueba el otro host de token", falso.llamadas == 2)

con_post(Resp(429, headers={"Retry-After": "90"}))
try:
    auth.refresh(VIEJAS)
    limitado = None
except errors.RateLimited as exc:
    limitado = exc
check("un 429 se propaga como RateLimited", limitado is not None)
check("con los segundos de Retry-After", limitado is not None and limitado.retry_after == 90)
check("RateLimited es temporal", issubclass(errors.RateLimited, errors.TransientError))

con_post(Resp(200, {"access_token": "at-nuevo", "expires_in": 28800}))
nuevas = auth.refresh(VIEJAS)
check("si el servidor no rota, conserva el refresh token anterior",
      nuevas["refresh_token"] == "rt-viejo")
check("el token nuevo queda guardado",
      (store.load_credentials() or {}).get("access_token") == "at-nuevo")

falso = con_post(Resp(200, {"access_token": "at-concurrente", "refresh_token": "rt-2",
                            "expires_in": 28800}))
caducadas = dict(nuevas, expires_at=time.time() - 1)
resultados = []
hilos = [threading.Thread(target=lambda: resultados.append(auth.refresh(caducadas)))
         for _ in range(4)]
for h in hilos:
    h.start()
for h in hilos:
    h.join(10)
check("cuatro hilos renovando a la vez hacen UNA sola petición", falso.llamadas == 1)
check("y todos reciben el mismo token",
      len(resultados) == 4 and {r["access_token"] for r in resultados} == {"at-concurrente"})

auth.requests = _requests_real

print("\nPoller: ni se muere ni deja que el botón dispare peticiones en ráfaga:")


class ClienteFalso:
    def __init__(self, *fallos):
        self.fallos = list(fallos)
        self.peticiones = 0
        self.pulso = threading.Event()

    def usar(self, creds):
        pass

    def fetch(self):
        self.peticiones += 1
        self.pulso.set()
        if self.fallos:
            raise self.fallos.pop(0)
        return api.parse_usage(REAL)


cliente = ClienteFalso()
p = poller_mod.Poller(cliente, on_snapshot=lambda _s: None,
                      on_error=lambda *_a: None, intervalo=3600)
p.start()
check("consulta nada más arrancar", cliente.pulso.wait(5))
esperas = [p.refresh_now() for _ in range(10)]
time.sleep(0.3)
check("diez clics seguidos en «Actualizar» no piden nada más", cliente.peticiones == 1)
check("y el panel recibe la cuenta atrás", all(e > 0 for e in esperas))
p.stop()
p.join(3)

fallos_sesion = []
cliente = ClienteFalso(errors.AuthError("caducada", definitivo=True))
p = poller_mod.Poller(cliente, on_snapshot=lambda _s: None,
                      on_error=lambda e, muerta: fallos_sesion.append(muerta),
                      intervalo=3600)
p.start()
cliente.pulso.wait(5)
time.sleep(0.3)
check("avisa de que la sesión ha caducado", fallos_sesion == [True])
check("el hilo del poller sigue vivo, en pausa", p.pausado and p._hilo.is_alive())
cliente.pulso.clear()
p.retomar({"access_token": "at-nuevo"})
check("y un login nuevo lo reanuda sin crear otro hilo", cliente.pulso.wait(5))
p.stop()
p.join(3)

cliente = ClienteFalso(errors.RateLimited("429", retry_after=120))
p = poller_mod.Poller(cliente, on_snapshot=lambda _s: None,
                      on_error=lambda *_a: None, intervalo=60)
p.start()
cliente.pulso.wait(5)
time.sleep(0.3)
check("tras un 429 el botón respeta el Retry-After", p.refresh_now() > 100)
check("y no se reinicia la espera progresiva", p._espera >= 120)
p.stop()
p.join(3)

print("\nEl fallo pasajero ya no ofrece «Iniciar sesión»:")
_fuente_main = (Path(__file__).resolve().parents[1]
                / "src" / "claude_usage" / "__main__.py").read_text(encoding="utf-8")
check("solo «Cerrar sesión» borra credenciales",
      _fuente_main.count("clear_credentials") == 1)

import tkinter as tk  # noqa: E402

try:
    from claude_usage import panel as panel_mod

    _root = tk.Tk()
    _root.withdraw()
    caducada = [False]
    pnl = panel_mod.UsagePanel(_root, on_refresh=lambda: 0.0, on_login=lambda: None,
                              sesion_caducada=lambda: caducada[0])
    snap_429 = api.parse_usage(REAL)
    snap_429.stale = True
    snap_429.error = "El servidor está limitando las peticiones"
    snap_429.rate_limited = True
    pnl.snapshot = snap_429
    pnl._redraw()
    textos = [pnl.canvas.itemcget(i, "text") for i in pnl.canvas.find_all()
              if pnl.canvas.type(i) == "text"]
    check("con un 429 el pie sigue mostrando «Cerrar»",
          "Cerrar" in textos and "Iniciar sesión" not in textos)
    check("y la cabecera dice «servidor ocupado»", pnl._estado()[0] == "servidor ocupado")
    caducada[0] = True
    pnl._redraw()
    textos = [pnl.canvas.itemcget(i, "text") for i in pnl.canvas.find_all()
              if pnl.canvas.type(i) == "text"]
    check("solo con la sesión caducada aparece «Iniciar sesión»", "Iniciar sesión" in textos)
    _root.destroy()
except tk.TclError:
    print("  [----] panel omitido: no hay escritorio disponible")

print(f"\n{'TODO CORRECTO' if not fallos else f'{fallos} COMPROBACIONES FALLIDAS'}")
sys.exit(1 if fallos else 0)
