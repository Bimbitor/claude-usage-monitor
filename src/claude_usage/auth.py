"""Flujo OAuth 2.0 PKCE contra el login normal de Claude.

Replica el flujo del cliente oficial: el navegador vuelve solo a un servidor
HTTP efimero en 127.0.0.1, sin que el usuario tenga que copiar nada. Si ese
servidor no puede arrancar, queda el pegado manual del codigo como respaldo.

La app no toca las credenciales de Claude Code (~/.claude/.credentials.json):
obtiene su propio par de tokens, de modo que refrescarlo nunca invalida la
sesion del CLI, y funciona en equipos donde Claude Code no esta instalado.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from . import config, store
from .errors import (  # noqa: F401  (reexportados)
    AuthError,
    RateLimited,
    TransientError,
    parse_retry_after,
)

log = logging.getLogger(__name__)

# Un solo hilo renueva a la vez: el servidor rota el refresh token, asi que
# dos renovaciones simultaneas invalidan la del otro y tiran la sesion.
_LOCK = threading.RLock()
# Se activa al salir para no dejar la app colgada en una espera de reintento.
_parada = threading.Event()


def cancelar() -> None:
    """Interrumpe las esperas entre reintentos (se llama al cerrar la app)."""
    _parada.set()


_PAGINA_OK = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Sesión iniciada</title></head>
<body style="font-family:Segoe UI,system-ui,sans-serif;background:#262624;color:#f5f4ef;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h1 style="color:#d97757;font-size:20px">Ya puedes cerrar esta pestaña</h1>
<p style="color:#8f8d87">Claude Usage Monitor ha iniciado sesión correctamente.</p>
</div></body></html>"""

