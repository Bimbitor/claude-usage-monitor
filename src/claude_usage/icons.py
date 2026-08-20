"""Dibujo del icono de la bandeja segun el consumo actual."""

from __future__ import annotations

from PIL import Image, ImageDraw

from . import config

_SUPER = 8  # se dibuja a 8x y se reduce: bordes suaves sin antialias nativo


def _hex_to_rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), alpha)


def build_icon(sesion_pct: float | None, semanal_pct: float | None,
               apagado: bool = False, size: int = 64) -> Image.Image:
    """Anillo exterior = sesion de 5 h; disco interior = consumo semanal.

    Se lee de un vistazo a 16x16: el color domina y el arco da la magnitud.
    """
    lado = size * _SUPER
    img = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    dib = ImageDraw.Draw(img)

    color_sesion = config.COLOR_STALE if apagado else config.severity_color(sesion_pct)
    color_semana = config.COLOR_STALE if apagado else config.severity_color(semanal_pct)

    grosor = int(lado * 0.20)
    margen = int(lado * 0.04)
    caja = (margen, margen, lado - margen, lado - margen)

    # Contorno oscuro: mantiene el icono legible sobre barras de tareas claras
    dib.ellipse(caja, outline=(0, 0, 0, 90), width=int(lado * 0.03))

    # Pista del anillo
    dib.ellipse(caja, outline=_hex_to_rgba("#55534e", 255), width=grosor)

    # Arco de la sesion, en sentido horario desde las 12 en punto
    grados = 360 * (min(100.0, max(0.0, sesion_pct or 0.0)) / 100.0)
    if grados > 0:
        dib.arc(caja, start=-90, end=-90 + max(14, grados),
                fill=_hex_to_rgba(color_sesion), width=grosor)

    # Disco interior con el estado semanal
    hueco = margen + grosor + int(lado * 0.045)
    dib.ellipse((hueco, hueco, lado - hueco, lado - hueco),
                fill=_hex_to_rgba(color_semana, 255))

    return img.resize((size, size), Image.LANCZOS)


def build_app_icon(size: int = 256) -> Image.Image:
    """Icono estatico de la aplicacion (instalador, acceso directo, .exe)."""
    return build_icon(72, 46, size=size)


def write_ico(destino: str) -> None:
    base = build_app_icon(256)
    base.save(destino, format="ICO",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
