"""Local loopback HTTP server that streams page images to the WebEngine view.

Streaming books (comics, PDFs) are rendered with relative ``<img src="page/N">`` tags and
a document base URL of ``http://127.0.0.1:PORT/<book_id>/``. This server resolves those
requests by asking the registered :class:`~src.core.pagesource.PageSource` for the page
bytes on demand, so only visible pages are ever read or rendered — keeping opens fast and
zoom/scroll responsive.

The server binds to ``127.0.0.1`` on an ephemeral port (no network exposure) and runs in a
daemon thread. It is process-wide singleton via :func:`get_page_server`.
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# book_id -> PageSource
_registry: dict[str, "PageSource"] = {}
_reg_lock = threading.Lock()


class _ThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True  # handler threads must not block process exit


class _Handler(BaseHTTPRequestHandler):
    server_version = "DigitalBookReader/1.0"

    def log_message(self, *args):  # silence default stderr logging
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        segs = [s for s in path.split("/") if s != ""]
        # Expected shape: <book_id>/page/<index>   (book_id is an int, never contains '/')
        if len(segs) >= 3 and segs[1] == "page" and segs[2].isdigit():
            book_id = segs[0]
            index = int(segs[2])
            with _reg_lock:
                src = _registry.get(book_id)
            if src is None:
                self._send(404, b"", "text/plain")
                return
            try:
                res = src.page_image(index)
            except Exception as e:  # noqa: BLE001 - surface a readable error page
                self._send(500, str(e).encode("utf-8", "replace"), "text/plain")
                return
            if res is None:
                self._send(404, b"", "image/png")
                return
            data, mime = res
            self._send(200, data, mime)
        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            # Client went away mid-response (navigation, page switch or app
            # shutdown). Nothing to do — swallow so it never reaches the server's
            # error handler and spams stderr.
            pass


class PageServer:
    def __init__(self) -> None:
        self._httpd = None
        self._port = 0

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = _ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def register(self, book_id: str, source: "PageSource") -> None:
        with _reg_lock:
            old = _registry.get(book_id)
            _registry[book_id] = source
        if old is not None and old is not source:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass

    def unregister(self, book_id: str) -> None:
        with _reg_lock:
            src = _registry.pop(book_id, None)
        if src is not None:
            try:
                src.close()
            except Exception:  # noqa: BLE001
                pass

    def base_url_for(self, book_id: str) -> str:
        return f"http://127.0.0.1:{self._port}/{book_id}/"

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
            self._httpd = None


_server: PageServer | None = None
_server_lock = threading.Lock()


def get_page_server() -> PageServer:
    """Process-wide :class:`PageServer` singleton (created on first use)."""
    global _server
    with _server_lock:
        if _server is None:
            _server = PageServer()
        return _server
