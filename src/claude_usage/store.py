"""Persistencia: credenciales cifradas con DPAPI + ajustes en JSON.

DPAPI (CryptProtectData) ata el cifrado a la cuenta de Windows actual: el
fichero resultante no es descifrable por otro usuario ni en otro equipo.
Se usa via ctypes para no depender de pywin32.
"""

from __future__ import annotations

import ctypes
import json
import logging
import time
from ctypes import wintypes
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


def load_credentials() -> dict[str, Any] | None:
    """Devuelve {access_token, refresh_token, expires_at} o None."""
    path = config.credentials_file()
    if not path.exists():
        return None
    try:
        raw = dpapi_decrypt(path.read_bytes())
        creds = json.loads(raw.decode("utf-8"))
    except Exception:
        # Fichero corrupto, de otro usuario o de otro equipo: se descarta y se
        # vuelve a pedir login en vez de reventar el arranque.
        log.warning("No se pudieron leer las credenciales; se pedira login de nuevo", exc_info=True)
        return None
    if not creds.get("access_token"):
        return None
    return creds


def save_credentials(creds: dict[str, Any]) -> None:
    payload = json.dumps(creds).encode("utf-8")
    path = config.credentials_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(dpapi_encrypt(payload))
    tmp.replace(path)
    log.info("Credenciales guardadas en %s", path)


def clear_credentials() -> None:
    try:
        config.credentials_file().unlink(missing_ok=True)
        log.info("Credenciales borradas")
    except OSError:
        log.warning("No se pudieron borrar las credenciales", exc_info=True)


# --- Ajustes --------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "poll_seconds": config.POLL_SECONDS,
    "token_url": None,       # host de token que funciono la ultima vez
    "last_snapshot": None,   # cache para pintar algo nada mas arrancar
}


def load_settings() -> dict[str, Any]:
    settings = dict(_DEFAULTS)
    path = config.settings_file()
    if path.exists():
        try:
            settings.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            log.warning("settings.json ilegible; se usan valores por defecto", exc_info=True)
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    path = config.settings_file()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        log.warning("No se pudieron guardar los ajustes", exc_info=True)


def update_setting(key: str, value: Any) -> None:
    settings = load_settings()
    settings[key] = value
    save_settings(settings)


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
