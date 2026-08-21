"""Panel emergente que aparece sobre la bandeja al pulsar el icono."""

from __future__ import annotations

import time
import tkinter as tk

from . import api, config, winutil


class UsagePanel:
    """Ventana sin bordes anclada a la esquina inferior derecha del escritorio.

    Todo se pinta en un Canvas: evita el theming de ttk, permite barras y
    botones con el mismo aspecto en cualquier version de Windows y mantiene el
    foco en la propia ventana (necesario para ocultarla al perderlo).
    """

    def __init__(self, root: tk.Tk, on_refresh, on_login=None, sesion_caducada=None):
        self.root = root
        self.on_refresh = on_refresh
        self.on_login = on_login
        self.sesion_caducada = sesion_caducada or (lambda: False)
        self.snapshot: api.UsageSnapshot | None = None
        self.visible = False
        # Hasta cuando el poller no va a preguntar nada (antirrebote o 429).
        self._veto_hasta = 0.0
        self._tick_id: str | None = None
        self._abierto_en = 0.0
        self._zonas: list[tuple[tuple[int, int, int, int], object]] = []

        self.s = winutil.ui_scale()
        self.ancho = self._px(340)

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=config.COLOR_BORDER)

        self.canvas = tk.Canvas(
            self.win, bg=config.COLOR_BG, highlightthickness=0, bd=0,
            width=self.ancho - self._px(2), height=self._px(200),
        )
        self.canvas.pack(padx=self._px(1), pady=self._px(1))

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.win.bind("<Escape>", lambda _e: self.hide())
        self.win.bind("<FocusOut>", self._on_focus_out)

    # --- utilidades de dibujo -------------------------------------------
    def _px(self, valor: float) -> int:
        return int(round(valor * self.s))

    def _fuente(self, tam: int, negrita: bool = False) -> tuple:
        return ("Segoe UI", -self._px(tam), "bold" if negrita else "normal")

    def _texto(self, x, y, texto, tam=12, color=config.COLOR_TEXT, negrita=False, ancla="nw"):
        return self.canvas.create_text(
            x, y, text=texto, fill=color, font=self._fuente(tam, negrita), anchor=ancla
        )

    # --- ciclo de vida ---------------------------------------------------
    def set_snapshot(self, snapshot: api.UsageSnapshot) -> None:
        self.snapshot = snapshot
        if self.visible:
            self._redraw()

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def show(self) -> None:
        # Con un sondeo de cinco minutos, abrir el panel es la senal de que
        # ahora si interesa el dato al segundo.
        if (self.snapshot is not None and not self.sesion_caducada()
                and self.snapshot.edad > config.PANEL_STALE_SECONDS):
            self._pedir_refresco()
        self._redraw()
        self._position()
        self.win.deiconify()
        self.win.lift()
        try:
            self.win.focus_force()
        except tk.TclError:
            pass
        self.visible = True
        self._abierto_en = time.monotonic()
        self._schedule_tick()

    def hide(self) -> None:
        self.visible = False
        if self._tick_id:
            self.win.after_cancel(self._tick_id)
            self._tick_id = None
        self.win.withdraw()

    def _on_focus_out(self, _evento) -> None:
        # Se oculta al hacer clic fuera, como cualquier menu de la bandeja.
        # Windows puede no conceder el foco de inmediato al abrirlo desde la
        # bandeja: se ignora el FocusOut que llega nada mas mostrarlo, o el
        # panel se cerraria solo.
        if self.visible and time.monotonic() - self._abierto_en > 0.4:
            self.hide()

    def _schedule_tick(self) -> None:
        # Las cuentas atras se refrescan cada segundo sin volver a llamar a la API.
        self._tick_id = self.win.after(1000, self._tick)

    def _tick(self) -> None:
        if not self.visible:
            return
        self._redraw()
        self._schedule_tick()

    def _position(self) -> None:
        izq, arriba, der, abajo = winutil.work_area()
        self.win.update_idletasks()
        ancho = self.win.winfo_reqwidth()
        alto = self.win.winfo_reqheight()
        margen = self._px(10)
        x = max(izq + margen, der - ancho - margen)
        y = max(arriba + margen, abajo - alto - margen)
        self.win.geometry(f"+{x}+{y}")

    # --- pintado ---------------------------------------------------------
    def _redraw(self) -> None:
        self.canvas.delete("all")
        self._zonas.clear()

        pad = self._px(16)
        ancho_util = self.ancho - self._px(2) - pad * 2
        y = pad
        snap = self.snapshot

        # Cabecera
        self._texto(pad, y, "Claude", tam=13, color=config.COLOR_ACCENT, negrita=True)
        estado, color_estado = self._estado()
        punto = self._px(4)
        cy = y + self._px(8)
        x_fin = pad + ancho_util
        ancho_estado = self._texto(x_fin, y + self._px(1), estado, tam=10,
                                   color=config.COLOR_MUTED, ancla="ne")
        bbox = self.canvas.bbox(ancho_estado)
        self.canvas.create_oval(bbox[0] - self._px(12), cy - punto,
                                bbox[0] - self._px(12) + punto * 2, cy + punto,
                                fill=color_estado, outline="")
        y += self._px(26)

        if snap is None or not snap.limites:
            self._texto(pad, y, "Sin datos todavía…", tam=12, color=config.COLOR_MUTED)
            y += self._px(24)
        else:
            for limite in snap.limites:
                y = self._fila(limite, pad, y, ancho_util)

        aviso = self._aviso()
        if aviso:
            self._texto(pad, y, aviso, tam=10, color=config.COLOR_WARN)
            y += self._px(18)

        # Pie
        y += self._px(2)
        self.canvas.create_line(pad, y, pad + ancho_util, y, fill=config.COLOR_BORDER)
        y += self._px(12)
        self._boton(pad, y, "Actualizar", self._pulsar_refresh)
        # Solo se ofrece volver a entrar cuando la sesion ha caducado de
        # verdad: con un 429 o un corte de red la sesion sigue viva y
        # proponer un login nuevo solo consigue que se pierda.
        if self.sesion_caducada() and self.on_login:
            self._boton(pad + ancho_util, y, "Iniciar sesión", self._pulsar_login, ancla="ne")
        else:
            self._boton(pad + ancho_util, y, "Cerrar", lambda: self.hide(), ancla="ne")
        y += self._px(20) + pad

        alto = y
        self.canvas.configure(width=ancho_util + pad * 2, height=alto)
        if self.visible:
            self._position()

    def _fila(self, limite: api.Limite, pad: int, y: int, ancho_util: int) -> int:
        """Una fila: título, porcentaje, barra y cuenta atrás."""
        titulo = api.TITULOS.get(limite.etiqueta, limite.etiqueta)
        color = config.COLOR_STALE if (self.snapshot and self.snapshot.stale) \
            else config.severity_color(limite.porcentaje)

        self._texto(pad, y, titulo, tam=12)
        self._texto(pad + ancho_util, y, f"{limite.porcentaje:.0f} %", tam=12,
                    negrita=True, color=color, ancla="ne")
        y += self._px(20)

        alto_barra = self._px(9)
        self.canvas.create_rectangle(pad, y, pad + ancho_util, y + alto_barra,
                                     fill=config.COLOR_TRACK, outline="")
        relleno = int(ancho_util * min(100.0, limite.porcentaje) / 100.0)
        if relleno > 0:
            self.canvas.create_rectangle(pad, y, pad + max(relleno, self._px(3)),
                                         y + alto_barra, fill=color, outline="")
        y += alto_barra + self._px(6)

        prefijo = "se reinicia en" if limite.etiqueta == "sesion" else "corte en"
        self._texto(pad, y, f"{prefijo} {config.human_delta(limite.restante)}",
                    tam=10, color=config.COLOR_MUTED)
        self._texto(pad + ancho_util, y, config.local_stamp(limite.resets_at),
                    tam=10, color=config.COLOR_MUTED, ancla="ne")
        return y + self._px(16) + self._px(12)

    def _boton(self, x: int, y: int, etiqueta: str, accion, ancla: str = "nw") -> None:
        item = self._texto(x, y, etiqueta, tam=11, color=config.COLOR_TEXT, ancla=ancla)
        bbox = self.canvas.bbox(item)
        holgura = self._px(6)
        zona = (bbox[0] - holgura, bbox[1] - holgura, bbox[2] + holgura, bbox[3] + holgura)
        self.canvas.create_rectangle(*zona, outline=config.COLOR_BORDER)
        self._zonas.append((zona, accion))

    def _estado(self) -> tuple[str, str]:
        snap = self.snapshot
        if self.sesion_caducada():
            return "sesión caducada", config.COLOR_CRIT
        if snap is None:
            return "conectando…", config.COLOR_MUTED
        if snap.rate_limited:
            return "servidor ocupado", config.COLOR_WARN
        if snap.error:
            return "sin conexión", config.COLOR_CRIT
        if snap.stale:
            return config.human_age(snap.edad), config.COLOR_WARN
        return f"actualizado {config.human_age(snap.edad)}", config.COLOR_OK

    def _aviso(self) -> str:
        """Linea explicativa bajo los limites: que pasa y cuando se reintenta."""
        snap = self.snapshot
        if self.sesion_caducada():
            return "La sesión ha caducado. Pulsa «Iniciar sesión» para volver a entrar."
        restante = self._veto_hasta - time.monotonic()
        if snap is not None and snap.rate_limited:
            if restante > 0:
                return ("El servidor limita las consultas · reintento en "
                        f"{config.human_espera(restante)}")
            return "El servidor limita las consultas · reintentando…"
        if restante > 0:
            return f"Siguiente consulta en {config.human_espera(restante)}"
        if snap is not None and snap.error:
            return snap.error
        return ""

    # --- interaccion -----------------------------------------------------
    def _zona_en(self, x: int, y: int):
        for (x0, y0, x1, y1), accion in self._zonas:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return accion
        return None

    def _on_click(self, evento) -> None:
        accion = self._zona_en(evento.x, evento.y)
        if accion:
            accion()

    def _on_motion(self, evento) -> None:
        self.canvas.configure(cursor="hand2" if self._zona_en(evento.x, evento.y) else "")

    def _pulsar_refresh(self) -> None:
        self._pedir_refresco()
        self._redraw()

    def _pedir_refresco(self) -> None:
        """Pide dato fresco; si el poller esta en veto, se anota la cuenta atras."""
        restante = self.on_refresh() or 0.0
        self._veto_hasta = time.monotonic() + restante
        if restante <= 0 and self.snapshot is not None and not self.snapshot.rate_limited:
            self.snapshot.error = None

    def _pulsar_login(self) -> None:
        self.hide()
        if self.on_login:
            self.on_login()
