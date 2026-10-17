"""SQLite-backed book library.

Tables: books, categories, book_categories (links), bookmarks with
ON DELETE CASCADE on bookmarks.book_id.
"""
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .app_paths import data_dir

DB_FILE = data_dir() / "library.db"


@dataclass
class Book:
    id: int
    title: str
    author: str
    format: str          # epub | mobi | prc | azw | fb2 | pdf | txt | html | md | rtf | docx | odt | comics
    path: str
    cover: Optional[bytes]
    added_date: str
    last_page: int = 0
    total_pages: int = 0
    is_read: bool = False
    last_location: str = ""


@dataclass
class Bookmark:
    id: int
    book_id: int
    page: int
    note: str
    color: str          # hex, e.g. "#f3e3a2" (default bookmark/highlight color)
    created_at: str
    location: str = ""


class BookProvider:
    """Library data access layer."""

    def __init__(self, db_path: Path = DB_FILE) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        # SQLite does not enforce declared foreign keys unless enabled per connection.
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        self._migrate_schema()

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
                is_read INTEGER DEFAULT 0,
                last_location TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
            INSERT OR IGNORE INTO categories (name) VALUES ('Read');
            CREATE TABLE IF NOT EXISTS book_categories (
                book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
                category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                PRIMARY KEY (book_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
                page INTEGER NOT NULL,
                note TEXT DEFAULT '',
                color TEXT DEFAULT '#f3e3a2',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                location TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self._conn.commit()

    def _migrate_schema(self) -> None:
        """Repair legacy relationship tables and remove rows orphaned by old builds."""
        foreign_keys = self._conn.execute(
            "PRAGMA foreign_key_list(book_categories)").fetchall()
        cascades = {(row["from"], row["table"], row["on_delete"].upper()) for row in foreign_keys}
        expected = {
            ("book_id", "books", "CASCADE"),
            ("category_id", "categories", "CASCADE"),
        }
        with self._conn:
            for table, column in (("books", "last_location"), ("bookmarks", "location")):
                columns = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            if not expected.issubset(cascades):
                self._conn.execute("DROP TABLE IF EXISTS book_categories_new")
                self._conn.execute(
                    """CREATE TABLE book_categories_new (
                        book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
                        category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                        PRIMARY KEY (book_id, category_id)
                    )"""
                )
                self._conn.execute(
                    """INSERT OR IGNORE INTO book_categories_new (book_id, category_id)
                       SELECT bc.book_id, bc.category_id
                       FROM book_categories bc
                       JOIN books b ON b.id = bc.book_id
                       JOIN categories c ON c.id = bc.category_id"""
                )
                self._conn.execute("DROP TABLE book_categories")
                self._conn.execute(
                    "ALTER TABLE book_categories_new RENAME TO book_categories")
            self._conn.execute(
                "DELETE FROM bookmarks WHERE book_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM books WHERE books.id = bookmarks.book_id)"
            )
            self._conn.execute("INSERT OR IGNORE INTO categories (name) VALUES ('Read')")

    # -- books -----------------------------------------------------------------
    def add_book(self, path: str, title: str = "", author: str = "",
                 cover: Optional[bytes] = None) -> int:
        abs_path = str(Path(path).resolve())
        fmt = Path(abs_path).suffix.lstrip(".").lower()
        resolved_title = title or Path(abs_path).stem
        existing = self._conn.execute(
            "SELECT id FROM books WHERE path=?", (abs_path,)).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE books SET title=?, author=?, format=?, "
                "cover=COALESCE(?, cover) WHERE id=?",
                (resolved_title, author, fmt, cover, int(existing["id"])),
            )
            self._conn.commit()
            return int(existing["id"])
        cursor = self._conn.execute(
            "INSERT INTO books (title, author, format, path, cover) VALUES (?, ?, ?, ?, ?)",
            (resolved_title, author, fmt, abs_path, cover),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def remove_book(self, book_id: int) -> None:
        self._conn.execute("DELETE FROM books WHERE id=?", (book_id,))
        self._conn.commit()

    @staticmethod
    def _book_from_row(row: sqlite3.Row) -> Book:
        values = dict(row)
        values["is_read"] = bool(values["is_read"])
        return Book(**values)

    def get_book(self, book_id: int) -> Optional[Book]:
        row = self._conn.execute(
            "SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return self._book_from_row(row) if row else None

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
        return [self._book_from_row(row) for row in rows]

    def set_progress(
        self,
        book_id: int,
        page: int,
        total_pages: int = 0,
        location: str | None = None,
    ) -> None:
        if page < 0 or total_pages < 0 or (total_pages and page > total_pages):
            raise ValueError("La posición de lectura está fuera de rango.")
        updates = ["last_page=?"]
        values: list[object] = [page]
        if total_pages:
            updates.append("total_pages=?")
            values.append(total_pages)
        if location is not None:
            updates.append("last_location=?")
            values.append(location)
        values.append(book_id)
        self._conn.execute(f"UPDATE books SET {', '.join(updates)} WHERE id=?", values)
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
        row = self._conn.execute(
            "SELECT name FROM categories WHERE id=?", (category_id,)).fetchone()
        if row and row["name"] == "Read":
            raise ValueError("La categoría integrada 'Read' no se puede renombrar.")
        self._conn.execute(
            "UPDATE categories SET name=? WHERE id=?", (name.strip(), category_id))
        self._conn.commit()

    def remove_category(self, category_id: int) -> None:
        row = self._conn.execute(
            "SELECT name FROM categories WHERE id=?", (category_id,)).fetchone()
        if row and row["name"] == "Read":
            raise ValueError("La categoría integrada 'Read' no se puede eliminar.")
        self._conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
        self._conn.commit()

    def set_book_categories(self, book_id: int, category_ids: list[int]) -> None:
        ids = sorted(set(category_ids))
        if not self._conn.execute(
                "SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone():
            raise ValueError(f"No existe el libro {book_id}.")
        if ids:
            placeholders = ",".join("?" for _ in ids)
            found = self._conn.execute(
                f"SELECT id FROM categories WHERE id IN ({placeholders})", ids).fetchall()
            if {int(row["id"]) for row in found} != set(ids):
                raise ValueError("Una o más categorías no existen.")
        with self._conn:
            self._conn.execute("DELETE FROM book_categories WHERE book_id=?", (book_id,))
            self._conn.executemany(
                "INSERT INTO book_categories (book_id, category_id) VALUES (?, ?)",
                [(book_id, cid) for cid in ids],
            )
            read_category = self._conn.execute(
                "SELECT id FROM categories WHERE name='Read'").fetchone()
            if read_category:
                self._conn.execute(
                    "UPDATE books SET is_read=? WHERE id=?",
                    (int(int(read_category["id"]) in ids), book_id),
                )

    def categories_for_book(self, book_id: int) -> list[int]:
        rows = self._conn.execute(
            "SELECT category_id FROM book_categories WHERE book_id=?", (book_id,)).fetchall()
        return [int(r["category_id"]) for r in rows]

    # -- bookmarks ---------------------------------------------------------------
    def add_bookmark(
        self,
        book_id: int,
        page: int,
        note: str = "",
        color: str = "#f3e3a2",
        location: str = "",
    ) -> int:
        if page < 0:
            raise ValueError("La página del marcador debe ser cero o mayor.")
        cursor = self._conn.execute(
            "INSERT INTO bookmarks (book_id, page, note, color, location) "
            "VALUES (?, ?, ?, ?, ?)",
            (book_id, page, note, color, location),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

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
        with self._conn:
            self._conn.execute("DELETE FROM books")
            self._conn.execute("DELETE FROM categories WHERE name <> 'Read'")

    def close(self) -> None:
        self._conn.close()
