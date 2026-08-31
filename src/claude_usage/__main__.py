"""Punto de entrada: bandeja + panel + sondeo, con tkinter en el hilo principal."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import tkinter as tk
from tkinter import messagebox

from . import api, auth, config, errors, netconf, panel, poller, store, tray, winutil

log = logging.getLogger("claude_usage")


def _configurar_log() -> None:
    manejador = logging.handlers.RotatingFileHandler(
        config.log_file(), maxBytes=512_000, backupCount=2, encoding="utf-8"
    )
    manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.addHandler(manejador)
    if sys.stderr:  # en el .exe compilado no hay consola
        consola = logging.StreamHandler()
        consola.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        raiz.addHandler(consola)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.poller: poller.Poller | None = None
        self.snapshot: api.UsageSnapshot | None = None
        self.sesion_caducada = False
        self.demo = False
        self._login = None
        self._avisado = False

        self.panel = panel.UsagePanel(
            root, on_refresh=self.refrescar, on_login=self.pedir_login,
            sesion_caducada=lambda: self.sesion_caducada,
        )
        self.tray = tray.Tray(
            on_toggle=lambda: self.root.after(0, self.panel.toggle),
            on_refresh=lambda: self.root.after(0, self.refrescar),
            on_logout=lambda: self.root.after(0, self.cerrar_sesion),
            on_quit=lambda: self.root.after(0, self.salir),
        )

    # --- arranque --------------------------------------------------------
    def arrancar(self, creds: dict) -> None:
        """Pinta el ultimo dato conocido y lanza el sondeo en segundo plano."""
        self._parar_poller()  # p. ej. al volver a iniciar sesion
        self.sesion_caducada = False
        self._avisado = False
        cacheado = api.snapshot_cacheado()
        if cacheado:
            self._aplicar(cacheado)

        cliente = api.UsageClient(creds)
        self.poller = poller.Poller(
            cliente,
            on_snapshot=lambda s: self.root.after(0, self._aplicar, s),
            on_error=lambda e, es_auth: self.root.after(0, self._fallo, e, es_auth),
            intervalo=store.poll_seconds(),
        )
        self.poller.start()

    def arrancar_demo(self) -> None:
        """Datos de ejemplo para revisar la interfaz sin iniciar sesión."""
        from datetime import datetime, timedelta, timezone

        self.demo = True

        # Mismos límites que reporta un plan Pro real: sesión y semanal.
        ahora = datetime.now(timezone.utc)
        self._aplicar(api.UsageSnapshot(limites=[
            api.Limite("sesion", 16, ahora + timedelta(hours=3, minutes=12)),
            api.Limite("semanal", 62, ahora + timedelta(days=1, hours=20)),
        ]))
        self.root.after(600, self.panel.show)

    def start_tray(self) -> None:
        self.tray.update(self.snapshot)
        self.tray.start()

    # --- estado ----------------------------------------------------------
    def _aplicar(self, snapshot: api.UsageSnapshot) -> None:
        self.snapshot = snapshot
        self.tray.update(snapshot, sesion_caducada=self.sesion_caducada)
        self.panel.set_snapshot(snapshot)

    def _fallo(self, error: Exception, sesion_caducada: bool) -> None:
        """Ningun fallo borra la sesion del disco.

        Ni siquiera cuando el servidor la invalida: el fichero se sustituye
        cuando haya un login nuevo. Y la ventana de login no se abre sola
        encima de lo que este haciendo el usuario, basta con avisar.
        """
        if self.snapshot is None:
            self.snapshot = api.UsageSnapshot()
        self.snapshot.stale = True
        self.snapshot.error = str(error)
        self.snapshot.rate_limited = isinstance(error, errors.RateLimited)

        if sesion_caducada:
            self.sesion_caducada = True
            self.snapshot.rate_limited = False
            self.snapshot.error = "La sesión ha caducado"
            if not self._avisado:
                self._avisado = True
                self.tray.notificar("La sesión de Claude ha caducado. "
                                    "Abre el panel para volver a iniciarla.")

        self._aplicar(self.snapshot)

    def refrescar(self) -> float:
        """Pide dato fresco. Devuelve los segundos de espera si hay que aguardar."""
        if self.demo:
            return 0.0
        if self.sesion_caducada or self.poller is None:
            self.pedir_login()
            return 0.0
        return self.poller.refresh_now()

    # --- sesion ----------------------------------------------------------
    def pedir_login(self) -> None:
        from .login_window import LoginWindow

        # Si ya hay una ventana abierta se trae al frente en vez de duplicarla.
        if self._login is not None and self._login.win.winfo_exists():
            self._login.win.lift()
            self._login.win.focus_force()
            return
        self._login = LoginWindow(self.root, on_success=self._tras_login,
                                  on_cancel=self._login_cancelado)

    def _login_cancelado(self) -> None:
        self._login = None

    def _tras_login(self, creds: dict) -> None:
        self._login = None
        self.sesion_caducada = False
        self._avisado = False
        if self.poller is not None and self.poller.pausado:
            # El hilo sigue vivo esperando credenciales nuevas: no hace falta
            # levantar otro (dos pollers sobre la misma sesion se pisan).
            if self.snapshot is not None:
                self.snapshot.error = None
            self.poller.retomar(creds)
        else:
            self.arrancar(creds)
        self.tray.update(self.snapshot, sesion_caducada=False)

    def cerrar_sesion(self) -> None:
        if not messagebox.askyesno(
            config.APP_TITLE,
            "¿Cerrar la sesión de Claude en este equipo?\n"
            "Tendrás que volver a iniciar sesión para ver el consumo.",
        ):
            return
        self._parar_poller()
        store.clear_credentials()
        self.snapshot = None
        self.sesion_caducada = False
        self._avisado = False
        self.tray.update(None)
        self.panel.hide()
        self.pedir_login()

    def _parar_poller(self) -> None:
        if self.poller:
            self.poller.stop()
            # Se espera a que muera: dos pollers a la vez sobre las mismas
            # credenciales provocan renovaciones simultaneas.
            self.poller.join(timeout=2)
            self.poller = None

    def salir(self) -> None:
        auth.cancelar()
        self._parar_poller()
        self.tray.stop()
        self.root.quit()
        self.root.destroy()


def main() -> int:
    winutil.set_dpi_aware()
    _configurar_log()
    log.info("Arrancando %s %s", config.APP_TITLE, config.VERSION)
    # Antes de cualquier peticion: en redes que inspeccionan TLS (Zscaler y
    # demas) requests no confia en la CA corporativa y el login no llega a
    # completarse.
    netconf.configurar_tls()

    demo = "--demo" in sys.argv
    if not demo and not winutil.single_instance(f"{config.APP_NAME}-mutex"):
        log.info("Ya hay otra instancia en marcha; se cierra esta")
        return 0

    root = tk.Tk()
    root.withdraw()

    app = App(root)
    creds = store.load_credentials()
    if demo:
        app.arrancar_demo()
    elif creds:
        app.arrancar(creds)
    else:
        root.after(0, app.pedir_login)
    app.start_tray()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.salir()
    return 0


if __name__ == "__main__":
    sys.exit(main())
