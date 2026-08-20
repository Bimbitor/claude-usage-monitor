"""Arranque automatico con Windows (clave Run del usuario actual)."""

from __future__ import annotations

import logging
import sys
import winreg

from . import config

log = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _comando() -> str:
    """Linea de comandos con la que relanzar la app."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # En desarrollo se relanza el modulo con el interprete actual.
    return f'"{sys.executable}" -m claude_usage'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as clave:
            valor, _ = winreg.QueryValueEx(clave, config.APP_NAME)
            return bool(valor)
    except FileNotFoundError:
        return False
    except OSError:
        log.debug("No se pudo leer la clave Run", exc_info=True)
        return False


def set_enabled(activo: bool) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as clave:
            if activo:
                winreg.SetValueEx(clave, config.APP_NAME, 0, winreg.REG_SZ, _comando())
                log.info("Arranque automatico activado")
            else:
                try:
                    winreg.DeleteValue(clave, config.APP_NAME)
                    log.info("Arranque automatico desactivado")
                except FileNotFoundError:
                    pass
    except OSError:
        log.warning("No se pudo cambiar el arranque automatico", exc_info=True)
