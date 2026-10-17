# QA handoff: Windows Qt startup failure

## Current checkout

- Repository: `derkeshtai/digitalbookreader`
- Comparison branch: `codex/fix-pyside6-icu-runtime`
- Base branch: `arena/331a3b5b-digitalbookreader`
- Base commit: `c32f2fe` (`feat: add desktop reader MVP and expanded formats`)
- The comparison branch is intentionally uncommitted. Its working tree contains `DigitalBookReader.spec` and this note; no application source files were changed.

## Reported problem and diagnosis

The packaged Windows executable showed an unhandled exception while importing `PySide6.QtWidgets`: `DLL load failed ... No se encontró el proceso especificado` (WinError 127). The traceback reached `src/app.py` during the QtWidgets import.

During QA, PyInstaller picked up ICU 78 DLLs from an unrelated tool on `PATH`. This PySide6 distribution uses Qt 6.12.0, which needs the Windows ICU 72 DLLs. The unrelated ICU DLLs caused Qt's core DLL load to fail. The spec file filters the incompatible ICU files from PyInstaller's collected binaries and explicitly puts `icu.dll`, `icuin.dll`, and `icuuc.dll` from Windows `System32` beside the PySide6 runtime. It also explicitly includes the Visual C++ runtime DLLs used by the build.

## What was checked

- Python 3.12.14 virtual environment; project requirements installed in `.venv`.
- `python -m compileall -q src tests run.py`: passed.
- `python -m unittest discover -v`: 17 tests passed.
- `python -m pip check`: passed.
- PyInstaller onedir build completed. The produced folder was about 647 MB; keep `_internal` alongside the executable.
- Imported bundled `PySide6.QtWidgets` and created a `QApplication` in offscreen mode: passed; Qt reported 6.12.0.
- Full GUI verification is still outstanding. The full executable exited with code `-2147483645` in the isolated session, and desktop-window inspection was unavailable. Do not treat the library import smoke test as proof that the reader UI works end to end.

## Rebuild and inspect

Use Python 3.10+ and install the repository's `requirements.txt` into a virtual environment. Build the Windows onedir distribution with PyInstaller using this spec file:

```powershell
python -m PyInstaller --noconfirm --clean DigitalBookReader.spec
```

Then test `dist\DigitalBookReader\DigitalBookReader.exe` on a normal Windows desktop. Verify the main window opens and check file open, EPUB navigation/TOC, search, bookmarks and reading-progress restoration. If the problem persists, inspect the loaded ICU DLL paths and PyInstaller's `warn-DigitalBookReader.txt` before changing the workaround.

The prior QA report and build artifacts are in the task's `outputs/digitalbookreader-qa` and `outputs/digitalbookreader-windows` folders. The disposable EPUB used in QA is under `work/qa-temp-f008ce6d12ac455ea8bb85a3b96408b8/`; do not substitute any personal library files.
