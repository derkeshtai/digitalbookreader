"""Minimal Windows desktop reader: library, WebEngine view, TOC, search and bookmarks."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.book_provider import Book, BookProvider
from ..core.formats import SUPPORTED_EXTS, BookContent, parse
from ..core.page_server import get_page_server
from ..core.settings import Settings

_LOCATION_SCRIPT = r"""(() => {
    const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    const ratio = maxScroll > 0 ? window.scrollY / maxScroll : 0;
    const sections = Array.from(document.querySelectorAll('.comic section[id], section[id]'));
    let active = null;
    const threshold = window.innerHeight * 0.45;
    for (const section of sections) {
        if (section.getBoundingClientRect().top <= threshold) active = section;
        else break;
    }
    return { anchor: active ? active.id : '', ratio: Math.max(0, Math.min(1, ratio)) };
})()"""


class _ImportWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object, object)

    def __init__(self, paths: list[str], db_path: Path) -> None:
        super().__init__()
        self.paths = paths
        self.db_path = db_path

    @Slot()
    def run(self) -> None:
        imported: list[int] = []
        errors: list[tuple[str, str]] = []
        try:
            provider = BookProvider(self.db_path)
        except (OSError, sqlite3.Error, ValueError) as exc:
            errors.append((str(self.db_path), f"No se pudo abrir la biblioteca: {exc}"))
            self.finished.emit(imported, errors)
            return

        try:
            for index, source in enumerate(self.paths, start=1):
                self.progress.emit(index - 1, len(self.paths), Path(source).name)
                content = None
                try:
                    content = parse(source)
                    cover = None
                    if content.cover_path and content.cover_path.is_file():
                        try:
                            if content.cover_path.stat().st_size <= 4 * 1024 * 1024:
                                cover = content.cover_path.read_bytes()
                        except OSError:
                            pass
                    book_id = provider.add_book(
                        source,
                        title=content.title,
                        author=content.author,
                        cover=cover,
                    )
                    imported.append(book_id)
                except Exception as exc:  # noqa: BLE001 - report one bad file and continue
                    errors.append((source, str(exc)))
                finally:
                    if content is not None and content.page_source is not None:
                        try:
                            content.page_source.close()
                        except Exception:  # noqa: BLE001, S110 - cleanup must not mask import results
                            pass
                self.progress.emit(index, len(self.paths), Path(source).name)
        finally:
            provider.close()
        self.finished.emit(imported, errors)


class _OpenWorker(QObject):
    finished = Signal(int, object, str)

    def __init__(self, book_id: int, path: str) -> None:
        super().__init__()
        self.book_id = book_id
        self.path = path

    @Slot()
    def run(self) -> None:
        try:
            content = parse(self.path)
        except Exception as exc:  # noqa: BLE001 - parser errors are displayed to the user
            self.finished.emit(self.book_id, None, str(exc))
        else:
            self.finished.emit(self.book_id, content, "")


class ReaderMainWindow(QMainWindow):
    def __init__(self, provider: BookProvider | None = None, settings: Settings | None = None) -> None:
        super().__init__()
        self.provider = provider or BookProvider()
        self.settings = settings or Settings()
        self.page_server = get_page_server()
        self.current_book: Book | None = None
        self.current_content: BookContent | None = None
        self._last_location: dict[str, Any] = {"anchor": "", "ratio": 0.0}
        self._restore_location: dict[str, Any] | None = None
        self._import_thread: QThread | None = None
        self._import_worker: _ImportWorker | None = None
        self._open_thread: QThread | None = None
        self._open_worker: _OpenWorker | None = None
        self._open_after_import = False
        self._shutting_down = False

        self.setWindowTitle("Digital Book Reader")
        self.resize(1280, 820)
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_actions()
        self._apply_application_style()
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(2500)
        self._progress_timer.timeout.connect(self._capture_progress)
        self.refresh_library()

    # -- interface ---------------------------------------------------------
    def _build_ui(self) -> None:
        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)
        self._build_library_page()
        self._build_reader_page()
        self.stack.addWidget(self.library_page)
        self.stack.addWidget(self.reader_page)
        self.stack.setCurrentWidget(self.library_page)

        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMaximumWidth(180)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()
        self.status.addPermanentWidget(self.progress_bar)
        self.status.showMessage("Listo")

    def _build_library_page(self) -> None:
        self.library_page = QWidget()
        layout = QVBoxLayout(self.library_page)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("Mi biblioteca")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.import_button = QPushButton("Agregar libros")
        self.import_button.clicked.connect(self.choose_books_to_import)
        self.open_file_button = QPushButton("Abrir archivo…")
        self.open_file_button.clicked.connect(self.choose_book_to_open)
        header.addWidget(self.import_button)
        header.addWidget(self.open_file_button)
        layout.addLayout(header)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Buscar en la biblioteca"))
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText("Título o autor…")
        self.library_search.textChanged.connect(self.refresh_library)
        search_row.addWidget(self.library_search, 1)
        layout.addLayout(search_row)

        self.library_table = QTableWidget(0, 5)
        self.library_table.setHorizontalHeaderLabels(["Título", "Autor", "Formato", "Avance", "Ruta"])
        self.library_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.library_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.library_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.library_table.setAlternatingRowColors(True)
        self.library_table.verticalHeader().setVisible(False)
        self.library_table.horizontalHeader().setStretchLastSection(True)
        self.library_table.setColumnWidth(0, 310)
        self.library_table.setColumnWidth(1, 210)
        self.library_table.setColumnWidth(2, 100)
        self.library_table.setColumnWidth(3, 100)
        self.library_table.cellDoubleClicked.connect(self._open_table_row)
        layout.addWidget(self.library_table, 1)

        actions = QHBoxLayout()
        self.open_selected_button = QPushButton("Leer libro")
        self.open_selected_button.clicked.connect(self.open_selected_book)
        self.remove_button = QPushButton("Quitar de la biblioteca")
        self.remove_button.clicked.connect(self.remove_selected_book)
        self.mark_read_button = QPushButton("Marcar como leído")
        self.mark_read_button.clicked.connect(self.mark_selected_read)
        actions.addWidget(self.open_selected_button)
        actions.addWidget(self.mark_read_button)
        actions.addStretch(1)
        actions.addWidget(self.remove_button)
        layout.addLayout(actions)

    def _build_reader_page(self) -> None:
        self.reader_page = QWidget()
        layout = QVBoxLayout(self.reader_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QToolBar("Lectura", self.reader_page)
        toolbar.setMovable(False)
        self.back_action = QAction("Biblioteca", self)
        self.back_action.triggered.connect(self.return_to_library)
        toolbar.addAction(self.back_action)
        toolbar.addSeparator()
        self.book_title_label = QLabel("Libro")
        self.book_title_label.setMinimumWidth(160)
        toolbar.addWidget(self.book_title_label)
        toolbar.addSeparator()
        self.previous_button = QPushButton("‹ Anterior")
        self.previous_button.clicked.connect(lambda: self._scroll_page(-1))
        self.next_button = QPushButton("Siguiente ›")
        self.next_button.clicked.connect(lambda: self._scroll_page(1))
        toolbar.addWidget(self.previous_button)
        toolbar.addWidget(self.next_button)
        toolbar.addSeparator()

        self.reader_search = QLineEdit()
        self.reader_search.setPlaceholderText("Buscar en el libro…")
        self.reader_search.setMaximumWidth(260)
        self.reader_search.returnPressed.connect(self.search_next)
        toolbar.addWidget(self.reader_search)
        self.search_previous_button = QPushButton("↑")
        self.search_previous_button.setToolTip("Coincidencia anterior")
        self.search_previous_button.clicked.connect(self.search_previous)
        self.search_next_button = QPushButton("↓")
        self.search_next_button.setToolTip("Coincidencia siguiente")
        self.search_next_button.clicked.connect(self.search_next)
        toolbar.addWidget(self.search_previous_button)
        toolbar.addWidget(self.search_next_button)
        toolbar.addSeparator()

        self.zoom_out_button = QPushButton("A−")
        self.zoom_out_button.clicked.connect(lambda: self._change_font_size(-1))
        self.zoom_in_button = QPushButton("A+")
        self.zoom_in_button.clicked.connect(lambda: self._change_font_size(1))
        self.theme_button = QPushButton("Tema")
        self.theme_button.clicked.connect(self._cycle_theme)
        toolbar.addWidget(self.zoom_out_button)
        toolbar.addWidget(self.zoom_in_button)
        toolbar.addWidget(self.theme_button)
        toolbar.addSeparator()

        self.bookmark_button = QPushButton("＋ Marcador")
        self.bookmark_button.clicked.connect(self.add_bookmark)
        toolbar.addWidget(self.bookmark_button)
        self.progress_label = QLabel("—")
        self.progress_label.setMinimumWidth(90)
        toolbar.addWidget(self.progress_label)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal, self.reader_page)
        self.sidebar = QTabWidget()
        self.sidebar.setMinimumWidth(230)
        self.sidebar.setMaximumWidth(380)

        self.toc_list = QListWidget()
        self.toc_list.itemClicked.connect(self._toc_clicked)
        self.sidebar.addTab(self.toc_list, "Índice")

        bookmark_panel = QWidget()
        bookmark_layout = QVBoxLayout(bookmark_panel)
        bookmark_layout.setContentsMargins(6, 6, 6, 6)
        self.bookmark_list = QListWidget()
        self.bookmark_list.itemDoubleClicked.connect(self._bookmark_clicked)
        bookmark_layout.addWidget(self.bookmark_list, 1)
        bookmark_actions = QHBoxLayout()
        add_bookmark = QPushButton("Añadir")
        add_bookmark.clicked.connect(self.add_bookmark)
        remove_bookmark = QPushButton("Eliminar")
        remove_bookmark.clicked.connect(self.remove_selected_bookmark)
        bookmark_actions.addWidget(add_bookmark)
        bookmark_actions.addWidget(remove_bookmark)
        bookmark_layout.addLayout(bookmark_actions)
        self.sidebar.addTab(bookmark_panel, "Marcadores")

        self.web = QWebEngineView()
        web_settings = self.web.settings()
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        web_settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        self.web.loadFinished.connect(self._reader_loaded)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.web)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([270, 1000])
        layout.addWidget(splitter, 1)

    def _build_actions(self) -> None:
        open_action = QAction("Abrir…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.choose_book_to_open)
        self.addAction(open_action)

        find_action = QAction("Buscar en el libro", self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self._focus_reader_search)
        self.addAction(find_action)

        bookmark_action = QAction("Añadir marcador", self)
        bookmark_action.setShortcut(QKeySequence("Ctrl+D"))
        bookmark_action.triggered.connect(self.add_bookmark)
        self.addAction(bookmark_action)

        library_action = QAction("Volver a la biblioteca", self)
        library_action.setShortcut(QKeySequence("Escape"))
        library_action.triggered.connect(self.return_to_library)
        self.addAction(library_action)

        next_action = QAction("Siguiente página", self)
        next_action.setShortcut(QKeySequence("PageDown"))
        next_action.triggered.connect(lambda: self._scroll_page(1))
        self.addAction(next_action)

        previous_action = QAction("Página anterior", self)
        previous_action.setShortcut(QKeySequence("PageUp"))
        previous_action.triggered.connect(lambda: self._scroll_page(-1))
        self.addAction(previous_action)

    def _apply_application_style(self) -> None:
        self.setStyleSheet("""
            QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
            QLabel#pageTitle { font-size: 22pt; font-weight: 600; }
            QTableWidget { gridline-color: #d8dce4; selection-background-color: #cfe2ff; }
            QHeaderView::section { padding: 7px; font-weight: 600; }
            QPushButton { padding: 6px 12px; }
            QLineEdit { padding: 7px; }
        """)

    # -- library -----------------------------------------------------------
    def _file_filter(self) -> str:
        patterns = " ".join(f"*{extension}" for extension in sorted(SUPPORTED_EXTS))
        return f"Libros compatibles ({patterns});;Todos los archivos (*)"

    def choose_books_to_import(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Agregar libros a la biblioteca", "", self._file_filter())
        if paths:
            self.import_files(paths)

    def choose_book_to_open(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Abrir un libro", "", self._file_filter())
        if paths:
            self.import_files(paths, open_first=True)

    def import_files(self, paths: list[str], open_first: bool = False) -> None:
        if self._import_thread and self._import_thread.isRunning():
            QMessageBox.information(self, "Importación en curso", "Espera a que termine la importación actual.")
            return
        normalized = list(dict.fromkeys(str(Path(path)) for path in paths))
        if not normalized:
            return

        self._open_after_import = open_first
        self.progress_bar.setRange(0, len(normalized))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status.showMessage(f"Preparando {len(normalized)} archivo(s)…")

        thread = QThread(self)
        worker = _ImportWorker(normalized, self.provider.db_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._import_progress)
        worker.finished.connect(self._import_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_import_worker)
        self._import_thread = thread
        self._import_worker = worker
        thread.start()

    def _import_progress(self, current: int, total: int, filename: str) -> None:
        if self._shutting_down:
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.status.showMessage(f"Leyendo {filename} ({current}/{total})…")

    def _import_finished(self, imported: list[int], errors: list[tuple[str, str]]) -> None:
        if self._shutting_down:
            return
        self.progress_bar.hide()
        self.refresh_library()
        if errors:
            details = "\n".join(f"• {Path(path).name}: {message}" for path, message in errors[:8])
            if len(errors) > 8:
                details += f"\n• y {len(errors) - 8} error(es) más"
            QMessageBox.warning(self, "Algunos libros no se pudieron agregar", details)
        else:
            self.status.showMessage(f"Se agregaron {len(imported)} libro(s).", 5000)
        should_open = self._open_after_import and bool(imported)
        self._open_after_import = False
        if should_open:
            self.open_book(imported[0])

    def _clear_import_worker(self) -> None:
        self._import_thread = None
        self._import_worker = None

    def refresh_library(self, _text: str | None = None) -> None:
        search = self.library_search.text().strip() if hasattr(self, "library_search") else ""
        books = self.provider.list_books(search=search)
        self.library_table.setRowCount(len(books))
        for row, book in enumerate(books):
            progress = self._book_progress_text(book)
            values = [book.title, book.author, book.format.upper(), progress, book.path]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, book.id)
                if column == 4:
                    item.setToolTip(book.path)
                self.library_table.setItem(row, column, item)
        if not books:
            self.status.showMessage("La biblioteca está vacía. Agrega un libro para empezar.")

    @staticmethod
    def _book_progress_text(book: Book) -> str:
        if book.total_pages > 0:
            percent = min(100, round(book.last_page * 100 / book.total_pages))
            return f"{percent}%" if book.last_page else "Sin empezar"
        return "Leído" if book.is_read else "Sin empezar"

    def _selected_book_id(self) -> int | None:
        row = self.library_table.currentRow()
        if row < 0:
            return None
        item = self.library_table.item(row, 0)
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return int(value) if value is not None else None

    def _open_table_row(self, row: int, _column: int) -> None:
        item = self.library_table.item(row, 0)
        if item:
            self.open_book(int(item.data(Qt.ItemDataRole.UserRole)))

    def open_selected_book(self) -> None:
        book_id = self._selected_book_id()
        if book_id is not None:
            self.open_book(book_id)

    def remove_selected_book(self) -> None:
        book_id = self._selected_book_id()
        if book_id is None:
            return
        book = self.provider.get_book(book_id)
        if book is None:
            return
        answer = QMessageBox.question(
            self,
            "Quitar libro",
            f"¿Quitar «{book.title}» de la biblioteca?\nEl archivo original no se eliminará.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.provider.remove_book(book_id)
            self.refresh_library()

    def mark_selected_read(self) -> None:
        book_id = self._selected_book_id()
        if book_id is not None:
            self.provider.mark_read(book_id)
            self.refresh_library()

    # -- opening and rendering --------------------------------------------
    def open_book(self, book_id: int) -> None:
        if self._open_thread and self._open_thread.isRunning():
            QMessageBox.information(self, "Libro en proceso", "Espera a que termine de abrirse el libro actual.")
            return
        book = self.provider.get_book(book_id)
        if book is None:
            return
        if not Path(book.path).is_file():
            QMessageBox.warning(self, "Archivo no encontrado", f"No se encontró el archivo:\n{book.path}")
            return

        self.status.showMessage(f"Abriendo «{book.title}»…")
        self.open_selected_button.setEnabled(False)
        self.open_file_button.setEnabled(False)
        thread = QThread(self)
        worker = _OpenWorker(book.id, book.path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._book_opened)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_open_worker)
        self._open_thread = thread
        self._open_worker = worker
        thread.start()

    def _book_opened(self, book_id: int, content: BookContent | None, error: str) -> None:
        if self._shutting_down:
            if content and content.page_source:
                content.page_source.close()
            return
        self.open_selected_button.setEnabled(True)
        self.open_file_button.setEnabled(True)
        if error or content is None:
            QMessageBox.critical(self, "No se pudo abrir el libro", error or "Error desconocido.")
            return
        book = self.provider.get_book(book_id)
        if book is None:
            if content.page_source:
                content.page_source.close()
            return

        self._release_current_book(save=True)
        self.current_book = book
        self.current_content = content
        self._last_location = {"anchor": "", "ratio": 0.0}
        self._restore_location = self._book_location(book, content)
        self.book_title_label.setText(book.title)
        self.book_title_label.setToolTip(f"{book.title}\n{book.author}" if book.author else book.title)
        self._populate_toc(content.toc)
        self._populate_bookmarks(book.id)

        try:
            if content.page_source is not None:
                self.page_server.start()
                self.page_server.register(str(book.id), content.page_source)
                base_url = QUrl(self.page_server.base_url_for(str(book.id)))
            elif content.base_url:
                base_url = QUrl(content.base_url)
            else:
                base_url = QUrl("about:blank")
            self.web.setHtml(self._document_html(content.html), base_url)
        except Exception as exc:  # noqa: BLE001 - Qt can raise several platform-specific errors
            if content.page_source is not None:
                self.page_server.unregister(str(book.id))
            self.current_book = None
            self.current_content = None
            QMessageBox.critical(self, "Error al mostrar el libro", str(exc))
            return

        self.stack.setCurrentWidget(self.reader_page)
        self._progress_timer.start()
        self._update_progress_label()
        self.status.showMessage(f"Leyendo «{book.title}»", 5000)
        self.reader_search.clear()
        self.web.setFocus()

    def _clear_open_worker(self) -> None:
        self._open_thread = None
        self._open_worker = None

    def _document_html(self, body: str) -> str:
        font = re.sub(r"[^A-Za-z0-9 ,_-]", "", self.settings.get("font_family", "Segoe UI")) or "Segoe UI"
        try:
            font_size = min(48, max(10, int(self.settings.get("font_size", "18"))))
        except ValueError:
            font_size = 18
        try:
            line_height = min(3.0, max(1.0, float(self.settings.get("line_height", "1.6"))))
        except ValueError:
            line_height = 1.6
        try:
            padding = min(120, max(8, int(self.settings.get("page_padding", "32"))))
        except ValueError:
            padding = 32

        theme = self.settings.get("last_style_id", "NightStyle")
        themes = {
            "DayStyle": ("#ffffff", "#202124", "#d9dee7"),
            "SepiaStyle": ("#f4ecd8", "#433b2c", "#ded3ba"),
            "NightStyle": ("#202124", "#e8eaed", "#3c4043"),
        }
        background, foreground, border = themes.get(theme, themes["NightStyle"])
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html {{ scroll-behavior: smooth; background: {background}; color: {foreground}; }}
body {{ max-width: 900px; margin: 0 auto; padding: {padding}px; font-family: '{font}', sans-serif;
       font-size: {font_size}px; line-height: {line_height}; color: {foreground}; background: {background};
       overflow-wrap: anywhere; }}
img {{ max-width: 100%; height: auto; }}
table {{ max-width: 100%; border-collapse: collapse; }}
th, td {{ border: 1px solid {border}; padding: 0.35em 0.55em; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
section[id] {{ scroll-margin-top: 14px; }}
.comic {{ max-width: none; }}
.comic section {{ min-height: 70vh; }}
.comic img {{ display: block; margin: 10px auto; }}
a {{ color: #5b9bd5; }}
</style></head><body>{body}</body></html>"""

    @staticmethod
    def _book_location(book: Book, content: BookContent) -> dict[str, Any] | None:
        if book.last_location:
            try:
                value = json.loads(book.last_location)
                if isinstance(value, dict):
                    anchor = str(value.get("anchor", ""))
                    ratio = min(1.0, max(0.0, float(value.get("ratio", 0.0))))
                    return {"anchor": anchor, "ratio": ratio}
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if book.last_page > 0 and content.total_pages > 1:
            ratio = min(1.0, max(0.0, (book.last_page - 1) / (content.total_pages - 1)))
            anchor = f"p{book.last_page}" if content.page_source else ""
            return {"anchor": anchor, "ratio": ratio}
        return None

    def _reader_loaded(self, ok: bool) -> None:
        if not ok or not self.current_book:
            return
        if self._restore_location:
            self._run_restore(self._restore_location)
            self._last_location = dict(self._restore_location)
            self._restore_location = None
        self._update_progress_label()

    def _populate_toc(self, toc: list[tuple[str, str]]) -> None:
        self.toc_list.clear()
        for title, anchor in toc:
            item = QListWidgetItem(title or "Sección")
            item.setData(Qt.ItemDataRole.UserRole, anchor)
            self.toc_list.addItem(item)
        if not toc:
            self.toc_list.addItem(QListWidgetItem("Este libro no incluye índice."))

    def _populate_bookmarks(self, book_id: int) -> None:
        self.bookmark_list.clear()
        for bookmark in self.provider.list_bookmarks(book_id):
            note = f" — {bookmark.note}" if bookmark.note else ""
            item = QListWidgetItem(f"Posición {bookmark.page}{note}")
            item.setData(Qt.ItemDataRole.UserRole, bookmark.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, bookmark.location)
            item.setData(Qt.ItemDataRole.UserRole + 2, bookmark.page)
            item.setToolTip(bookmark.note or f"Posición {bookmark.page}")
            self.bookmark_list.addItem(item)

    def _toc_clicked(self, item: QListWidgetItem) -> None:
        anchor = item.data(Qt.ItemDataRole.UserRole)
        if anchor:
            self._jump_to_anchor(str(anchor))

    def _bookmark_clicked(self, item: QListWidgetItem) -> None:
        raw = item.data(Qt.ItemDataRole.UserRole + 1)
        location = self._decode_location(raw)
        if location:
            self._run_restore(location)
            return
        page = item.data(Qt.ItemDataRole.UserRole + 2)
        if self.current_content and self.current_content.page_source and page:
            self._jump_to_anchor(f"p{int(page)}")
        elif page and self.current_content and self.current_content.total_pages > 1:
            ratio = (int(page) - 1) / (self.current_content.total_pages - 1)
            self._run_restore({"anchor": "", "ratio": ratio})

    @staticmethod
    def _decode_location(raw: object) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            value = json.loads(str(raw))
            if not isinstance(value, dict):
                return None
            anchor = str(value.get("anchor", ""))
            ratio = min(1.0, max(0.0, float(value.get("ratio", 0.0))))
            return {"anchor": anchor, "ratio": ratio}
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _jump_to_anchor(self, anchor: str) -> None:
        if not anchor:
            return
        self.web.page().runJavaScript(
            "(function(id){var el=document.getElementById(id);"
            "if(el){el.scrollIntoView({behavior:'smooth',block:'start'});return true;}return false;})"
            f"({json.dumps(anchor)})"
        )

    def _run_restore(self, location: dict[str, Any]) -> None:
        anchor = str(location.get("anchor", ""))
        ratio = min(1.0, max(0.0, float(location.get("ratio", 0.0))))
        script = (
            "(function(anchor,ratio){const el=anchor&&document.getElementById(anchor);"
            "if(el&&/^p\\d+$/.test(anchor)){el.scrollIntoView({block:'start'});}"
            "else{const max=Math.max(0,document.documentElement.scrollHeight-window.innerHeight);"
            "window.scrollTo(0,max*ratio);}return true;})"
            f"({json.dumps(anchor)},{ratio})"
        )
        self.web.page().runJavaScript(script)

    # -- reading, search, progress and bookmarks ---------------------------
    def _capture_progress(self) -> None:
        if not self.current_book or not self.current_content:
            return
        book_id = self.current_book.id
        self.web.page().runJavaScript(
            _LOCATION_SCRIPT,
            lambda value, expected_id=book_id: self._progress_captured(expected_id, value),
        )

    def _progress_captured(self, expected_id: int, value: object) -> None:
        if not self.current_book or self.current_book.id != expected_id or not isinstance(value, dict):
            return
        location = self._normalize_location(value)
        if location is None:
            return
        self._last_location = location
        page, total = self._page_from_location(location)
        serialized = json.dumps(location, ensure_ascii=False, separators=(",", ":"))
        try:
            self.provider.set_progress(expected_id, page, total, serialized)
        except (ValueError, OSError):
            return
        self.current_book.last_page = page
        self.current_book.total_pages = total
        self.current_book.last_location = serialized
        self._update_progress_label(location)

    @staticmethod
    def _normalize_location(value: dict[str, Any]) -> dict[str, Any] | None:
        try:
            anchor = str(value.get("anchor", ""))
            if len(anchor) > 200 or not re.fullmatch(r"[A-Za-z0-9_.:-]*", anchor):
                anchor = ""
            ratio = min(1.0, max(0.0, float(value.get("ratio", 0.0))))
            return {"anchor": anchor, "ratio": ratio}
        except (TypeError, ValueError):
            return None

    def _page_from_location(self, location: dict[str, Any]) -> tuple[int, int]:
        total = max(1, self.current_content.total_pages if self.current_content else 1)
        anchor = str(location.get("anchor", ""))
        match = re.fullmatch(r"p(\d+)", anchor)
        if match:
            page = int(match.group(1))
        else:
            page = round(float(location.get("ratio", 0.0)) * max(0, total - 1)) + 1
        return min(total, max(1, page)), total

    def _update_progress_label(self, location: dict[str, Any] | None = None) -> None:
        if not self.current_content:
            self.progress_label.setText("—")
            return
        location = location or self._last_location
        page, total = self._page_from_location(location)
        percent = round(float(location.get("ratio", 0.0)) * 100)
        self.progress_label.setText(f"{page}/{total} · {percent}%")
        self.progress_label.setToolTip("Progreso guardado automáticamente")

    def add_bookmark(self) -> None:
        if not self.current_book:
            return
        note, accepted = QInputDialog.getText(self, "Añadir marcador", "Nota (opcional):")
        if not accepted:
            return
        book_id = self.current_book.id
        self.web.page().runJavaScript(
            _LOCATION_SCRIPT,
            lambda value, expected_id=book_id, bookmark_note=note: self._bookmark_location_captured(
                expected_id, value, bookmark_note
            ),
        )

    def _bookmark_location_captured(self, book_id: int, value: object, note: str) -> None:
        if not self.current_book or self.current_book.id != book_id or not isinstance(value, dict):
            return
        location = self._normalize_location(value)
        if location is None:
            return
        page, _total = self._page_from_location(location)
        serialized = json.dumps(location, ensure_ascii=False, separators=(",", ":"))
        try:
            self.provider.add_bookmark(book_id, page, note.strip(), location=serialized)
        except (sqlite3.Error, ValueError) as exc:
            QMessageBox.warning(self, "No se pudo guardar el marcador", str(exc))
            return
        self._populate_bookmarks(book_id)
        self.sidebar.setCurrentIndex(1)
        self.status.showMessage("Marcador guardado", 3000)

    def remove_selected_bookmark(self) -> None:
        if not self.current_book:
            return
        item = self.bookmark_list.currentItem()
        if not item:
            return
        bookmark_id = item.data(Qt.ItemDataRole.UserRole)
        if bookmark_id is not None:
            self.provider.remove_bookmark(int(bookmark_id))
            self._populate_bookmarks(self.current_book.id)

    def search_next(self) -> None:
        query = self.reader_search.text().strip()
        if query:
            self.web.findText(query)

    def search_previous(self) -> None:
        query = self.reader_search.text().strip()
        if query:
            self.web.findText(query, QWebEnginePage.FindFlag.FindBackward)

    def _focus_reader_search(self) -> None:
        if self.stack.currentWidget() == self.reader_page:
            self.reader_search.setFocus()
            self.reader_search.selectAll()

    def _scroll_page(self, direction: int) -> None:
        if not self.current_content:
            return
        script = (
            "(function(direction){const pages=Array.from(document.querySelectorAll('.comic section[id]'));"
            "if(pages.length){let index=0;const y=window.scrollY+window.innerHeight*0.35;"
            "for(let i=0;i<pages.length;i++){if(pages[i].offsetTop<=y)index=i;}"
            "index=Math.max(0,Math.min(pages.length-1,index+direction));"
            "pages[index].scrollIntoView({behavior:'smooth',block:'start'});}"
            "else{window.scrollBy({top:direction*window.innerHeight*0.88,behavior:'smooth'});}})"
            f"({direction})"
        )
        self.web.page().runJavaScript(script)

    def _change_font_size(self, delta: int) -> None:
        try:
            size = int(self.settings.get("font_size", "18"))
        except ValueError:
            size = 18
        size = min(48, max(10, size + delta))
        self.settings.set("font_size", str(size))
        self.settings.save()
        self._reload_current_book()

    def _cycle_theme(self) -> None:
        themes = ["DayStyle", "SepiaStyle", "NightStyle"]
        current = self.settings.get("last_style_id", "NightStyle")
        next_theme = themes[(themes.index(current) + 1) % len(themes)] if current in themes else themes[0]
        self.settings.set("last_style_id", next_theme)
        self.settings.save()
        self.status.showMessage(f"Tema: {next_theme}", 2500)
        self._reload_current_book()

    def _reload_current_book(self) -> None:
        if not self.current_book or not self.current_content:
            return
        location = dict(self._last_location)
        content = self.current_content
        if content.page_source is not None:
            base_url = QUrl(self.page_server.base_url_for(str(self.current_book.id)))
        elif content.base_url:
            base_url = QUrl(content.base_url)
        else:
            base_url = QUrl("about:blank")
        self._restore_location = location
        self.web.setHtml(self._document_html(content.html), base_url)

    def _release_current_book(self, save: bool = True) -> None:
        self._progress_timer.stop()
        book = self.current_book
        content = self.current_content
        if save and book and content:
            page, total = self._page_from_location(self._last_location)
            location = json.dumps(self._last_location, ensure_ascii=False, separators=(",", ":"))
            try:
                self.provider.set_progress(book.id, page, total, location)
            except (ValueError, OSError):
                pass
        if book and content and content.page_source is not None:
            self.page_server.unregister(str(book.id))
        self.current_book = None
        self.current_content = None
        self._restore_location = None

    def return_to_library(self) -> None:
        if self.stack.currentWidget() == self.reader_page:
            self._release_current_book(save=True)
            self.stack.setCurrentWidget(self.library_page)
            self.refresh_library()
            self.status.showMessage("Biblioteca", 2000)

    # -- window lifecycle --------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.import_files(paths)
            event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        self._shutting_down = True
        self._release_current_book(save=True)
        for thread in (self._import_thread, self._open_thread):
            if thread and thread.isRunning():
                thread.quit()
                thread.wait()
        self.page_server.stop()
        try:
            self.provider.close()
        except sqlite3.Error:  # The app is already shutting down; nothing more to do.
            pass
        event.accept()
