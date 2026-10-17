from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.core.book_provider import BookProvider


class BookProviderTests(unittest.TestCase):
    def test_removing_book_cascades_bookmarks_and_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = BookProvider(Path(tmp) / "library.db")
            book = provider.add_book(str(Path(tmp) / "book.epub"))
            category = provider.add_category("Fantasy")
            provider.set_book_categories(book, [category])
            provider.add_bookmark(book, 1, "note")
            self.assertEqual(provider._conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

            provider.remove_book(book)
            self.assertEqual(provider.list_bookmarks(book), [])
            self.assertEqual(provider.categories_for_book(book), [])
            provider.close()

    def test_legacy_database_is_migrated_without_orphan_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.db"
            connection = sqlite3.connect(db)
            connection.executescript(
                """CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL, author TEXT DEFAULT '',
                    format TEXT NOT NULL, path TEXT NOT NULL UNIQUE, cover BLOB, added_date TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_page INTEGER DEFAULT 0, total_pages INTEGER DEFAULT 0, is_read INTEGER DEFAULT 0);
                CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
                INSERT INTO categories (id, name) VALUES (1, 'Read');
                CREATE TABLE book_categories (book_id INTEGER REFERENCES books(id),
                    category_id INTEGER REFERENCES categories(id), PRIMARY KEY (book_id, category_id));
                CREATE TABLE bookmarks (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER REFERENCES books(id) ON DELETE CASCADE, page INTEGER NOT NULL,
                    note TEXT DEFAULT '', color TEXT DEFAULT '#f3e3a2', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                INSERT INTO books (id, title, author, format, path) VALUES (1, 'Book', '', 'epub', 'book.epub');
                INSERT INTO categories (id, name) VALUES (2, 'Custom');
                INSERT INTO book_categories VALUES (1, 2);
                INSERT INTO book_categories VALUES (999, 2);
                INSERT INTO bookmarks (book_id, page) VALUES (1, 1);
                INSERT INTO bookmarks (book_id, page) VALUES (999, 1);"""
            )
            connection.commit()
            connection.close()

            provider = BookProvider(db)
            self.assertEqual(provider._conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(provider._conn.execute("SELECT COUNT(*) FROM book_categories").fetchone()[0], 1)
            self.assertEqual(provider._conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0], 1)
            self.assertEqual(provider.get_book(1).last_location, "")
            self.assertEqual(provider.list_bookmarks(1)[0].location, "")
            provider.remove_category(2)
            self.assertEqual(provider.categories_for_book(1), [])
            provider.remove_book(1)
            self.assertEqual(provider.list_bookmarks(1), [])
            provider.close()

    def test_read_category_is_preserved_by_name_not_numeric_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library.db"
            legacy = sqlite3.connect(db)
            legacy.execute("CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL)")
            legacy.execute("INSERT INTO categories (id, name) VALUES (1, 'Custom')")
            legacy.commit()
            legacy.close()

            provider = BookProvider(db)
            custom = provider.add_category("Custom")
            provider.rename_category(custom, "Renamed")
            provider.clear_library()
            names = [name for _, name in provider.list_categories()]
            self.assertEqual(names, ["Read"])
            read_id = next(category_id for category_id, name in provider.list_categories() if name == "Read")
            self.assertNotEqual(read_id, 1)
            with self.assertRaises(ValueError):
                provider.remove_category(read_id)
            with self.assertRaises(ValueError):
                provider.rename_category(read_id, "Finished")
            provider.close()

    def test_progress_and_bookmark_locations_survive_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library.db"
            provider = BookProvider(db)
            book_id = provider.add_book(str(Path(tmp) / "read.epub"))
            location = '{"anchor":"chapter-2","ratio":0.42}'
            provider.set_progress(book_id, 5, 12, location)
            bookmark_id = provider.add_bookmark(book_id, 5, "Important", location=location)
            provider.close()

            reopened = BookProvider(db)
            book = reopened.get_book(book_id)
            bookmark = reopened.list_bookmarks(book_id)[0]
            self.assertEqual(book.last_page, 5)
            self.assertEqual(book.total_pages, 12)
            self.assertEqual(book.last_location, location)
            self.assertEqual(bookmark.id, bookmark_id)
            self.assertEqual(bookmark.location, location)
            reopened.close()

    def test_set_book_categories_is_atomic_for_invalid_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = BookProvider(Path(tmp) / "library.db")
            book = provider.add_book(str(Path(tmp) / "book.pdf"))
            category = provider.add_category("History")
            provider.set_book_categories(book, [category])
            with self.assertRaises(ValueError):
                provider.set_book_categories(book, [123456])
            self.assertEqual(provider.categories_for_book(book), [category])
            provider.close()


if __name__ == "__main__":
    unittest.main()
