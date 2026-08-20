"""Constantes, rutas y utilidades de formato compartidas por toda la app."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

APP_NAME = "ClaudeUsageMonitor"
APP_TITLE = "Claude Usage Monitor"
VERSION = "1.0.0"

# --- OAuth ----------------------------------------------------------------
# client_id publico de Claude Code (extraido del binario oficial). El flujo es
# PKCE puro, no hay secreto de cliente.
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
# claude.ai/oauth/authorize sigue pintando la pantalla de consentimiento, pero
# el POST de aprobacion devuelve 400 "Invalid request format": el flujo vigente
# es el de claude.com/cai, que es el que usa el cliente oficial.
AUTHORIZE_URL = "https://claude.com/cai/oauth/authorize"
# Se prueban en orden; el primero que responda con 2xx se recuerda en settings.
TOKEN_URLS = (
    "https://platform.claude.com/v1/oauth/token",
    "https://console.anthropic.com/v1/oauth/token",
)
# Respaldo si no se puede abrir el servidor local: la pagina muestra el codigo
# para pegarlo a mano.
REDIRECT_URI_MANUAL = "https://platform.claude.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"

# --- API de uso -----------------------------------------------------------
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
USER_AGENT = f"{APP_NAME}/{VERSION}"

# --- Sondeo ---------------------------------------------------------------
POLL_SECONDS = 60
POLL_BACKOFF_MAX = 300
REFRESH_MARGIN_SECONDS = 300  # refrescar el token si le quedan menos de 5 min
HTTP_TIMEOUT = 20

# --- Rutas ----------------------------------------------------------------


def app_dir() -> Path:
    """Carpeta de datos por usuario: %APPDATA%\\ClaudeUsageMonitor."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def credentials_file() -> Path:
    return app_dir() / "credentials.bin"


def settings_file() -> Path:
    return app_dir() / "settings.json"


def log_file() -> Path:
    return app_dir() / "app.log"


# --- Paleta ---------------------------------------------------------------
COLOR_BG = "#262624"
COLOR_BORDER = "#3d3b37"
COLOR_TEXT = "#f5f4ef"
COLOR_MUTED = "#8f8d87"
COLOR_TRACK = "#3a3936"
COLOR_ACCENT = "#d97757"  # naranja Claude
COLOR_OK = "#5ea87a"
COLOR_WARN = "#e0a458"
COLOR_CRIT = "#d1604f"
COLOR_STALE = "#7a7873"


def severity_color(pct: float | None) -> str:
    """Verde / ambar / rojo segun el porcentaje de consumo."""
    if pct is None:
        return COLOR_STALE
    if pct >= 80:
        return COLOR_CRIT
    if pct >= 50:
        return COLOR_WARN
    return COLOR_OK


# --- Formato de tiempo ----------------------------------------------------
_MESES = [
    "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic",
]


def human_delta(delta: timedelta | None) -> str:
    """'1d 20h', '3h 12m', '45m'. Sin dependencias de locale."""
    if delta is None:
        return "--"
    total = int(delta.total_seconds())
    if total <= 0:
        return "ya disponible"
    dias, resto = divmod(total, 86400)
    horas, resto = divmod(resto, 3600)
    minutos = resto // 60
    if dias:
        return f"{dias}d {horas}h"
    if horas:
        return f"{horas}h {minutos}m"
    if minutos:
        return f"{minutos}m"
    return "<1m"


def local_stamp(dt: datetime | None) -> str:
    """'20 ago, 21:50' en hora local del equipo."""
    if dt is None:
        return "--"
    local = dt.astimezone()
    return f"{local.day} {_MESES[local.month - 1]}, {local:%H:%M}"


def human_age(seconds: float) -> str:
    """Antiguedad del ultimo dato: 'hace 12s', 'hace 4 min'."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"hace {seconds}s"
    if seconds < 3600:
        return f"hace {seconds // 60} min"
    if seconds < 86400:
        return f"hace {seconds // 3600} h"
    return f"hace {seconds // 86400} d"
