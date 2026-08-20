"""Genera assets/app.ico a partir del dibujo vectorial de icons.py."""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from claude_usage import icons  # noqa: E402

destino = RAIZ / "assets" / "app.ico"
destino.parent.mkdir(parents=True, exist_ok=True)
icons.write_ico(str(destino))
print(f"Icono generado: {destino}")
