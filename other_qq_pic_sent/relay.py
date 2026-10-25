#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 图片/消息中继（方案二：A 跑 bot，B 跑本程序）。

作用：
    A（内网机器人）  ──HTTP──▶  B（本程序）  ──HTTP──▶  网站 / Cloudflare Tunnel

本程序在 B 上：
  · 定时从 A 拉取 /api/qq/messages（带 Token）；
  · 把图片下载到本地 data_dir/pic_http/（文件名保持不变）；
  · 对外提供与 A 相同格式的 /api/qq/messages、/api/qq/images、
    /api/qq/stream(SSE)、/health、/pic_http/<file>；
  · 消息落 SQLite，重启不丢；用指纹去重，A 重启导致 id 归零也不会重复。

只依赖 Python 标准库（Python 3.9+）。

用法：
    python other_qq_pic_sent/relay.py --config other_qq_pic_sent/relay.example.json
    python other_qq_pic_sent/relay.py --upstream http://192.168.1.23:8765 \
        --upstream-token A的Token --listen-port 18765 --token 给网站的Token

常驻运行（Linux systemd）示例见 docs/QQ图片网站接入.md。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_CONFIG = {
    'upstream_base': 'http://127.0.0.1:8765',
    'upstream_token': '',
    'listen_host': '127.0.0.1',
    'listen_port': 18765,
    'token': '',
    'public_base': '',
    'data_dir': os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'relay_data'),
    'poll_seconds': 2.0,
    'request_timeout': 15.0,
    'cors_origin': '*',
}

MAX_BODY = 64 * 1024
SSE_MAX_SECONDS = 30 * 60


def log(msg: str) -> None:
    print(time.strftime('[%Y-%m-%d %H:%M:%S] ') + msg, flush=True)


