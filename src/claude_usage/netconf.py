"""Configuracion TLS: confiar en el almacen de certificados de Windows.

``requests`` valida siempre contra el paquete de CA que trae ``certifi`` y nunca
mira el almacen del sistema. En equipos detras de un proxy que inspecciona TLS
(Zscaler, Netskope, Cisco Umbrella, antivirus con escaneo HTTPS...), el
certificado que ve la app lo firma una CA corporativa que Windows si conoce pero
``certifi`` no: el resultado es ``CERTIFICATE_VERIFY_FAILED`` en cada peticion y
un inicio de sesion que no termina nunca.

``truststore`` redirige la validacion TLS de Python -y por tanto la de
``requests``- al almacen del sistema operativo, que ya incluye esa CA. Si no
esta disponible, se admite indicar un paquete de CA a mano (ajuste ``ca_bundle``
o variables ``REQUESTS_CA_BUNDLE`` / ``SSL_CERT_FILE``) y, como ultimo recurso,
desactivar la verificacion con el ajuste ``tls_no_verify``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import store

log = logging.getLogger(__name__)

_configurado = False


def configurar_tls() -> None:
    """Ajusta la validacion TLS del proceso. Idempotente; se llama al arrancar.

    Orden de preferencia:

    1. ``tls_no_verify`` en settings.json  -> se desactiva la verificacion.
    2. ``ca_bundle`` en settings.json o ``REQUESTS_CA_BUNDLE`` en el entorno
       -> se usa ese fichero de CA (y se exporta para la stdlib tambien).
    3. ``truststore`` instalado -> se valida contra el almacen de Windows.
    4. Nada de lo anterior -> ``requests`` sigue con el bundle de ``certifi``.
    """
    global _configurado
    if _configurado:
        return
    _configurado = True

    ajustes = store.load_settings()

    if ajustes.get("tls_no_verify"):
        _desactivar_verificacion()
        return

    bundle = _resolver_bundle(ajustes.get("ca_bundle"))
    if bundle:
        os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
        os.environ.setdefault("SSL_CERT_FILE", bundle)
        log.info("Verificacion TLS con el paquete de CA indicado: %s", bundle)
        return

    try:
        import truststore
    except ImportError:
        log.info("truststore no disponible; requests seguira usando certifi. "
                 "Si tu red inspecciona HTTPS (proxy/antivirus), define "
                 "REQUESTS_CA_BUNDLE o instala truststore.")
        return

    truststore.inject_into_ssl()
    log.info("TLS validado contra el almacen de certificados de Windows (truststore)")


def _resolver_bundle(valor) -> str | None:
    """Devuelve la ruta de un fichero de CA existente, o None."""
    for candidato in (valor, os.environ.get("REQUESTS_CA_BUNDLE"),
                      os.environ.get("SSL_CERT_FILE")):
        if candidato and Path(candidato).is_file():
            return str(Path(candidato))
        if candidato:
            log.warning("El paquete de CA indicado no existe: %s", candidato)
    return None


def _desactivar_verificacion() -> None:
    """Ultimo recurso: que requests no valide ningun certificado.

    Solo se activa a mano poniendo ``\"tls_no_verify\": true`` en settings.json.
    Se parchea ``Session.request`` para que ``verify=False`` sea el valor por
    defecto en toda la app (``requests.post`` y ``Session.get`` pasan por ahi).
    """
    import requests

    try:
        from urllib3.exceptions import InsecureRequestWarning
        import urllib3
        urllib3.disable_warnings(InsecureRequestWarning)
    except Exception:  # noqa: BLE001  (silenciar avisos es accesorio)
        log.debug("No se pudieron silenciar los avisos de urllib3", exc_info=True)

    original = requests.Session.request

    def _sin_verificar(self, *args, **kwargs):
        kwargs.setdefault("verify", False)
        return original(self, *args, **kwargs)

    requests.Session.request = _sin_verificar
    log.warning("VERIFICACION TLS DESACTIVADA (tls_no_verify). La conexion con "
                "Claude no esta protegida frente a intermediarios.")
