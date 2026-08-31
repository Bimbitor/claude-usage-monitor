"""Comprueba el flujo OAuth de punta a punta sin abrir la interfaz.

    python tools/auth_probe.py             # login completo (abre el navegador)
    python tools/auth_probe.py --url-only  # solo imprime la URL y espera el retorno
    python tools/auth_probe.py --hosts     # que hosts de token responden
    python tools/auth_probe.py --tls       # diagnostico de verificacion TLS
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests  # noqa: E402

from claude_usage import api, auth, config, netconf, store  # noqa: E402

# Misma configuracion TLS que la app: sin esto, en redes que inspeccionan HTTPS
# la sonda falla donde la app funciona (o al reves).
netconf.configurar_tls()

ESPERA_MAX = 300


def probar_hosts() -> None:
    """Distingue el host correcto (400 ante un codigo falso) del erroneo (404)."""
    print("Probando endpoints de token con un codigo invalido a proposito:\n")
    for url in config.TOKEN_URLS:
        try:
            resp = requests.post(
                url,
                json={
                    "grant_type": "authorization_code",
                    "code": "codigo-de-prueba-invalido",
                    "redirect_uri": config.REDIRECT_URI_MANUAL,
                    "client_id": config.CLIENT_ID,
                    "code_verifier": "x" * 43,
                    "state": "y" * 22,
                },
                headers={"Content-Type": "application/json", "User-Agent": config.USER_AGENT},
                timeout=20,
            )
        except requests.RequestException as exc:
            print(f"  {url}\n    inalcanzable: {exc}\n")
            continue
        veredicto = "NO existe (host equivocado)" if resp.status_code in (404, 405) \
            else "existe y responde"
        print(f"  {url}\n    HTTP {resp.status_code} -> {veredicto}")
        print(f"    cuerpo: {resp.text[:200]}\n")


def diagnostico_tls() -> None:
    """Muestra si requests puede validar los servidores y quien firma su cert."""
    import socket
    import ssl

    hosts = [config.USAGE_URL.split("/")[2], *[u.split("/")[2] for u in config.TOKEN_URLS]]
    print("Certificado que presenta cada servidor (así se detecta un proxy TLS):\n")
    for host in dict.fromkeys(hosts):
        try:
            with socket.create_connection((host, 443), timeout=15) as raw:
                with ssl.create_default_context().wrap_socket(
                    raw, server_hostname=host
                ) as tls:
                    emisor = dict(x[0] for x in tls.getpeercert()["issuer"])
            print(f"  {host}\n    firmado por: {emisor.get('organizationName')} / "
                  f"{emisor.get('commonName')}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {host}\n    no se pudo leer el certificado: {exc}")

    print("\n¿Puede requests validar los endpoints de token?")
    for url in config.TOKEN_URLS:
        try:
            requests.head(url, timeout=15)
            print(f"  {url}\n    OK")
        except requests.exceptions.SSLError as exc:
            print(f"  {url}\n    FALLA la verificación TLS: {str(exc)[:160]}")
        except requests.RequestException as exc:
            print(f"  {url}\n    (sin verificar) otro error de red: {str(exc)[:120]}")


def login_completo(abrir: bool = True) -> int:
    flow = auth.start_login()
    print("1) Abre esta URL, inicia sesión y autoriza:\n")
    print(f"   {flow.url}\n")
    print(f"   (retorno: {flow.redirect_uri})\n")
    if abrir:
        auth.open_browser(flow.url)

    if flow.servidor is None:
        codigo = input("2) Pega aquí el código que muestra el navegador: ").strip()
    else:
        print("2) Esperando a que el navegador vuelva…", flush=True)
        inicio = time.monotonic()
        while flow.servidor.recibido() is None:
            if time.monotonic() - inicio > ESPERA_MAX:
                print("   Se agotó la espera.")
                flow.cerrar()
                return 1
            time.sleep(0.5)
        print("   Código recibido.")
        codigo = ""

    try:
        creds = auth.exchange_code(flow, codigo)
    except Exception as exc:
        print(f"\nFALLO el canje del código: {exc}")
        return 1

    print("\nTokens obtenidos correctamente:")
    print(f"   access_token  : {creds['access_token'][:12]}… ({len(creds['access_token'])} car.)")
    print(f"   refresh_token : {'sí' if creds.get('refresh_token') else 'NO'}")
    print(f"   caduca en     : {(creds['expires_at'] - time.time()) / 3600:.1f} h")
    print(f"   host de token : {store.load_settings().get('token_url')}")

    print("\n3) Consultando /api/oauth/usage…")
    snapshot = api.UsageClient(creds).fetch()
    for limite in snapshot.limites:
        print(f"   {api.TITULOS.get(limite.etiqueta, limite.etiqueta):<16} "
              f"{limite.porcentaje:5.1f} %   reset en {config.human_delta(limite.restante)}"
              f"   ({config.local_stamp(limite.resets_at)})")

    print("\n4) Probando renovación con el refresh token…")
    try:
        nuevas = auth.refresh(creds)
        print(f"   OK, nuevo token válido {(nuevas['expires_at'] - time.time()) / 3600:.1f} h")
    except Exception as exc:
        print(f"   FALLO al renovar: {exc}")
        return 1

    print("\nTodo correcto. Credenciales guardadas en", config.credentials_file())
    return 0


if __name__ == "__main__":
    if "--tls" in sys.argv:
        diagnostico_tls()
        sys.exit(0)
    if "--hosts" in sys.argv:
        probar_hosts()
        sys.exit(0)
    sys.exit(login_completo(abrir="--url-only" not in sys.argv))
