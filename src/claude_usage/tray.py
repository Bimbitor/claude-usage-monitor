"""Icono de la bandeja del sistema: tooltip al pasar el cursor y menu."""

from __future__ import annotations

import logging

import pystray
from pystray import Menu, MenuItem

from . import api, autostart, config, icons

log = logging.getLogger(__name__)

_MAX_TOOLTIP = 127  # limite de szTip en NOTIFYICONDATA


class Tray:
    def __init__(self, *, on_toggle, on_refresh, on_logout, on_quit):
        self.icon = pystray.Icon(
            config.APP_NAME,
            icons.build_icon(None, None, apagado=True),
            f"{config.APP_TITLE}\nconectando…",
            menu=Menu(
                MenuItem("Ver uso", lambda *_: on_toggle(), default=True),
                MenuItem("Actualizar ahora", lambda *_: on_refresh()),
                Menu.SEPARATOR,
                MenuItem(
                    "Iniciar con Windows",
                    lambda *_: autostart.set_enabled(not autostart.is_enabled()),
                    checked=lambda _item: autostart.is_enabled(),
                ),
                MenuItem("Cerrar sesión", lambda *_: on_logout()),
                Menu.SEPARATOR,
                MenuItem("Salir", lambda *_: on_quit()),
            ),
        )

    def start(self) -> None:
        # run_detached deja el hilo principal libre para el mainloop de tkinter.
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            log.debug("El icono ya estaba detenido", exc_info=True)

    def notificar(self, texto: str) -> None:
        """Globo de notificacion de Windows. No esta en todos los backends."""
        try:
            self.icon.notify(texto, config.APP_TITLE)
        except Exception:
            log.debug("El icono no admite notificaciones", exc_info=True)

    def update(self, snapshot: api.UsageSnapshot | None,
               sesion_caducada: bool = False) -> None:
        sesion = snapshot.sesion if snapshot else None
        semanal = snapshot.semanal if snapshot else None
        apagado = snapshot is None or snapshot.stale or bool(snapshot.error)

        self.icon.icon = icons.build_icon(
            sesion.porcentaje if sesion else None,
            semanal.porcentaje if semanal else None,
            apagado=apagado,
        )
        self.icon.title = self._tooltip(snapshot, sesion_caducada)

    def _tooltip(self, snapshot: api.UsageSnapshot | None,
                 sesion_caducada: bool = False) -> str:
        if sesion_caducada:
            return f"{config.APP_TITLE}\nsesión caducada · pulsa para entrar"
        if snapshot is None:
            return f"{config.APP_TITLE}\nconectando…"
        if snapshot.error and not snapshot.limites:
            return f"{config.APP_TITLE}\n{snapshot.error}"

        lineas = ["Claude"]
        prefijos = {"sesion": "Sesión", "semanal": "Semana",
                    "opus": "Opus", "sonnet": "Sonnet"}
        verbos = {"sesion": "reset"}
        for limite in snapshot.limites:
            nombre = prefijos.get(limite.etiqueta, limite.etiqueta)
            verbo = verbos.get(limite.etiqueta, "corte")
            lineas.append(
                f"{nombre} {limite.porcentaje:.0f} % · {verbo} {config.human_delta(limite.restante)}"
            )
        if snapshot.error:
            lineas.append("⚠ sin conexión")
        elif snapshot.stale:
            lineas.append(f"datos de {config.human_age(snapshot.edad)}")

        return "\n".join(lineas)[:_MAX_TOOLTIP]
