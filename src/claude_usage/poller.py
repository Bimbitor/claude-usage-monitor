"""Hilo de sondeo periodico del endpoint de uso.

El hilo esta pensado para vivir dias: ningun fallo lo mata. Ante un problema
pasajero espera cada vez mas; ante una sesion caducada se queda en pausa hasta
que un login nuevo lo reanuda, en vez de terminar y dejar a la app sin poller.
"""

from __future__ import annotations

import logging
import random
import threading
import time

from . import api, config, errors

log = logging.getLogger(__name__)

# Si el reloj de pared avanza mucho mas que la espera, el equipo estuvo
# suspendido: al despertar conviene consultar ya en vez de esperar el turno.
_SALTO_RELOJ = 120.0


class Poller:
    """Consulta el uso cada N segundos con reintento progresivo.

    Los callbacks se invocan desde el hilo del poller: quien los reciba debe
    reenviarlos al hilo de tkinter (root.after).

    ``on_error(exc, sesion_caducada)`` solo llega con ``sesion_caducada=True``
    cuando el servidor ha invalidado de verdad la sesion.
    """

    def __init__(self, client: api.UsageClient, on_snapshot, on_error, intervalo: int | None = None):
        self.client = client
        self.on_snapshot = on_snapshot
        self.on_error = on_error
        self.intervalo = intervalo or config.POLL_SECONDS
        self._despertar = threading.Event()
        self._parar = threading.Event()
        self._reanudar = threading.Event()
        self._reanudar.set()
        self._hilo: threading.Thread | None = None
        self._espera = float(self.intervalo)
        # Instante (monotonic) antes del cual no se vuelve a pedir nada, pase
        # lo que pase: es lo que impide que diez clics seguidos en "Actualizar"
        # se conviertan en diez peticiones.
        self._proxima = 0.0
        self._primer_fallo_auth = 0.0

    # --- ciclo de vida ---------------------------------------------------
    def start(self) -> None:
        self._hilo = threading.Thread(target=self._run, name="usage-poller", daemon=True)
        self._hilo.start()

    def stop(self) -> None:
        self._parar.set()
        self._reanudar.set()
        self._despertar.set()

    def join(self, timeout: float | None = None) -> None:
        if self._hilo:
            self._hilo.join(timeout)

    @property
    def pausado(self) -> bool:
        return not self._reanudar.is_set()

    def retomar(self, creds: dict) -> None:
        """Reanuda el sondeo con credenciales nuevas tras volver a iniciar sesion."""
        self.client.usar(creds)
        self._primer_fallo_auth = 0.0
        self._espera = float(self.intervalo)
        self._proxima = 0.0
        self._reanudar.set()
        self._despertar.set()

    # --- peticion manual -------------------------------------------------
    def refresh_now(self) -> float:
        """Pide una consulta inmediata.

        Devuelve los segundos que faltan si todavia hay veto (antirrebote o
        ventana de 429), 0 si la peticion sale ya. El veto no se puede saltar:
        insistir cuando el servidor esta limitando solo empeora las cosas.
        """
        if self.pausado:
            return 0.0
        restante = self._proxima - time.monotonic()
        if restante > 0:
            return restante
        self._espera = float(self.intervalo)
        self._despertar.set()
        return 0.0

    # --- bucle -----------------------------------------------------------
    def _run(self) -> None:
        while not self._parar.is_set():
            self._reanudar.wait()
            if self._parar.is_set():
                break

            restante = self._proxima - time.monotonic()
            if restante > 0:
                self._dormir(restante)
                continue

            self._ciclo()
            self._dormir(self._con_jitter(self._espera))

    def _ciclo(self) -> None:
        # Cualquier intento, salga bien o mal, abre el antirrebote minimo.
        self._proxima = time.monotonic() + config.MIN_REFRESH_SECONDS
        try:
            snapshot = self.client.fetch()
        except errors.RateLimited as exc:
            self._tras_429(exc)
        except errors.AuthError as exc:
            self._tras_auth(exc)
        except errors.TransientError as exc:
            log.info("Fallo temporal al consultar el uso: %s", exc)
            self._tras_fallo(exc)
        except Exception as exc:  # nunca dejar morir el hilo por un imprevisto
            log.exception("Error inesperado en el sondeo")
            self._tras_fallo(exc)
        else:
            self._primer_fallo_auth = 0.0
            self._espera = float(self.intervalo)
            self.on_snapshot(snapshot)

    def _tras_429(self, exc: errors.RateLimited) -> None:
        espera = exc.retry_after or min(self._espera * 2, config.RATE_LIMIT_BACKOFF_MAX)
        self._espera = min(max(espera, float(self.intervalo)), config.RATE_LIMIT_BACKOFF_MAX)
        # Mientras dure el castigo, ni el boton "Actualizar" pide nada.
        self._proxima = time.monotonic() + self._espera
        log.info("El servidor limita las peticiones; siguiente intento en %.0f s", self._espera)
        self._tras_fallo(exc, ya_espaciado=True)

    def _tras_auth(self, exc: errors.AuthError) -> None:
        """Un 4xx dudoso se reintenta durante una hora antes de rendirse."""
        if not exc.definitivo:
            ahora = time.monotonic()
            if not self._primer_fallo_auth:
                self._primer_fallo_auth = ahora
                log.warning("Fallo de autenticacion dudoso; se reintentara: %s", exc)
            elif ahora - self._primer_fallo_auth > config.SESSION_GRACE_SECONDS:
                log.warning("Una hora sin poder renovar la sesión; se da por caducada")
                exc = errors.AuthError(str(exc), definitivo=True)

        if not exc.definitivo:
            self._tras_fallo(exc)
            return

        log.warning("Sesión caducada: %s", exc)
        # En pausa, no muerto: tras un login nuevo este mismo hilo sigue.
        self._reanudar.clear()
        self.on_error(exc, True)

    def _tras_fallo(self, exc: Exception, ya_espaciado: bool = False) -> None:
        if not ya_espaciado:
            self._espera = min(self._espera * 2, config.POLL_BACKOFF_MAX)
        self.on_error(exc, False)

    # --- esperas ---------------------------------------------------------
    def _con_jitter(self, segundos: float) -> float:
        return segundos * random.uniform(1 - config.POLL_JITTER, 1 + config.POLL_JITTER)

    def _dormir(self, segundos: float) -> None:
        reloj = time.time()
        self._despertar.wait(segundos)
        self._despertar.clear()
        salto = time.time() - reloj - segundos
        if salto > _SALTO_RELOJ:
            # El equipo volvio de suspension: el token puede haber caducado
            # mientras dormia, asi que se consulta (y se renueva) ya mismo.
            log.info("El equipo estuvo suspendido ~%.0f min; se consulta al despertar",
                     salto / 60)
            self._proxima = 0.0
            self._espera = float(self.intervalo)
