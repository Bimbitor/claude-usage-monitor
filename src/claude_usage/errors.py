"""Tipos de error compartidos.

La distincion importa: un fallo temporal se reintenta con espera progresiva,
mientras que un fallo de sesion *definitivo* obliga a volver a iniciar sesion.
Un corte de red, un 429 o un 403 de un proxy nunca deben cerrar la sesion.
"""

from __future__ import annotations


class TransientError(Exception):
    """Fallo pasajero: sin red, 5xx, limite de peticiones. Se reintenta."""


class RateLimited(TransientError):
    """El servidor pide bajar el ritmo (429).

    ``retry_after`` son los segundos que indica la cabecera del mismo nombre,
    o None si no la manda: el poller la respeta antes de volver a preguntar.
    """

    def __init__(self, mensaje: str, retry_after: float | None = None):
        super().__init__(mensaje)
        self.retry_after = retry_after


class AuthError(Exception):
    """Fallo de autenticacion.

    Solo con ``definitivo=True`` (el servidor invalido el refresh token) se da
    la sesion por muerta. El resto de 4xx del endpoint de token puede ser un
    proxy, una peticion mal formada o una carrera de rotacion: se reintenta.
    """

    def __init__(self, mensaje: str, definitivo: bool = False):
        super().__init__(mensaje)
        self.definitivo = definitivo


def parse_retry_after(valor: str | None) -> float | None:
    """Segundos de la cabecera Retry-After; solo se acepta el formato numerico."""
    if not valor:
        return None
    try:
        return max(0.0, float(str(valor).strip()))
    except ValueError:
        return None
