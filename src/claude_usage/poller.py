"""Hilo de sondeo periodico del endpoint de uso."""

from __future__ import annotations

import logging
import threading

from . import api, config, errors

log = logging.getLogger(__name__)


class Poller:
    """Consulta el uso cada N segundos con reintento progresivo.

    Los callbacks se invocan desde el hilo del poller: quien los reciba debe
    reenviarlos al hilo de tkinter (root.after).
    """

    def __init__(self, client: api.UsageClient, on_snapshot, on_error, intervalo: int | None = None):
        self.client = client
        self.on_snapshot = on_snapshot
        self.on_error = on_error
        self.intervalo = intervalo or config.POLL_SECONDS
        self._despertar = threading.Event()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._espera = self.intervalo

    def start(self) -> None:
        self._hilo = threading.Thread(target=self._run, name="usage-poller", daemon=True)
        self._hilo.start()

    def stop(self) -> None:
        self._parar.set()
        self._despertar.set()

    def refresh_now(self) -> None:
        self._espera = self.intervalo
        self._despertar.set()

    def _run(self) -> None:
        while not self._parar.is_set():
            try:
                snapshot = self.client.fetch()
                self._espera = self.intervalo
                self.on_snapshot(snapshot)
            except errors.AuthError as exc:
                log.warning("Fallo de autenticacion: %s", exc)
                self.on_error(exc, True)
                return  # la app pedira login; este hilo termina
            except errors.TransientError as exc:
                log.info("Fallo temporal al consultar el uso: %s", exc)
                self.on_error(exc, False)
                self._espera = min(self._espera * 2, config.POLL_BACKOFF_MAX)
            except Exception as exc:  # nunca dejar morir el hilo por un imprevisto
                log.exception("Error inesperado en el sondeo")
                self.on_error(exc, False)
                self._espera = min(self._espera * 2, config.POLL_BACKOFF_MAX)

            self._despertar.wait(self._espera)
            self._despertar.clear()
