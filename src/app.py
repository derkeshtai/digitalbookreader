"""Desktop application entry point (run with ``python -m src.app``)."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.main_window import ReaderMainWindow


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Digital Book Reader")
    app.setOrganizationName("Digital Book Reader")
    app.setApplicationDisplayName("Digital Book Reader")

    window = ReaderMainWindow()
    window.show()
    if args:
        initial_path = str(Path(args[0]).expanduser())
        window.import_files([initial_path], open_first=True)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
