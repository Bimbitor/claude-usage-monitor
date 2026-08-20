"""Ayudas especificas de Windows: DPI, area de trabajo e instancia unica."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

SPI_GETWORKAREA = 0x0030
ERROR_ALREADY_EXISTS = 183


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def set_dpi_aware() -> None:
    """Evita que Windows escale la ventana por software (se veria borrosa)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            log.debug("No se pudo activar DPI awareness", exc_info=True)


def ui_scale() -> float:
    """Factor de escala de la pantalla (1.0 = 96 ppp, 1.5 = 150 %)."""
    try:
        dc = ctypes.windll.user32.GetDC(0)
        try:
            ppp = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, dc)
        if ppp:
            return max(1.0, ppp / 96.0)
    except Exception:
        log.debug("No se pudo leer el DPI", exc_info=True)
    return 1.0


def work_area() -> tuple[int, int, int, int]:
    """(left, top, right, bottom) del escritorio sin la barra de tareas."""
    rect = RECT()
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
    )
    if not ok:
        ancho = ctypes.windll.user32.GetSystemMetrics(0)
        alto = ctypes.windll.user32.GetSystemMetrics(1)
        return 0, 0, ancho, alto
    return rect.left, rect.top, rect.right, rect.bottom


_mutex_handle = None


def single_instance(nombre: str) -> bool:
    """True si esta es la unica instancia; False si ya habia otra corriendo."""
    global _mutex_handle
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex_handle = kernel32.CreateMutexW(None, wintypes.BOOL(True), f"Local\\{nombre}")
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
