"""Persistencia: credenciales cifradas con DPAPI + ajustes en JSON.

DPAPI (CryptProtectData) ata el cifrado a la cuenta de Windows actual: el
fichero resultante no es descifrable por otro usuario ni en otro equipo.
Se usa via ctypes para no depender de pywin32.
"""

from __future__ import annotations

import ctypes
import json
import logging
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)

CRYPTPROTECT_UI_FORBIDDEN = 0x01
_ENTROPY = b"ClaudeUsageMonitor/v1"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_in(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_out(blob: _DataBlob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def dpapi_encrypt(data: bytes) -> bytes:
    out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_blob_in(data)),
        ctypes.c_wchar_p(config.APP_NAME),
        ctypes.byref(_blob_in(_ENTROPY)),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise OSError(ctypes.GetLastError(), "CryptProtectData fallo")
    return _blob_out(out)


def dpapi_decrypt(data: bytes) -> bytes:
    out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_blob_in(data)),
        None,
        ctypes.byref(_blob_in(_ENTROPY)),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise OSError(ctypes.GetLastError(), "CryptUnprotectData fallo")
    return _blob_out(out)


# --- Credenciales ---------------------------------------------------------


_CREDS_LOCK = threading.RLock()


def _backup_file() -> Path:
    return config.credentials_file().with_suffix(".bak")


def _leer(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    creds = json.loads(dpapi_decrypt(path.read_bytes()).decode("utf-8"))
    return creds if creds.get("access_token") else None


def load_credentials() -> dict[str, Any] | None:
    """Devuelve {access_token, refresh_token, expires_at} o None.

    Si el fichero principal no se puede leer se prueba la copia anterior: un
    guardado interrumpido no debe costarle al usuario un login nuevo.
    """
    with _CREDS_LOCK:
        for path in (config.credentials_file(), _backup_file()):
            try:
                creds = _leer(path)
            except Exception:
                # Corrupto, de otro usuario o de otro equipo: se prueba el respaldo.
                log.warning("No se pudieron leer las credenciales de %s", path.name,
                            exc_info=True)
                continue
            if creds:
                if path != config.credentials_file():
                    log.info("Credenciales recuperadas de la copia de respaldo")
                return creds
    return None


def save_credentials(creds: dict[str, Any]) -> bool:
    """Guarda las credenciales sin lanzar: el token nuevo ya vive en memoria.

    Si el guardado falla, perder la sesion en marcha seria el peor desenlace
    posible, asi que se registra el fallo y se sigue. Antes de sustituir el
    fichero se conserva el anterior como respaldo.
    """
    with _CREDS_LOCK:
        path = config.credentials_file()
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_bytes(dpapi_encrypt(json.dumps(creds).encode("utf-8")))
            if path.exists():
                try:
                    _backup_file().write_bytes(path.read_bytes())
                except OSError:
                    log.debug("No se pudo escribir la copia de respaldo", exc_info=True)
            tmp.replace(path)
        except Exception:
            log.error("No se pudieron guardar las credenciales en %s", path, exc_info=True)
            return False
        log.info("Credenciales guardadas en %s", path)
        return True


def clear_credentials() -> None:
    """Borra la sesion del disco. Solo desde 'Cerrar sesion' del menu.

    Ningun fallo de red, 429 ni 4xx dudoso llama aqui: borrar el refresh token
    es irreversible y obliga a un login nuevo.
    """
    with _CREDS_LOCK:
        for path in (config.credentials_file(), _backup_file()):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.warning("No se pudieron borrar las credenciales", exc_info=True)
        log.info("Credenciales borradas")


# --- Ajustes --------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "poll_seconds": config.POLL_SECONDS,
    "token_url": None,       # host de token que funciono la ultima vez
    "last_snapshot": None,   # cache para pintar algo nada mas arrancar
}


# El hilo del poller (token_url, last_snapshot) y el de la interfaz escriben en
# el mismo fichero: sin cerrojo, dos lecturas-modificaciones-escrituras
# simultaneas se pisan y una de las dos claves se pierde.
_SETTINGS_LOCK = threading.RLock()


def load_settings() -> dict[str, Any]:
    settings = dict(_DEFAULTS)
    path = config.settings_file()
    with _SETTINGS_LOCK:
        if path.exists():
            try:
                # utf-8-sig: si alguien edita el fichero con el Bloc de notas
                # y le deja un BOM, no se pierden todos los ajustes.
                settings.update(json.loads(path.read_text(encoding="utf-8-sig")))
            except Exception:
                log.warning("settings.json ilegible; se usan valores por defecto", exc_info=True)
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    path = config.settings_file()
    tmp = path.with_suffix(".tmp")
    with _SETTINGS_LOCK:
        try:
            tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            log.warning("No se pudieron guardar los ajustes", exc_info=True)


def update_setting(key: str, value: Any) -> None:
    with _SETTINGS_LOCK:
        settings = load_settings()
        settings[key] = value
        save_settings(settings)


def poll_seconds() -> int:
    """Intervalo de sondeo, acotado a un ritmo que la API tolere.

    Los ajustes guardados por versiones anteriores traen 60 s, que es
    justamente lo que hacia saltar el limite de peticiones: se corrigen al
    vuelo y se reescriben para no volver a leerlos mal.
    """
    guardado = load_settings().get("poll_seconds")
    try:
        valor = int(guardado)
    except (TypeError, ValueError):
        valor = config.POLL_SECONDS
    if valor < config.POLL_MIN_SECONDS:
        # Los 60 s que guardaban las versiones anteriores no son una eleccion
        # del usuario, son el ajuste que provocaba los 429: al nuevo por defecto.
        acotado = config.POLL_SECONDS
    else:
        acotado = min(valor, config.POLL_MAX_SECONDS)
    if acotado != guardado:
        log.info("Intervalo de sondeo ajustado a %s s", acotado)
        update_setting("poll_seconds", acotado)
    return acotado


_CACHE_CADA = 300
_ultimo_cache = 0.0


def cache_snapshot(data: dict[str, Any]) -> None:
    """Guarda el ultimo uso conocido, como mucho una vez cada 5 minutos.

    Solo sirve para pintar algo al arrancar, asi que no compensa reescribir el
    fichero en cada sondeo.
    """
    global _ultimo_cache
    ahora = time.time()
    if ahora - _ultimo_cache < _CACHE_CADA:
        return
    _ultimo_cache = ahora
    update_setting("last_snapshot", {"saved_at": ahora, "payload": data})
