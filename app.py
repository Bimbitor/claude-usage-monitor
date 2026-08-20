"""Lanzador para PyInstaller y para ejecutar la app desde el codigo fuente.

    python app.py            # normal
    python app.py --demo     # datos de ejemplo, sin iniciar sesion
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from claude_usage.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
