"""Build the standalone Windows executable: uv run --group build build.py."""
from pathlib import Path
import os

import PyInstaller.__main__

root = Path(__file__).resolve().parent
os.chdir(root)
if not (root / ".env").exists():
    raise SystemExit("Create desktop/.env with the API server address before building.")

PyInstaller.__main__.run([
    "main.py", "--name", "ProZapas", "--onefile", "--windowed", "--noconfirm",
    "--icon", str(root / "app" / "assets" / "prozapas.ico"),
    "--add-data", f"{root / 'app' / 'assets'}:app/assets",
    "--add-data", f"{root / '.env'}:.",
    "--specpath", str(root / "build"),
    "--distpath", str(root / "dist"),
    "--workpath", str(root / "build" / "work"),
    "--hidden-import", "win32timezone",
])
