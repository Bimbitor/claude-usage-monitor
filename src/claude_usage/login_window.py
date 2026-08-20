"""Ventana de inicio de sesion (primer arranque en cada equipo)."""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk

from . import auth, config, winutil

log = logging.getLogger(__name__)

_ESPERA_MS = 500      # cada cuanto se mira si el navegador ya volvio
_CADUCA_SEGUNDOS = 300  # tiempo maximo esperando al navegador


class LoginWindow:
    """Abre el navegador y espera a que vuelva solo.

    El servidor local recoge el codigo sin que el usuario copie nada; el campo
    de pegado queda como respaldo por si el navegador no puede volver (otro
    equipo, sesion remota, cortafuegos).
    """

    def __init__(self, root: tk.Tk, on_success, on_cancel):
        self.root = root
        self.on_success = on_success
        self.on_cancel = on_cancel
        self.flow: auth.LoginFlow | None = None
        self._espera_id: str | None = None
        self._esperando_desde = 0.0

        self.s = winutil.ui_scale()
        self.win = tk.Toplevel(root)
        self.win.title(f"{config.APP_TITLE} · iniciar sesión")
        self.win.configure(bg=config.COLOR_BG)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._cancelar)

        self._construir()
        self._centrar()
        self.win.lift()
        self.win.focus_force()

    # --- construccion ----------------------------------------------------
    def _px(self, v: float) -> int:
        return int(round(v * self.s))

    def _fuente(self, tam: int, negrita: bool = False) -> tuple:
        return ("Segoe UI", -self._px(tam), "bold" if negrita else "normal")

    def _etiqueta(self, texto, tam=11, color=config.COLOR_TEXT, negrita=False, **kw):
        return tk.Label(self.win, text=texto, bg=config.COLOR_BG, fg=color,
                        font=self._fuente(tam, negrita), justify="left",
                        anchor="w", **kw)

    def _boton(self, texto, comando, principal=False):
        return tk.Button(
            self.win, text=texto, command=comando,
            bg=config.COLOR_ACCENT if principal else config.COLOR_TRACK,
            fg="#ffffff" if principal else config.COLOR_TEXT,
            activebackground=config.COLOR_ACCENT if principal else config.COLOR_BORDER,
            activeforeground="#ffffff",
            font=self._fuente(11, principal), relief="flat", bd=0,
            padx=self._px(14), pady=self._px(7), cursor="hand2",
        )

    def _construir(self) -> None:
        pad = self._px(18)
        ancho = self._px(430)

        self._etiqueta("Conecta tu cuenta de Claude", tam=14, negrita=True,
                       color=config.COLOR_ACCENT).pack(
            fill="x", padx=pad, pady=(pad, self._px(4)))
        self._etiqueta(
            "Solo hace falta una vez en este equipo. La sesión queda guardada\n"
            "y cifrada para tu usuario de Windows.",
            tam=10, color=config.COLOR_MUTED,
        ).pack(fill="x", padx=pad)

        tk.Frame(self.win, bg=config.COLOR_BORDER, height=1).pack(
            fill="x", padx=pad, pady=self._px(14))

        self._etiqueta("Inicia sesión en el navegador", tam=11, negrita=True).pack(
            fill="x", padx=pad)
        self._etiqueta(
            "Se abrirá Claude. Autoriza el acceso y la ventana volverá sola:\n"
            "no tienes que copiar nada.",
            tam=10, color=config.COLOR_MUTED,
        ).pack(fill="x", padx=pad, pady=(self._px(2), self._px(8)))
        self.btn_abrir = self._boton("Abrir navegador", self._abrir, principal=True)
        self.btn_abrir.pack(anchor="w", padx=pad)

        self.enlace = tk.Entry(
            self.win, bg=config.COLOR_TRACK, fg=config.COLOR_MUTED,
            font=self._fuente(9), relief="flat", bd=0, insertbackground=config.COLOR_TEXT,
        )
        self.enlace.pack(fill="x", padx=pad, pady=(self._px(8), 0), ipady=self._px(4))
        self.enlace.insert(0, "El enlace aparecerá aquí por si tienes que abrirlo a mano")
        self.enlace.configure(state="readonly", readonlybackground=config.COLOR_TRACK)

        self._etiqueta("¿El navegador no volvió solo? Pega aquí el código", tam=10,
                       color=config.COLOR_MUTED).pack(
            fill="x", padx=pad, pady=(self._px(16), self._px(6)))
        self.codigo = tk.Entry(
            self.win, bg=config.COLOR_TRACK, fg=config.COLOR_TEXT,
            font=self._fuente(11), relief="flat", bd=0,
            insertbackground=config.COLOR_TEXT, width=48,
        )
        self.codigo.pack(fill="x", padx=pad, ipady=self._px(6))
        self.codigo.bind("<Return>", lambda _e: self._conectar())

        self.estado = self._etiqueta("", tam=10, color=config.COLOR_MUTED)
        self.estado.pack(fill="x", padx=pad, pady=(self._px(10), 0))

        pie = tk.Frame(self.win, bg=config.COLOR_BG)
        pie.pack(fill="x", padx=pad, pady=(self._px(12), pad))
        self.btn_conectar = self._boton("Conectar", self._conectar, principal=True)
        self.btn_conectar.pack(in_=pie, side="right")
        self._boton("Cancelar", self._cancelar).pack(in_=pie, side="right",
                                                     padx=(0, self._px(8)))

        self.win.update_idletasks()
        self.win.minsize(ancho, self.win.winfo_reqheight())

    def _centrar(self) -> None:
        self.win.update_idletasks()
        izq, arriba, der, abajo = winutil.work_area()
        x = izq + ((der - izq) - self.win.winfo_reqwidth()) // 2
        y = arriba + ((abajo - arriba) - self.win.winfo_reqheight()) // 3
        self.win.geometry(f"+{max(izq, x)}+{max(arriba, y)}")

    # --- acciones --------------------------------------------------------
    def _mostrar_estado(self, texto: str, color: str = config.COLOR_MUTED) -> None:
        self.estado.configure(text=texto, fg=color)

    def _abrir(self) -> None:
        self._cerrar_flow()
        self.flow = auth.start_login()

        self.enlace.configure(state="normal")
        self.enlace.delete(0, "end")
        self.enlace.insert(0, self.flow.url)
        self.enlace.configure(state="readonly")

        if auth.open_browser(self.flow.url):
            self._mostrar_estado("Esperando a que autorices el acceso en el navegador…")
        else:
            self._mostrar_estado("No se pudo abrir el navegador: copia el enlace de arriba.",
                                 config.COLOR_WARN)

        if self.flow.servidor:
            self._esperando_desde = time.monotonic()
            self._vigilar_callback()

    def _vigilar_callback(self) -> None:
        """Comprueba periodicamente si el navegador ya devolvio el codigo."""
        if self.flow is None or self.flow.servidor is None:
            return
        if self.flow.servidor.recibido():
            self._mostrar_estado("Código recibido, conectando…")
            self._lanzar_canje("")
            return
        if time.monotonic() - self._esperando_desde > _CADUCA_SEGUNDOS:
            self._mostrar_estado("Se agotó la espera. Vuelve a pulsar «Abrir navegador».",
                                 config.COLOR_WARN)
            return
        self._espera_id = self.win.after(_ESPERA_MS, self._vigilar_callback)

    def _conectar(self) -> None:
        if self.flow is None:
            self._mostrar_estado("Primero pulsa «Abrir navegador».", config.COLOR_WARN)
            return
        self._lanzar_canje(self.codigo.get().strip())

    def _lanzar_canje(self, texto: str) -> None:
        self.btn_conectar.configure(state="disabled")
        self._mostrar_estado("Conectando…")
        # El canje de tokens es una llamada de red: fuera del hilo de la interfaz.
        threading.Thread(target=self._canjear, args=(texto,), daemon=True).start()

    def _canjear(self, texto: str) -> None:
        try:
            creds = auth.exchange_code(self.flow, texto)
        except Exception as exc:
            log.warning("Fallo el canje del codigo: %s", exc)
            self.root.after(0, lambda: self._fallo(str(exc)))
            return
        self.root.after(0, lambda: self._exito(creds))

    def _fallo(self, mensaje: str) -> None:
        self.btn_conectar.configure(state="normal")
        self._mostrar_estado(mensaje, config.COLOR_CRIT)

    def _exito(self, creds: dict) -> None:
        self._mostrar_estado("¡Listo!", config.COLOR_OK)
        self._cerrar_flow()
        self.win.destroy()
        self.on_success(creds)

    def _cancelar(self) -> None:
        self._cerrar_flow()
        self.win.destroy()
        self.on_cancel()

    def _cerrar_flow(self) -> None:
        if self._espera_id:
            try:
                self.win.after_cancel(self._espera_id)
            except tk.TclError:
                pass
            self._espera_id = None
        if self.flow:
            self.flow.cerrar()
