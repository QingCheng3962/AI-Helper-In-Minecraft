"""本地 HTTP 服务：QQ 图片/视频 + QQ 消息 API（供网站接入）。

两类路径：

- ``/pic_http/<file>`` —— 只读图片/视频（保持原有行为，可被网站 ``<img>`` /
  ``<video>`` 直接引用）；
- ``/api/qq/messages``、``/api/qq/images``、``/api/qq/stream``、``/health``
  —— 给网站的 JSON / SSE 接口（可选 Token 鉴权）。

图片/视频保存在 ``<project>/pic_http/``，文件名由 :func:`save_image_bytes`
生成。消息只保存在内存环形缓冲（默认最近 500 条），重启后清空；媒体文件仍在
磁盘上。
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import random
import re
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Deque, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTO_DIR = os.path.join(BASE_DIR, 'pic_http')
PHOTO_PREFIX = '/pic_http/'

MAX_EVENTS = 500
MAX_IMAGES = 500
SSE_MAX_SECONDS = 30 * 60

_EXT_BY_MAGIC = (
    (b'\x89PNG\r\n\x1a\n', '.png'),
    (b'\xff\xd8\xff', '.jpg'),
    (b'GIF87a', '.gif'),
    (b'GIF89a', '.gif'),
)

_IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')
_VIDEO_EXTS = ('.mp4', '.m4v', '.mov')
_MEDIA_EXTS = _IMAGE_EXTS + _VIDEO_EXTS


def guess_ext(data: bytes, hint: str = '') -> str:
    """Pick a file extension from magic bytes, falling back to the hint."""
    for magic, ext in _EXT_BY_MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) > 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    clean_hint = str(hint or '').split('?', 1)[0].split('#', 1)[0]
    hint_ext = os.path.splitext(clean_hint)[1].lower()
    # MP4 / MOV share the ISO-BMFF ``ftyp`` header; prefer the hint's extension.
    if len(data) > 12 and data[4:8] == b'ftyp':
        if hint_ext in _VIDEO_EXTS:
            return hint_ext
        return '.mp4'
    if hint_ext and len(hint_ext) <= 6 and hint_ext[1:].isalnum():
        return hint_ext
    return '.jpg'


def _safe_stem(hint: str) -> str:
    """把 QQ 给的路径/URL 变成安全的文件名主干（不含扩展名）。"""
    raw = str(hint or '').strip()
    if not raw:
        return ''
    if '://' in raw:
        raw = urllib.parse.urlsplit(raw).path
    else:
        raw = raw.split('?', 1)[0]
    raw = urllib.parse.unquote(raw).replace('\\', '/')
    base = os.path.basename(raw)
    stem, ext = os.path.splitext(base)
    if ext.lower() not in _MEDIA_EXTS:
        return ''
    # 允许中文/英文/数字/._-，其余换成 _；去掉首尾的点/下划线，防止隐藏文件/穿越
    stem = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff._-]+', '_', stem)
    stem = stem.strip('._-')[:60]
    return stem


def _unique_name(stem: str, ext: str) -> str:
    """同名文件自动加 _2/_3…，不覆盖已有图片。"""
    name = stem + ext
    full = os.path.join(PHOTO_DIR, name)
    i = 1
    while os.path.exists(full):
        i += 1
        name = f'{stem}_{i}{ext}'
        full = os.path.join(PHOTO_DIR, name)
        if i > 500:
            name = f'{stem}_{random.randint(0, 0xffffff):06x}{ext}'
            full = os.path.join(PHOTO_DIR, name)
            break
    return name


def save_image_bytes(data: bytes, hint: str = '',
                     preserve_name: bool = False) -> str:
    """Save ``data`` under ``pic_http/`` and return the generated file name.

    ``data`` 可以是图片，也可以是 QQ 视频（.mp4 等）。``preserve_name=True``
    时尽量保留 QQ 原始文件名（只保留安全字符，重复自动加后缀），扩展名仍按
    真实媒体格式修正，避免 MIME 不一致。
    """
    os.makedirs(PHOTO_DIR, exist_ok=True)
    ext = guess_ext(data, hint)
    name = ''
    if preserve_name:
        stem = _safe_stem(hint)
        if stem:
            name = _unique_name(stem, ext)
    if not name:
        now = time.time()
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime(now))
        ms = int((now - int(now)) * 1000)
        name = f'{stamp}_{ms:03d}_{random.randint(0, 0xffff):04x}{ext}'
    full = os.path.join(PHOTO_DIR, name)
    with open(full, 'wb') as f:
        f.write(data)
    return name


class _PhotoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, photo: 'PhotoServer'):
        super().__init__(addr, handler)
        self.photo = photo


class _PhotoHandler(BaseHTTPRequestHandler):
    server_version = 'aafm-photo/1.1'
    protocol_version = 'HTTP/1.0'

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def photo(self) -> 'PhotoServer':
        return self.server.photo  # type: ignore[attr-defined]

    def _path(self) -> str:
        return urllib.parse.urlsplit(self.path).path

    def _query(self) -> Dict[str, List[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def _cors(self) -> None:
        origin = self.photo.cors_origin or '*'
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'Authorization, X-Token, X-Api-Token, Content-Type')
        self.send_header('Access-Control-Max-Age', '86400')

    def _json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authorized(self) -> bool:
        token = str(self.photo.token or '')
        if not token:
            return True
        supplied = ''
        auth = self.headers.get('Authorization') or ''
        if auth.lower().startswith('bearer '):
            supplied = auth[7:].strip()
        if not supplied:
            supplied = (self.headers.get('X-Token')
                        or self.headers.get('X-Api-Token') or '')
        if not supplied:
            supplied = (self._query().get('token') or [''])[0]
        return hmac.compare_digest(str(supplied), token)

    def _int_param(self, name: str, default: int, lo: int, hi: int) -> int:
        try:
            value = int((self._query().get(name) or [default])[0])
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    def _serve_file(self, head_only: bool = False) -> None:
        path = self._path()
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
            size = os.path.getsize(full)
            f = open(full, 'rb')
        except OSError:
            self.send_error(404, 'Not Found')
            return
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self._cors()
        self.end_headers()
        if head_only:
            f.close()
            return
        try:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            f.close()

    # ------------------------------------------------------------------
    # HTTP methods
    # ------------------------------------------------------------------
    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header('Content-Length', '0')
        self._cors()
        self.end_headers()

    def do_HEAD(self):  # noqa: N802
        if self._path().startswith(PHOTO_PREFIX):
            self._serve_file(head_only=True)
        else:
            self.send_error(404, 'Not Found')

    def do_GET(self):  # noqa: N802
        path = self._path()
        if path.startswith(PHOTO_PREFIX):
            self._serve_file()
            return

        if path == '/health':
            self._json(200, {'ok': True, **self.photo.stats()})
            return

        if path not in ('/api/qq/messages', '/api/qq/images', '/api/qq/stream'):
            self.send_error(404, 'Not Found')
            return

        if not self._authorized():
            self._json(401, {'ok': False, 'msg': 'unauthorized'})
            return

        if path == '/api/qq/messages':
            since = self._int_param('since', 0, 0, 2 ** 62)
            limit = self._int_param('limit', 50, 1, 200)
            messages = self.photo.messages_since(since, limit)
            self._json(200, {
                'ok': True,
                'last_id': messages[-1]['id'] if messages else since,
                'messages': messages,
            })
            return

        if path == '/api/qq/images':
            limit = self._int_param('limit', 50, 1, 200)
            images = self.photo.recent_images(limit)
            self._json(200, {'ok': True, 'images': images})
            return

        # Server-Sent Events: 网站可用 EventSource 实时接收新消息
        since = self._int_param('since', 0, 0, 2 ** 62)
        last_event = self.headers.get('Last-Event-ID') or ''
        if last_event.isdigit():
            since = max(since, int(last_event))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self._cors()
        self.end_headers()
        deadline = time.time() + SSE_MAX_SECONDS
        last_ping = time.time()
        try:
            while time.time() < deadline:
                messages = self.photo.messages_since(since, 50)
                for msg in messages:
                    payload = json.dumps(msg, ensure_ascii=False)
                    self.wfile.write(
                        f'id: {msg["id"]}\nevent: message\ndata: {payload}\n\n'.encode('utf-8'))
                    since = msg['id']
                if time.time() - last_ping >= 15:
                    self.wfile.write(b': ping\n\n')
                    last_ping = time.time()
                self.wfile.flush()
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *args):  # silence default logging
        pass


class PhotoServer:
    def __init__(self) -> None:
        self._httpd: Optional[_PhotoHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.host: str = ''
        self.port: int = 0
        self.token: str = ''
        self.public_base: str = ''
        self.cors_origin: str = '*'
        self._lock = threading.Lock()
        self._events: Deque[Dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._images: Deque[Dict[str, Any]] = deque(maxlen=MAX_IMAGES)
        self._seq = 0

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self, host: str = '0.0.0.0', port: int = 8765,
              token: str = '', public_base: str = '',
              cors_origin: str = '*') -> None:
        token = str(token or '').strip()
        public_base = str(public_base or '').strip().rstrip('/')
        cors_origin = str(cors_origin or '*').strip() or '*'
        if self.running:
            if (self.host, self.port, self.token, self.public_base,
                    self.cors_origin) == (host, port, token, public_base, cors_origin):
                return
            self.stop()
        os.makedirs(PHOTO_DIR, exist_ok=True)
        self.token = token
        self.public_base = public_base
        self.cors_origin = cors_origin
        self._httpd = _PhotoHTTPServer((host, port), _PhotoHandler, self)
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

    # ------------------------------------------------------------------
    # QQ message store (memory ring buffer, newest last)
    # ------------------------------------------------------------------
    def _public_url(self, name: str) -> str:
        rel = PHOTO_PREFIX + name
        return (self.public_base + rel) if self.public_base else rel

    def _serialize(self, event: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(event)
        out['images'] = [
            {
                'name': name,
                'url': PHOTO_PREFIX + name,
                'abs_url': self._public_url(name),
            }
            for name in event.get('images') or []
        ]
        out['videos'] = [
            {
                'name': name,
                'url': PHOTO_PREFIX + name,
                'abs_url': self._public_url(name),
            }
            for name in event.get('videos') or []
        ]
        return out

    def record_qq_message(self, ev: Dict[str, Any],
                          images: Optional[List[str]] = None,
                          videos: Optional[List[str]] = None) -> Dict[str, Any]:
        """记录一条 QQ 群消息（供网站读取）。

        ``images`` / ``videos`` 都是文件名列表（视频一般为 .mp4）。
        """
        names = [str(n) for n in (images or []) if n]
        video_names = [str(n) for n in (videos or []) if n]
        now = time.time()
        with self._lock:
            self._seq += 1
            event = {
                'id': self._seq,
                'time': now,
                'group_id': str(ev.get('groupId') or ''),
                'group_name': str(ev.get('groupName') or ''),
                'sender': str(ev.get('sender') or ''),
                'text': str(ev.get('content') or ''),
                'images': names,
                'videos': video_names,
            }
            self._events.append(event)
            for name in names:
                self._images.append({
                    'name': name,
                    'event_id': self._seq,
                    'time': now,
                    'group_id': event['group_id'],
                    'group_name': event['group_name'],
                    'sender': event['sender'],
                })
            return self._serialize(event)

    def messages_since(self, since: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [self._serialize(e) for e in self._events if e['id'] > since]
        return rows[:max(1, int(limit))]

    def recent_images(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = []
            for img in reversed(self._images):
                name = img['name']
                rows.append({
                    **img,
                    'url': PHOTO_PREFIX + name,
                    'abs_url': self._public_url(name),
                })
                if len(rows) >= max(1, int(limit)):
                    break
        return rows

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'events': len(self._events),
                'images': len(self._images),
                'last_id': self._seq,
                'base_url': self.public_base or '',
                'token_required': bool(self.token),
            }
