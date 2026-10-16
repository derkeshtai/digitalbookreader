"""Locate an UnRAR executable for the `rarfile` package.

CBR files are RAR archives; rarfile needs an external unarchiver. We prefer a
bundled copy (tools/UnRAR.exe, shipped inside _internal/tools in the exe) so
the app works on machines without WinRAR, then fall back to system installs.
"""
import sys
from pathlib import Path

_configured = False


def _candidates() -> list[Path]:
    cands: list[Path] = []
    if getattr(sys, "frozen", False):  # PyInstaller onedir build
        base = Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
        cands.append(base / "tools" / "UnRAR.exe")
    else:
        root = Path(__file__).resolve().parents[3]  # repo root
        cands.append(root / "tools" / "UnRAR.exe")
    for p in (r"C:\Program Files\WinRAR\UnRAR.exe",
              r"C:\Program Files (x86)\WinRAR\UnRAR.exe"):
        cands.append(Path(p))
    return cands


def configure_unrar() -> None:
    """Point rarfile at the best available UnRAR tool (idempotent)."""
    global _configured
    if _configured:
        return
    import rarfile
    for cand in _candidates():
        if cand.is_file():
            rarfile.UNRAR_TOOL = str(cand)
            break
    _configured = True
