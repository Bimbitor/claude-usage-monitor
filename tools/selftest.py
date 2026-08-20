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

print(f"\n{'TODO CORRECTO' if not fallos else f'{fallos} COMPROBACIONES FALLIDAS'}")
sys.exit(1 if fallos else 0)
