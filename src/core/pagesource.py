"""Abstract on-demand page source shared by comic archives, PDFs and the exporter.

A :class:`PageSource` produces one book's pages lazily: callers ask for a specific
1-based page index and get back ``(image_bytes, mime_type)`` (or ``None``). Nothing is
decoded or rendered until it is requested, which is what makes opening large books fast —
only the pages that are actually shown ever touch disk / CPU.

Subclasses keep their own handle open and cache recently used pages in a small LRU so
scrolling back and forth does not re-read or re-render. ``close()`` releases the handle
but must be safe to call repeatedly and must allow the source to be reopened afterwards
(the reader closes a book's source when another book is opened, then may reopen it from
the in-memory cache).
"""
import threading
from collections import OrderedDict


class _LRU(OrderedDict):
    """Tiny bounded insertion-ordered cache. ``put`` evicts the oldest entry."""

    def __init__(self, capacity: int = 40) -> None:
        super().__init__()
        self.capacity = max(1, int(capacity))

    def get(self, key, default=None):
        """Return *key* and refresh its recency, like a true LRU cache."""
        if key not in self:
            return default
        self.move_to_end(key)
        return super().get(key, default)

    def put(self, key, value) -> None:
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.capacity:
            self.popitem(last=False)


class PageSource:
    """Base class for anything that can hand out page images on demand."""

    def __init__(self) -> None:
        # Serializes access to the (usually non-thread-safe) underlying handle. The
        # HTTP server serves pages from worker threads, so this must be a re-entrant lock.
        self._lock = threading.RLock()

    @property
    def total_pages(self) -> int:
        raise NotImplementedError

    def page_image(self, index: int):
        """Return ``(image_bytes, mime_type)`` for 1-based *index*, or ``None``."""
        raise NotImplementedError

    def close(self) -> None:
        """Release resources. Must be idempotent and reopen-safe."""

    def __del__(self):
        # Best-effort cleanup when the reader's LRU evicts a book without an explicit
        # close (e.g. process shutdown). Never raise from a destructor.
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
