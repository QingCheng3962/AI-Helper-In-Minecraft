"""Tiny local HTTP server that publishes relayed QQ images.

Only the ``/pic_http/<file>`` path is exposed (never the filesystem root and
no directory listing). Images are saved into ``<project>/pic_http/`` with a
timestamp-based name by :func:`save_image_bytes`.
"""
from __future__ import annotations

import mimetypes
import os
import random
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTO_DIR = os.path.join(BASE_DIR, 'pic_http')
PHOTO_PREFIX = '/pic_http/'

_EXT_BY_MAGIC = (
    (b'\x89PNG\r\n\x1a\n', '.png'),
    (b'\xff\xd8\xff', '.jpg'),
    (b'GIF87a', '.gif'),
    (b'GIF89a', '.gif'),
)


def guess_ext(data: bytes, hint: str = '') -> str:
    """Pick a file extension from magic bytes, falling back to the hint."""
    for magic, ext in _EXT_BY_MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) > 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    m = os.path.splitext(str(hint or ''))[1].lower()
    if m and len(m) <= 6 and m[1:].isalnum():
        return m
    return '.jpg'


def save_image_bytes(data: bytes, hint: str = '') -> str:
    """Save ``data`` under ``pic_http/`` and return the generated file name."""
    os.makedirs(PHOTO_DIR, exist_ok=True)
    ext = guess_ext(data, hint)
    now = time.time()
    stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(now))
    ms = int((now - int(now)) * 1000)
    name = f'{stamp}_{ms:03d}_{random.randint(0, 0xffff):04x}{ext}'
    full = os.path.join(PHOTO_DIR, name)
    with open(full, 'wb') as f:
        f.write(data)
    return name


class _PhotoHandler(BaseHTTPRequestHandler):
    server_version = 'aafm-photo/1.0'

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith(PHOTO_PREFIX):
            self.send_error(404, 'Not Found')
            return
        name = os.path.basename(urllib.parse.unquote(path[len(PHOTO_PREFIX):]))
        if not name or name in ('.', '..'):
            self.send_error(404, 'Not Found')
            return
        full = os.path.join(PHOTO_DIR, name)
        # Guard against path traversal (basename already strips separators).
        if os.path.dirname(os.path.abspath(full)) != os.path.abspath(PHOTO_DIR) \
                or not os.path.isfile(full):
            self.send_error(404, 'Not Found')
            return
        try:
            with open(full, 'rb') as f:
                body = f.read()
        except OSError:
            self.send_error(404, 'Not Found')
            return
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def log_message(self, *args):  # silence default logging
        pass


class PhotoServer:
    def __init__(self) -> None:
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.host: str = ''
        self.port: int = 0

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self, host: str = '0.0.0.0', port: int = 8765) -> None:
        if self.running:
            if self.host == host and self.port == port:
                return
            self.stop()
        os.makedirs(PHOTO_DIR, exist_ok=True)
        self._httpd = ThreadingHTTPServer((host, port), _PhotoHandler)
        self.host, self.port = host, port
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
