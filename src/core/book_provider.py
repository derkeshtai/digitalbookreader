"""SQLite-backed book library.

Tables: books, categories, book_categories (links), bookmarks with
ON DELETE CASCADE on bookmarks.book_id.
"""
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DB_FILE = Path.home() / "AppData" / "Local" / "Digital Book Reader" / "library.db"


@dataclass
class Book:
    id: int
    title: str
    author: str
    format: str          # epub | mobi | prc | fb2 | pdf | txt | cbz | cbr | zip | 7z
    path: str
    cover: Optional[bytes]
    added_date: str
    last_page: int = 0
    total_pages: int = 0
    is_read: bool = False


@dataclass
class Bookmark:
    id: int
    book_id: int
    page: int
    note: str
    color: str          # hex, e.g. "#f3e3a2" (default bookmark/highlight color)
    created_at: str


class BookProvider:
    """Library data access layer."""

    def __init__(self, db_path: Path = DB_FILE) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    # -- schema -------------------------------------------------------------
    def _create_schema(self) -> None:
        c = self._conn.cursor()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT DEFAULT '',
                format TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                cover BLOB,
                added_date TEXT DEFAULT CURRENT_TIMESTAMP,
                last_page INTEGER DEFAULT 0,
                total_pages INTEGER DEFAULT 0,
                is_read INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
            INSERT OR IGNORE INTO categories (id, name) VALUES (1, 'Read');
            CREATE TABLE IF NOT EXISTS book_categories (
                book_id INTEGER REFERENCES books(id),
                category_id INTEGER REFERENCES categories(id),
                PRIMARY KEY (book_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
                page INTEGER NOT NULL,
                note TEXT DEFAULT '',
                color TEXT DEFAULT '#f3e3a2',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._conn.commit()

    # -- books -----------------------------------------------------------------
    def add_book(self, path: str, title: str = "", author: str = "",
                 cover: Optional[bytes] = None) -> int:
        c = self._conn.cursor()
        abs_path = str(Path(path).resolve())
        fmt = Path(abs_path).suffix.lstrip(".").lower()
        try:
            c.execute(
                "INSERT INTO books (title, author, format, path, cover) VALUES (?, ?, ?, ?, ?)",
                (title or Path(abs_path).stem, author, fmt, abs_path, cover))
            self._conn.commit()
            return int(c.lastrowid)
        except sqlite3.IntegrityError:
            # Already in library; update metadata only.
            c.execute(
                "UPDATE books SET title=?, author=? WHERE path=?",
                (title or Path(abs_path).stem, author, abs_path))
            self._conn.commit()
            row = c.execute("SELECT id FROM books WHERE path=?", (abs_path,)).fetchone()
            return int(row["id"]) if row else -1

    def remove_book(self, book_id: int) -> None:
        self._conn.execute("DELETE FROM books WHERE id=?", (book_id,))
        self._conn.commit()

    def get_book(self, book_id: int) -> Optional[Book]:
        row = self._conn.execute(
            "SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return Book(**dict(row)) if row else None

    def list_books(self, search: str = "", category_id: Optional[int] = None) -> list[Book]:
        sql = "SELECT b.* FROM books b"
        params: list = []
        if search:
            sql += " WHERE (b.title LIKE ? OR b.author LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like])
        if category_id is not None:
            join = " AND b.id IN (SELECT book_id FROM book_categories WHERE category_id=?) " \
                   if search else " WHERE b.id IN (SELECT book_id FROM book_categories WHERE category_id=?)"
            sql += join
            params.append(category_id)
        rows = self._conn.execute(sql, params).fetchall()
        return [Book(**dict(r)) for r in rows]

    def set_progress(self, book_id: int, page: int, total_pages: int = 0) -> None:
        if total_pages:
            self._conn.execute(
                "UPDATE books SET last_page=?, total_pages=? WHERE id=?",
                (page, total_pages, book_id))
        else:
            self._conn.execute("UPDATE books SET last_page=? WHERE id=?", (page, book_id))
        self._conn.commit()

    def mark_read(self, book_id: int) -> None:
        """Mark as read and move to the built-in 'Read' category."""
        c = self._conn.cursor()
        c.execute("UPDATE books SET is_read=1 WHERE id=?", (book_id,))
        row = c.execute(
            "SELECT id FROM categories WHERE name='Read'").fetchone()
        if row:
            c.execute(
                "INSERT OR IGNORE INTO book_categories (book_id, category_id) VALUES (?, ?)",
                (book_id, int(row["id"])))
        self._conn.commit()

    # -- categories -------------------------------------------------------------
    def list_categories(self) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT id, name FROM categories ORDER BY name").fetchall()
        return [(int(r["id"]), r["name"]) for r in rows]

    def add_category(self, name: str) -> int:
        c = self._conn.cursor()
        c.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
        row = c.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()
        self._conn.commit()
        return int(row["id"])

    def rename_category(self, category_id: int, name: str) -> None:
        self._conn.execute(
            "UPDATE categories SET name=? WHERE id=?", (name, category_id))
        self._conn.commit()

    def remove_category(self, category_id: int) -> None:
        self._conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
        self._conn.commit()

    def set_book_categories(self, book_id: int, category_ids: list[int]) -> None:
        c = self._conn.cursor()
        c.execute("DELETE FROM book_categories WHERE book_id=?", (book_id,))
        for cid in category_ids:
            c.execute(
                "INSERT OR IGNORE INTO book_categories (book_id, category_id) VALUES (?, ?)",
                (book_id, cid))
        self._conn.commit()

    def categories_for_book(self, book_id: int) -> list[int]:
        rows = self._conn.execute(
            "SELECT category_id FROM book_categories WHERE book_id=?", (book_id,)).fetchall()
        return [int(r["category_id"]) for r in rows]

    # -- bookmarks ---------------------------------------------------------------
    def add_bookmark(self, book_id: int, page: int, note: str = "",
                    color: str = "#f3e3a2") -> int:
        c = self._conn.cursor()
        c.execute(
            "INSERT INTO bookmarks (book_id, page, note, color) VALUES (?, ?, ?, ?)",
            (book_id, page, note, color))
        self._conn.commit()
        return int(c.lastrowid)

    def remove_bookmark(self, bookmark_id: int) -> None:
        self._conn.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))
        self._conn.commit()

    def clear_bookmarks(self, book_id: int) -> None:
        self._conn.execute("DELETE FROM bookmarks WHERE book_id=?", (book_id,))
        self._conn.commit()

    def list_bookmarks(self, book_id: int) -> list[Bookmark]:
        rows = self._conn.execute(
            "SELECT * FROM bookmarks WHERE book_id=? ORDER BY page", (book_id,)).fetchall()
        return [Bookmark(**dict(r)) for r in rows]

    # -- maintenance ---------------------------------------------------------------
    def clear_library(self) -> None:
        """Remove all books and custom categories (keeps the built-in 'Read')."""
        self._conn.execute("DELETE FROM books")
        self._conn.execute(
            "DELETE FROM categories WHERE id NOT IN (1)")  # keep 'Read'
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