_PAGINA_ERROR = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Error</title></head>
<body style="font-family:Segoe UI,system-ui,sans-serif;background:#262624;color:#f5f4ef;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h1 style="color:#d1604f;font-size:20px">No se pudo completar el inicio de sesión</h1>
<p style="color:#8f8d87">Vuelve a la aplicación e inténtalo otra vez.</p>
</div></body></html>"""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class _CallbackHandler(BaseHTTPRequestHandler):
    """Recoge el ?code=...&state=... con el que vuelve el navegador."""

    def do_GET(self) -> None:  # noqa: N802  (nombre impuesto por BaseHTTPRequestHandler)
        partes = urllib.parse.urlparse(self.path)
        if partes.path.rstrip("/") not in ("/callback", ""):
            self.send_error(404)
            return
        consulta = urllib.parse.parse_qs(partes.query)
        codigo = (consulta.get("code") or [""])[0]
        estado = (consulta.get("state") or [""])[0]

        cuerpo = (_PAGINA_OK if codigo else _PAGINA_ERROR).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

        self.server.resultado = (codigo, estado)  # type: ignore[attr-defined]
        self.server.recibido.set()  # type: ignore[attr-defined]

    def log_message(self, formato: str, *args) -> None:
        log.debug("callback local: " + formato, *args)


class CallbackServer:
    """Servidor HTTP efimero en 127.0.0.1 con puerto libre asignado por el SO."""

    def __init__(self) -> None:
        self._http = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        self._http.recibido = threading.Event()  # type: ignore[attr-defined]
        self._http.resultado = ("", "")  # type: ignore[attr-defined]
        self.puerto = self._http.server_address[1]
        self._hilo = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._hilo.start()
        log.info("Servidor de callback escuchando en 127.0.0.1:%s", self.puerto)

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.puerto}/callback"

    def recibido(self) -> tuple[str, str] | None:
        """(codigo, state) si el navegador ya volvio; None si sigue esperando."""
        if self._http.recibido.is_set():  # type: ignore[attr-defined]
            return self._http.resultado  # type: ignore[attr-defined]
        return None

    def cerrar(self) -> None:
        try:
            self._http.shutdown()
            self._http.server_close()
        except Exception:
            log.debug("Fallo al cerrar el servidor de callback", exc_info=True)


@dataclass
class LoginFlow:
    """Estado de un intento de login: PKCE, URL y servidor de retorno."""

    url: str
    verifier: str
    state: str
    redirect_uri: str
    servidor: CallbackServer | None = field(default=None, repr=False)

    def cerrar(self) -> None:
        if self.servidor:
            self.servidor.cerrar()
            self.servidor = None


def start_login() -> LoginFlow:
    """Prepara el desafio PKCE y la URL de autorizacion."""
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(os.urandom(32))

    servidor: CallbackServer | None
    try:
        servidor = CallbackServer()
        redirect_uri = servidor.redirect_uri
    except OSError:
        # Sin puerto local disponible: se cae al pegado manual del codigo.
        log.warning("No se pudo abrir el servidor local; se usara el pegado manual",
                    exc_info=True)
        servidor = None
        redirect_uri = config.REDIRECT_URI_MANUAL

    params = {
        "code": "true",
        "client_id": config.CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": config.SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    # quote_via=quote: los espacios de 'scope' van como %20, no como '+'.
    consulta = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return LoginFlow(
        url=f"{config.AUTHORIZE_URL}?{consulta}",
        verifier=verifier,
        state=state,
        redirect_uri=redirect_uri,
        servidor=servidor,
    )


def open_browser(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except Exception:
        log.warning("No se pudo abrir el navegador", exc_info=True)
        return False


def _token_hosts() -> list[str]:
    """Host que funciono la ultima vez primero, luego el resto."""
    preferido = store.load_settings().get("token_url")
    hosts = list(config.TOKEN_URLS)
    if preferido in hosts:
        hosts.remove(preferido)
        hosts.insert(0, preferido)
    elif preferido:
        hosts.insert(0, preferido)
    return hosts


# Codigos OAuth con los que el servidor dice que estas credenciales ya no
# sirven. Cualquier otro 4xx puede ser un proxy, una peticion mal formada o una
# carrera de rotacion: eso se reintenta, no se cierra la sesion.
_ERRORES_DEFINITIVOS = frozenset({
    "invalid_grant", "invalid_client", "unauthorized_client", "access_denied",
})


def _error_oauth(resp) -> tuple[str, str]:
    """(codigo, descripcion) del cuerpo JSON del error; vacios si no lo trae."""
    try:
        cuerpo = resp.json()
    except ValueError:
        return "", ""
    if not isinstance(cuerpo, dict):
        return "", ""
    return str(cuerpo.get("error") or ""), str(cuerpo.get("error_description") or "")


def _post_token(payload: dict[str, str]) -> dict:
    """POST al endpoint de token probando los hosts conocidos en orden.

    Solo los codigos OAuth de ``_ERRORES_DEFINITIVOS`` cierran la sesion. Un
    403 de un proxy, un cuerpo vacio o un ``invalid_request`` se devuelven como
    fallo reintentable, y antes se prueba el otro host por si el problema era
    ese: perder el refresh token por un tropiezo pasajero es irreversible.
    """
    ultimo_error: Exception | None = None
    for url in _token_hosts():
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": config.USER_AGENT,
                },
                timeout=config.HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            ultimo_error = TransientError(f"{url} inalcanzable: {exc}")
            log.warning("Endpoint de token %s inalcanzable: %s", url, exc)
            continue

        if resp.ok:
            store.update_setting("token_url", url)
            return resp.json()

        # 404 / 405 = host equivocado: se prueba el siguiente.
        if resp.status_code in (404, 405):
            ultimo_error = TransientError(f"{url} respondio {resp.status_code}")
            continue
        if resp.status_code == 429:
            raise RateLimited(
                "El servidor está limitando las peticiones",
                parse_retry_after(resp.headers.get("Retry-After")),
            )
        if resp.status_code >= 500:
            ultimo_error = TransientError(f"El servidor no está disponible ({resp.status_code})")
            continue

        codigo, descripcion = _error_oauth(resp)
        if codigo in _ERRORES_DEFINITIVOS:
            raise AuthError(
                descripcion or f"El servidor invalidó la sesión ({codigo})", definitivo=True
            )
        # 4xx sin codigo OAuth reconocible: dudoso, se reintenta.
        ultimo_error = AuthError(
            f"El servidor rechazó la petición ({resp.status_code}): "
            f"{descripcion or resp.text[:200]}"
        )
        log.warning("Respuesta dudosa de %s: HTTP %s %s", url, resp.status_code, codigo or "")

    raise ultimo_error or TransientError("No se pudo contactar con el servidor de tokens")


def _to_credentials(data: dict, previas: dict | None = None) -> dict:
    access = data.get("access_token")
    if not access:
        raise AuthError("La respuesta del servidor no incluye access_token")
    expires_in = int(data.get("expires_in") or 3600)
    previas = previas or {}
    return {
        "access_token": access,
        # Si el servidor no rota el refresh token, se conserva el anterior.
        "refresh_token": data.get("refresh_token") or previas.get("refresh_token"),
        "expires_at": time.time() + expires_in,
        "scopes": data.get("scope") or previas.get("scopes") or config.SCOPES,
        "account": (data.get("account") or {}).get("email_address") or previas.get("account"),
        "subscription": data.get("subscription_type") or previas.get("subscription"),
    }


def exchange_code(flow: LoginFlow, codigo_pegado: str = "") -> dict:
    """Canjea el codigo por tokens.

    Usa el codigo que recogio el servidor local; si no hay servidor (o el
    usuario prefirio pegarlo), acepta el texto ``CODIGO#STATE``, el codigo
    suelto o incluso la URL completa de retorno.
    """
    codigo = ""
    state_final = flow.state

    recibido = flow.servidor.recibido() if flow.servidor else None
    if recibido and recibido[0]:
        codigo, estado = recibido
        if estado and estado != flow.state:
            raise AuthError("La respuesta del navegador no corresponde a este inicio de sesión")
    else:
        texto = (codigo_pegado or "").strip()
        if not texto:
            raise AuthError("Todavía no ha vuelto el navegador. Autoriza el acceso o pega el código.")
        if texto.startswith("http"):
            consulta = urllib.parse.parse_qs(urllib.parse.urlparse(texto).query)
            texto = (consulta.get("code") or [""])[0]
            if not texto:
                raise AuthError("La URL pegada no contiene ningún código")
        codigo, _, state_pegado = texto.partition("#")
        codigo = codigo.strip()
        if state_pegado.strip():
            if state_pegado.strip() != flow.state:
                raise AuthError("El código pegado no corresponde a esta ventana de inicio de sesión")
            state_final = state_pegado.strip()

    data = _post_token({
        "grant_type": "authorization_code",
        "code": codigo,
        "redirect_uri": flow.redirect_uri,
        "client_id": config.CLIENT_ID,
        "code_verifier": flow.verifier,
        "state": state_final,
    })
    creds = _to_credentials(data)
    if not store.save_credentials(creds):
        log.warning("Login correcto pero no se pudo guardar la sesión en disco")
    _parada.clear()
    flow.cerrar()
    return creds


def refresh(creds: dict) -> dict:
    """Renueva el access token, insistiendo si el fallo parece pasajero.

    Una renovacion ocurre cada ocho horas: merece la pena pelearla. Solo se
    rinde ante un rechazo definitivo del servidor, y nunca borra nada del
    disco: de eso decide la aplicacion.
    """
    with _LOCK:
        # Otro hilo pudo renovar mientras esperabamos el cerrojo. Con rotacion
        # de refresh token, volver a pedir aqui invalidaria el token recien
        # guardado, asi que se reutiliza el suyo.
        guardadas = store.load_credentials()
        if (guardadas and guardadas.get("access_token") != creds.get("access_token")
                and float(guardadas.get("expires_at") or 0) - time.time()
                > config.REFRESH_MARGIN_SECONDS):
            log.info("Otro hilo ya habia renovado el token; se reutiliza")
            return guardadas

        base = guardadas if (guardadas and guardadas.get("refresh_token")) else creds
        refresh_token = base.get("refresh_token")
        if not refresh_token:
            raise AuthError("No hay refresh token guardado", definitivo=True)

        ultimo: Exception | None = None
        for espera in (0, *config.AUTH_RETRY_WAITS):
            if espera:
                log.info("Reintentando la renovación del token en %ss", espera)
                if _parada.wait(espera):
                    break
            try:
                data = _post_token({
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": config.CLIENT_ID,
                })
            except AuthError as exc:
                if exc.definitivo:
                    raise
                ultimo = exc
                continue
            except RateLimited:
                # La espera la gestiona el poller, que sabe interrumpirla.
                raise
            except TransientError as exc:
                ultimo = exc
                continue

            nuevas = _to_credentials(data, previas=base)
            store.save_credentials(nuevas)
            log.info("Token renovado; caduca en %.0f min",
                     (nuevas["expires_at"] - time.time()) / 60)
            return nuevas

        raise ultimo or TransientError("No se pudo renovar el token")


def ensure_fresh(creds: dict, forzar: bool = False) -> dict:
    """Devuelve credenciales con access token valido, renovando si hace falta.

    El margen es amplio (15 min) a proposito: renovar con tiempo de sobra deja
    hueco para reintentar si el primer intento falla, en vez de descubrir que
    el token ha caducado con un 401 en la cara.
    """
    caduca = float(creds.get("expires_at") or 0)
    if forzar or caduca - time.time() < config.REFRESH_MARGIN_SECONDS:
        return refresh(creds)
    return creds
