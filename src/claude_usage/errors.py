"""Tipos de error compartidos.

La distincion importa: un fallo temporal se reintenta con espera progresiva,
mientras que un fallo de autenticacion borra la sesion y pide login otra vez.
Un corte de red nunca debe cerrar la sesion del usuario.
"""


class TransientError(Exception):
    """Fallo pasajero: sin red, 5xx, limite de peticiones. Se reintenta."""


class AuthError(Exception):
    """La sesion ya no sirve. Hay que volver a iniciar sesion."""