def _json_request(url: str, token: str = '', timeout: float = 15.0) -> Dict[str, Any]:
    headers = {'Accept': 'application/json'}
    if token:
        headers['X-Token'] = token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _bytes_request(url: str, token: str = '', timeout: float = 30.0) -> bytes:
    headers = {'User-Agent': 'qq-relay/1.0'}
    if token:
        headers['X-Token'] = token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fingerprint(msg: Dict[str, Any], names: List[str]) -> str:
    raw = json.dumps([
        str(msg.get('group_id') or ''),
        str(msg.get('sender') or ''),
        float(msg.get('time') or 0),
        str(msg.get('text') or ''),
        names,
    ], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _safe_name(name: str) -> str:
    name = os.path.basename(str(name or '').replace('\\', '/'))
    if not name or name in ('.', '..'):
        return ''
    if any(ch in name for ch in ('/', '\\', '\x00')):
        return ''
    return name


def re_image_name(name: str) -> bool:
    return str(name or '').lower().endswith(
        ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'))


class Relay:
    """消息/图片同步 + 对外 HTTP 服务。"""

    def __init__(self, config: Dict[str, Any]):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({k: v for k, v in (config or {}).items() if v is not None})
        self.upstream_base = str(cfg['upstream_base']).rstrip('/')
        self.upstream_token = str(cfg['upstream_token'] or '')
        self.listen_host = str(cfg['listen_host'] or '127.0.0.1')
        self.listen_port = int(cfg['listen_port'] or 18765)
        self.token = str(cfg['token'] or '')
        self.public_base = str(cfg['public_base'] or '').strip().rstrip('/')
        self.data_dir = os.path.abspath(str(cfg['data_dir']))
        self.pic_dir = os.path.join(self.data_dir, 'pic_http')
        self.poll_seconds = max(0.5, float(cfg['poll_seconds'] or 2.0))
        self.request_timeout = max(3.0, float(cfg['request_timeout'] or 15.0))
        self.cors_origin = str(cfg['cors_origin'] or '*')

        os.makedirs(self.pic_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, 'relay.db')
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._init_db()

        self._httpd: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.upstream_ok = False
        self.last_sync = 0.0
        self.last_error = ''
        self._upstream_since = self._meta_int('upstream_since', 0)

    # ------------------------------------------------------------------
    # storage
    # ------------------------------------------------------------------
    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript('''
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT UNIQUE,
                    upstream_id INTEGER,
                    time        REAL,
                    group_id    TEXT,
                    group_name  TEXT,
                    sender      TEXT,
                    text        TEXT,
                    images      TEXT
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(time);
            ''')
            self._conn.commit()

    def _meta_int(self, key: str, default: int = 0) -> int:
        with self._lock:
            row = self._conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        try:
            return int(row[0]) if row else default
        except (TypeError, ValueError):
            return default

    def _set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                'INSERT INTO meta(key, value) VALUES(?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, str(value)))
            self._conn.commit()

    # ------------------------------------------------------------------
    # upstream sync
    # ------------------------------------------------------------------
    def _download_image(self, name: str, url: str) -> bool:
        name = _safe_name(name)
        if not name or not url:
            return False
        full = os.path.join(self.pic_dir, name)
        if os.path.isfile(full) and os.path.getsize(full) > 0:
            return True
        src = url if re_url(url) else self.upstream_base + url
        try:
            data = _bytes_request(src, self.upstream_token, self.request_timeout * 2)
        except Exception as e:  # noqa: BLE001
            log(f'图片下载失败 {name}: {e}')
            return False
        if not data:
            return False
        tmp = full + '.part'
        try:
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, full)
            return True
        except OSError as e:
            log(f'图片保存失败 {name}: {e}')
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False

    def _fetch_messages(self, since: int) -> Dict[str, Any]:
        url = (self.upstream_base + '/api/qq/messages?since=%d&limit=100' % since)
        return _json_request(url, self.upstream_token, self.request_timeout)

    def sync_once(self) -> int:
        """拉取一次；返回本轮新增消息数。"""
        data = self._fetch_messages(self._upstream_since)
        if not data.get('ok'):
            raise RuntimeError('上游返回失败: ' + str(data)[:200])
        messages = data.get('messages') or []
        last_id = int(data.get('last_id') or 0)

        # A 重启后 id 会从 1 重新开始：检测到回绕就下一轮从 0 重拉，
        # 已入库的消息靠 fingerprint 去重。
        if last_id < self._upstream_since:
            log(f'上游消息 id 回绕（{self._upstream_since} -> {last_id}），'
                f'下一轮从 0 重新同步（自动去重）。')
            self._upstream_since = 0
            self._set_meta('upstream_since', 0)
            return 0

        inserted = 0
        max_seen = self._upstream_since
        for msg in messages:
            images_json: List[Dict[str, str]] = []
            for img in msg.get('images') or []:
                name = _safe_name(str(img.get('name') or ''))
                if not name:
                    continue
                url = str(img.get('url') or ('/pic_http/' + name))
                images_json.append({'name': name, 'url': url})
            names = [x['name'] for x in images_json]
            fp = _fingerprint(msg, names)
            with self._lock:
                cur = self._conn.execute(
                    'INSERT OR IGNORE INTO messages'
                    '(fingerprint, upstream_id, time, group_id, group_name, sender, text, images) '
                    'VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                    (fp, int(msg.get('id') or 0), float(msg.get('time') or time.time()),
                     str(msg.get('group_id') or ''), str(msg.get('group_name') or ''),
                     str(msg.get('sender') or ''), str(msg.get('text') or ''),
                     json.dumps(images_json, ensure_ascii=False)))
                if cur.rowcount:
                    inserted += 1
                self._conn.commit()
            for img in images_json:
                self._download_image(img['name'], img['url'])
            max_seen = max(max_seen, int(msg.get('id') or 0))

        # 重试最近 100 条里缺失的图片
        self._retry_missing_images(100)

        self._upstream_since = max(max_seen, last_id)
        self._set_meta('upstream_since', self._upstream_since)
        self.upstream_ok = True
        self.last_error = ''
        self.last_sync = time.time()
        return inserted

    def _retry_missing_images(self, limit: int) -> None:
        with self._lock:
            rows = self._conn.execute(
                'SELECT images FROM messages ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        for (raw,) in rows:
            try:
                images = json.loads(raw or '[]')
            except ValueError:
                continue
            for img in images:
                name = _safe_name(str(img.get('name') or ''))
                if name and not os.path.isfile(os.path.join(self.pic_dir, name)):
                    self._download_image(name, str(img.get('url') or ''))

    # ------------------------------------------------------------------
    # local queries
    # ------------------------------------------------------------------
    def _public_url(self, name: str) -> str:
        # B 端对外图片地址：https://域名/文件名（不带 /pic_http/）
        rel = '/' + name
        return (self.public_base + rel) if self.public_base else rel

    def _serialize(self, row: Tuple) -> Dict[str, Any]:
        (mid, _fp, _uid, ts, gid, gname, sender, text, raw_images) = row
        try:
            images = json.loads(raw_images or '[]')
        except ValueError:
            images = []
        out_images = []
        for img in images:
            name = str(img.get('name') or '')
            out_images.append({
                'name': name,
                'url': '/' + name,
                'abs_url': self._public_url(name),
            })
        return {
            'id': int(mid), 'time': float(ts or 0),
            'group_id': gid or '', 'group_name': gname or '',
            'sender': sender or '', 'text': text or '',
            'images': out_images,
        }

    def messages_since(self, since: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                'SELECT id, fingerprint, upstream_id, time, group_id, group_name, '
                'sender, text, images FROM messages WHERE id > ? ORDER BY id LIMIT ?',
                (int(since), max(1, int(limit)))).fetchall()
        return [self._serialize(r) for r in rows]

    def recent_images(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                'SELECT id, fingerprint, upstream_id, time, group_id, group_name, '
                'sender, text, images FROM messages ORDER BY id DESC LIMIT 200').fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            for img in self._serialize(row)['images']:
                out.append({**img, 'event_id': int(row[0]), 'time': float(row[3] or 0),
                            'group_id': row[4] or '', 'group_name': row[5] or '',
                            'sender': row[6] or ''})
                if len(out) >= max(1, int(limit)):
                    return out
        return out

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            count = self._conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
            last = self._conn.execute('SELECT MAX(id) FROM messages').fetchone()[0] or 0
        return {
            'events': int(count),
            'last_id': int(last),
            'upstream_ok': self.upstream_ok,
            'upstream_base': self.upstream_base,
            'last_sync': self.last_sync,
            'last_error': self.last_error,
            'base_url': self.public_base,
            'token_required': bool(self.token),
        }

    # ------------------------------------------------------------------
    # HTTP server
    # ------------------------------------------------------------------
    def start_http(self) -> None:
        relay = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'qq-relay/1.0'
            protocol_version = 'HTTP/1.0'

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

            def _cors(self) -> None:
                self.send_header('Access-Control-Allow-Origin', relay.cors_origin or '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
                self.send_header('Access-Control-Allow-Headers',
                                 'Authorization, X-Token, X-Api-Token, Content-Type')

            def _authorized(self) -> bool:
                if not relay.token:
                    return True
                supplied = self.headers.get('X-Token') or self.headers.get('X-Api-Token') or ''
                auth = self.headers.get('Authorization') or ''
                if not supplied and auth.lower().startswith('bearer '):
                    supplied = auth[7:].strip()
                if not supplied:
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    supplied = (query.get('token') or [''])[0]
                return supplied == relay.token

            def _int_param(self, name: str, default: int, lo: int, hi: int) -> int:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    value = int((query.get(name) or [default])[0])
                except (TypeError, ValueError):
                    value = default
                return max(lo, min(hi, value))

            def do_OPTIONS(self):  # noqa: N802
                self.send_response(204)
                self.send_header('Content-Length', '0')
                self._cors()
                self.end_headers()

            def do_HEAD(self):  # noqa: N802
                self._serve_file(head_only=True)

            def do_GET(self):  # noqa: N802
                path = urllib.parse.urlsplit(self.path).path
                if path.startswith('/pic_http/'):
                    self._serve_file()
                    return
                if path == '/health':
                    self._json(200, {'ok': True, **relay.stats()})
                    return
                if path not in ('/api/qq/messages', '/api/qq/images', '/api/qq/stream'):
                    # 兼容根路径图片：/文件名（B 端对外）
                    name = _safe_name(urllib.parse.unquote(path.lstrip('/')))
                    if name and re_image_name(name):
                        self._serve_file()
                        return
                    self.send_error(404, 'Not Found')
                    return
                if not self._authorized():
                    self._json(401, {'ok': False, 'msg': 'unauthorized'})
                    return
                if path == '/api/qq/messages':
                    since = self._int_param('since', 0, 0, 2 ** 62)
                    limit = self._int_param('limit', 50, 1, 200)
                    rows = relay.messages_since(since, limit)
                    self._json(200, {
                        'ok': True,
                        'last_id': rows[-1]['id'] if rows else since,
                        'messages': rows,
                    })
                    return
                if path == '/api/qq/images':
                    limit = self._int_param('limit', 50, 1, 200)
                    self._json(200, {'ok': True, 'images': relay.recent_images(limit)})
                    return
                self._stream()

            def _stream(self) -> None:
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
                    while time.time() < deadline and not relay._stop.is_set():
                        rows = relay.messages_since(since, 50)
                        for msg in rows:
                            payload = json.dumps(msg, ensure_ascii=False)
                            self.wfile.write(
                                f'id: {msg["id"]}\nevent: message\ndata: {payload}\n\n'
                                .encode('utf-8'))
                            since = msg['id']
                        if time.time() - last_ping >= 15:
                            self.wfile.write(b': ping\n\n')
                            last_ping = time.time()
                        self.wfile.flush()
                        time.sleep(0.5)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def _serve_file(self, head_only: bool = False) -> None:
                path = urllib.parse.urlsplit(self.path).path
                if path.startswith('/pic_http/'):
                    raw = path[len('/pic_http/'):]
                else:
                    raw = path.lstrip('/')
                name = _safe_name(urllib.parse.unquote(raw))
                full = os.path.join(relay.pic_dir, name) if name else ''
                if not name or not os.path.isfile(full):
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

            def log_message(self, *args):  # silence
                pass

        self._httpd = ThreadingHTTPServer((self.listen_host, self.listen_port), Handler)
        self._httpd.daemon_threads = True
        self._http_thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._http_thread.start()

    def stop(self) -> None:
        self._stop.set()
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass

    def serve_forever(self) -> None:
        self.start_http()
        log(f'中继已启动：http://{self.listen_host}:{self.listen_port}  '
            f'-> 上游 {self.upstream_base}')
        backoff = self.poll_seconds
        while not self._stop.is_set():
            try:
                added = self.sync_once()
                if added:
                    log(f'同步 {added} 条新消息（上游 id={self._upstream_since}）')
                backoff = self.poll_seconds
            except Exception as e:  # noqa: BLE001
                self.upstream_ok = False
                self.last_error = str(e)
                log(f'同步失败：{e}（{backoff:.1f}s 后重试）')
                backoff = min(60.0, max(self.poll_seconds, backoff * 2))
            self._stop.wait(backoff)


def re_url(value: str) -> bool:
    return value.startswith('http://') or value.startswith('https://')


def load_config(path: str) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit('配置文件必须是 JSON 对象')
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description='QQ 图片/消息中继（A bot -> B server）')
    parser.add_argument('--config', default='', help='JSON 配置文件路径')
    parser.add_argument('--upstream', default=None, help='A 的地址，如 http://192.168.1.23:8765')
    parser.add_argument('--upstream-token', default=None, help='A 的 webApiToken')
    parser.add_argument('--listen-host', default=None, help='B 监听地址（默认 127.0.0.1）')
    parser.add_argument('--listen-port', type=int, default=None, help='B 监听端口（默认 18765）')
    parser.add_argument('--token', default=None, help='网站访问 B 用的 Token')
    parser.add_argument('--public-base', default=None, help='图片公网前缀')
    parser.add_argument('--data-dir', default=None, help='数据目录（默认 relay_data）')
    parser.add_argument('--poll', type=float, default=None, help='轮询秒数（默认 2）')
    args = parser.parse_args()

    cfg = load_config(args.config)
    for key, value in (
            ('upstream_base', args.upstream),
            ('upstream_token', args.upstream_token),
            ('listen_host', args.listen_host),
            ('listen_port', args.listen_port),
            ('token', args.token),
            ('public_base', args.public_base),
            ('data_dir', args.data_dir),
            ('poll_seconds', args.poll)):
        if value is not None:
            cfg[key] = value

    relay = Relay(cfg)
    try:
        relay.serve_forever()
    except KeyboardInterrupt:
        log('收到 Ctrl+C，正在停止…')
    finally:
        relay.stop()


if __name__ == '__main__':
    main()
