"""Cliente del endpoint de uso y modelo de datos que consume la interfaz."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from . import auth, config, store
from .errors import TransientError

log = logging.getLogger(__name__)


class UsageError(TransientError):
    """Fallo temporal al consultar el uso (red, 5xx, formato inesperado)."""


@dataclass
class Limite:
    """Un limite del plan: sesion de 5 h, semanal, o semanal de Opus."""

    etiqueta: str
    porcentaje: float
    resets_at: datetime | None

    @property
    def restante(self) -> timedelta | None:
        if self.resets_at is None:
            return None
        return self.resets_at - datetime.now(timezone.utc)


@dataclass
class UsageSnapshot:
    limites: list[Limite] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)
    plan: str | None = None
    stale: bool = False
    error: str | None = None

    def por_etiqueta(self, etiqueta: str) -> Limite | None:
        return next((l for l in self.limites if l.etiqueta == etiqueta), None)

    @property
    def sesion(self) -> Limite | None:
        return self.por_etiqueta("sesion")

    @property
    def semanal(self) -> Limite | None:
        return self.por_etiqueta("semanal")

    @property
    def pico(self) -> float | None:
        """Mayor porcentaje de todos los limites: define el color del icono."""
        if not self.limites:
            return None
        return max(l.porcentaje for l in self.limites)

    @property
    def edad(self) -> float:
        return time.time() - self.fetched_at


def _parse_ts(valor) -> datetime | None:
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        log.warning("Marca de tiempo no reconocida: %r", valor)
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pct(valor) -> float | None:
    try:
        return max(0.0, min(100.0, float(valor)))
    except (TypeError, ValueError):
        return None


# Nombres visibles de cada limite conocido, en orden de aparicion en el panel.
_ETIQUETAS = {
    "session": ("sesion", "Sesión (5 h)"),
    "weekly_all": ("semanal", "Semana (todos)"),
    "weekly_opus": ("opus", "Semana (Opus)"),
    "weekly_sonnet": ("sonnet", "Semana (Sonnet)"),
}
_CLAVES_RAIZ = {
    "five_hour": ("sesion", "Sesión (5 h)"),
    "seven_day": ("semanal", "Semana (todos)"),
    "seven_day_opus": ("opus", "Semana (Opus)"),
    "seven_day_sonnet": ("sonnet", "Semana (Sonnet)"),
}
_ORDEN = ["sesion", "semanal", "opus", "sonnet"]

# El panel pinta una fila por limite reportado, en este orden. Los que el plan
# no tiene (en Pro, `seven_day_opus` llega a null) simplemente no aparecen: una
# barra sin cifra no aporta nada.
TITULOS = {
    "sesion": "Sesión (5 h)",
    "semanal": "Semana (todos)",
    "opus": "Semana (Opus)",
    "sonnet": "Semana (Sonnet)",
}


def parse_usage(data: dict) -> UsageSnapshot:
    """Convierte la respuesta cruda de /api/oauth/usage en un snapshot.

    Se prioriza el array ``limits`` (formato canonico) y se cae a las claves
    de nivel superior (``five_hour`` / ``seven_day`` / ...) si no viene.
    Un limite con valor nulo simplemente no se muestra.
    """
    encontrados: dict[str, Limite] = {}

    for entrada in data.get("limits") or []:
        if not isinstance(entrada, dict):
            continue
        clave, titulo = _ETIQUETAS.get(entrada.get("kind"), (None, None))
        if clave is None:
            continue
        pct = _pct(entrada.get("percent"))
        if pct is None:
            continue
        encontrados[clave] = Limite(clave, pct, _parse_ts(entrada.get("resets_at")))

    for raiz, (clave, titulo) in _CLAVES_RAIZ.items():
        if clave in encontrados:
            continue
        bloque = data.get(raiz)
        if not isinstance(bloque, dict):
            continue
        pct = _pct(bloque.get("utilization"))
        if pct is None:
            continue
        encontrados[clave] = Limite(clave, pct, _parse_ts(bloque.get("resets_at")))

    if not encontrados:
        raise UsageError("La respuesta no contiene ningun limite reconocible")

    limites = [encontrados[c] for c in _ORDEN if c in encontrados]
    return UsageSnapshot(limites=limites, plan=_plan(data))


def _plan(data: dict) -> str | None:
    extra = data.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled"):
        return "Claude · créditos extra activos"
    return None


class UsageClient:
    """Consulta el uso renovando el token cuando hace falta."""

    def __init__(self, creds: dict):
        self.creds = creds
        self._sesion = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.creds['access_token']}",
            "anthropic-beta": config.BETA_HEADER,
            "Accept": "application/json",
            "User-Agent": config.USER_AGENT,
        }

    def fetch(self) -> UsageSnapshot:
        self.creds = auth.ensure_fresh(self.creds)
        respuesta = self._get()

        if respuesta.status_code == 401:
            # Token rechazado antes de tiempo: un intento de renovacion forzada.
            log.info("401 del endpoint de uso; forzando renovacion del token")
            self.creds = auth.ensure_fresh(self.creds, forzar=True)
            respuesta = self._get()

        if respuesta.status_code == 401:
            raise auth.AuthError("La sesión caducó, vuelve a iniciar sesión")
        if not respuesta.ok:
            raise UsageError(f"El servidor respondió {respuesta.status_code}")

        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise UsageError("Respuesta ilegible del servidor") from exc

        snapshot = parse_usage(datos)
        store.cache_snapshot(datos)
        return snapshot

    def _get(self):
        try:
            return self._sesion.get(
                config.USAGE_URL, headers=self._headers(), timeout=config.HTTP_TIMEOUT
            )
        except requests.RequestException as exc:
            raise UsageError(f"Sin conexión: {exc}") from exc


def snapshot_cacheado() -> UsageSnapshot | None:
    """Ultimo uso conocido, para pintar el icono nada mas arrancar."""
    guardado = store.load_settings().get("last_snapshot")
    if not isinstance(guardado, dict) or not isinstance(guardado.get("payload"), dict):
        return None
    try:
        snapshot = parse_usage(guardado["payload"])
    except UsageError:
        return None
    snapshot.fetched_at = float(guardado.get("saved_at") or 0)
    snapshot.stale = True
    return snapshot
