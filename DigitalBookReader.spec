# PyInstaller build for the Windows desktop application.
from pathlib import Path
import os
import sys

project_root = Path(SPECPATH).resolve()
pyside_dir = Path(sys.prefix) / "Lib" / "site-packages" / "PySide6"
system32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"

# Qt6Core 6.12 on this Windows build uses the Windows ICU 72 shims. PyInstaller
# can otherwise collect an unrelated ICU 78 from another application on PATH;
# Windows then loads the wrong ICU and QtWidgets fails with WinError 127.
wrong_icu_names = {"icuuc.dll", "icudt78.dll", "icuin78.dll", "icu.dll", "icuin.dll"}
windows_icu_names = ("icu.dll", "icuin.dll", "icuuc.dll")
windows_icu_sources = {str((system32 / name).resolve()).lower() for name in windows_icu_names}
vc_runtime_names = (
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_codecvt_ids.dll",
    "vccorlib140.dll",
    "vcomp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)

binaries = [(str(pyside_dir / name), "PySide6") for name in vc_runtime_names]
binaries += [(str(system32 / name), "PySide6") for name in windows_icu_names]
missing = [path for path, _ in binaries if not Path(path).is_file()]
if missing:
    raise FileNotFoundError("Required Qt runtime DLLs are missing: " + ", ".join(missing))

a = Analysis(
    [str(project_root / "run.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Drop ICU versions picked up from unrelated PATH entries, but retain the
# exact Windows System32 ICU files listed explicitly above.
a.binaries = [
    entry for entry in a.binaries
    if (
        Path(entry[0]).name.lower() not in wrong_icu_names
        or str(Path(entry[1]).resolve()).lower() in windows_icu_sources
    )
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DigitalBookReader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DigitalBookReader",
)
