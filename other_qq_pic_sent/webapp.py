#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 端 Python Web 后台：登录 + 图片墙 + 只读消息 API。

只依赖 Python 标准库。与 relay.py 同进程运行：
    relay.py 负责去 A 收消息/图片，webapp.py 负责给网站用户看。

用法：
    python other_qq_pic_sent/hash_password.py '你的密码'
    # 把输出填到 relay.json 的 web.password_hash
    python other_qq_pic_sent/webapp.py --config other_qq_pic_sent/relay.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Deque, Dict, Optional, Tuple

try:
    from .relay import Relay
except ImportError:  # 直接 python webapp.py 运行时
    from relay import Relay


DEFAULT_WEB = {
    'listen_host': '127.0.0.1',
    'listen_port': 8080,
    # 默认免登录（用户要求：打开网页就能看）。需要登录时改为 true，
    # 并配置 password_hash（hash_password.py 生成）。
    'require_login': False,
    'password_hash': '',
    'session_ttl': 86400,
    'secure_cookie': False,
    'title': 'QQ 群图片',
    'login_max_fails': 5,
    'login_window': 600,
}

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, 'web', 'templates')
STATIC = os.path.join(HERE, 'web', 'static')
COOKIE_NAME = 'qqpic_sid'


def log(msg: str) -> None:
    print(time.strftime('[%Y-%m-%d %H:%M:%S] ') + msg, flush=True)


# ----------------------------------------------------------------------
# password
# ----------------------------------------------------------------------
def make_password_hash(password: str, iterations: int = 310_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return 'pbkdf2_sha256$%d$%s$%s' % (
        iterations,
        base64.b64encode(salt).decode('ascii'),
        base64.b64encode(digest).decode('ascii'),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = str(stored).split('$', 3)
        if algo != 'pbkdf2_sha256':
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                     salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------
# sessions / rate limit
# ----------------------------------------------------------------------
class SessionStore:
    def __init__(self, ttl: int):
        self.ttl = max(300, int(ttl))
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}

    def create(self, authenticated: bool = False) -> Tuple[str, Dict[str, Any]]:
        token = secrets.token_urlsafe(32)
        session = {
            'auth': bool(authenticated),
            'csrf': secrets.token_urlsafe(32),
            'expires': time.time() + self.ttl,
        }
        with self._lock:
            self._data[token] = session
            self._cleanup_locked()
        return token, dict(session)

    def get(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        with self._lock:
            session = self._data.get(token)
            if session is None:
                return None
            if session['expires'] < time.time():
                self._data.pop(token, None)
                return None
            return dict(session)

    def rotate(self, token: str) -> Tuple[str, Dict[str, Any]]:
        """登录成功后换新 session id，防会话固定。"""
        self.destroy(token)
        return self.create(authenticated=True)

    def destroy(self, token: str) -> None:
        with self._lock:
            self._data.pop(token, None)

    def _cleanup_locked(self) -> None:
        now = time.time()
        for key in [k for k, v in self._data.items() if v['expires'] < now]:
            self._data.pop(key, None)


class LoginLimiter:
    def __init__(self, max_fails: int = 5, window: int = 600):
        self.max_fails = max(1, int(max_fails))
        self.window = max(60, int(window))
        self._lock = threading.Lock()
        self._fails: Dict[str, Deque[float]] = defaultdict(deque)

    def allowed(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            queue = self._fails[ip]
            while queue and now - queue[0] > self.window:
                queue.popleft()
            return len(queue) < self.max_fails

    def fail(self, ip: str) -> None:
        with self._lock:
            self._fails[ip].append(time.time())

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)


# ----------------------------------------------------------------------
# web app
# ----------------------------------------------------------------------
class WebApp:
    def __init__(self, relay: Relay, web_config: Dict[str, Any]):
        cfg = dict(DEFAULT_WEB)
        cfg.update({k: v for k, v in (web_config or {}).items() if v is not None})
        self.relay = relay
        self.listen_host = str(cfg['listen_host'] or '127.0.0.1')
        self.listen_port = int(cfg['listen_port'] or 8080)
        self.require_login = bool(cfg['require_login'])
        self.password_hash = str(cfg['password_hash'] or '')
        self.session_ttl = int(cfg['session_ttl'] or 86400)
        self.secure_cookie = bool(cfg['secure_cookie'])
        self.title = str(cfg['title'] or 'QQ 群图片')
        self.sessions = SessionStore(self.session_ttl)
        self.limiter = LoginLimiter(cfg['login_max_fails'], cfg['login_window'])
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def render(self, name: str, **values: Any) -> str:
        path = os.path.join(TEMPLATES, name)
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        for key, value in values.items():
            # 以 _html 结尾的变量允许传可信 HTML（服务端自己拼的）
            rendered = str(value) if key.endswith('_html') \
                else html.escape(str(value), quote=True)
            text = text.replace('{{' + key + '}}', rendered)
        return text

    def start(self) -> None:
        if self._httpd is not None:
            return
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'qqpic-web/1.0'
            protocol_version = 'HTTP/1.0'

            # ---------------- helpers ----------------
            def _headers(self, ctype: str, code: int = 200,
                         extra: Optional[Dict[str, str]] = None) -> None:
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Permissions-Policy',
                                 'geolocation=(), microphone=(), camera=()')
                self.send_header(
                    'Content-Security-Policy',
                    "default-src 'self'; img-src 'self' data:; media-src 'self'; "
                    "script-src 'self'; style-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()

            def _send(self, body: bytes, ctype: str, code: int = 200,
                      extra: Optional[Dict[str, str]] = None) -> None:
                self._headers(ctype, code, extra)
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _html(self, text: str, code: int = 200,
                      extra: Optional[Dict[str, str]] = None) -> None:
                self._send(text.encode('utf-8'),
                           'text/html; charset=utf-8', code, extra)

            def _json(self, obj: Any, code: int = 200,
                      extra: Optional[Dict[str, str]] = None) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
                headers = {'Cache-Control': 'no-store'}
                headers.update(extra or {})
                self._send(body, 'application/json; charset=utf-8', code, headers)

            def _redirect(self, location: str, extra: Optional[Dict[str, str]] = None) -> None:
                headers = {'Location': location}
                headers.update(extra or {})
                self._headers('text/plain; charset=utf-8', 303, headers)

            def _cookie(self, token: str, clear: bool = False) -> str:
                attrs = [f'{COOKIE_NAME}={" " if clear else token}',
                         'Path=/', 'HttpOnly', 'SameSite=Strict']
                if app.secure_cookie:
                    attrs.append('Secure')
                if clear:
                    attrs.append('Max-Age=0')
                return '; '.join(attrs)

            def _session_token(self) -> str:
                raw = self.headers.get('Cookie') or ''
                cookie = SimpleCookie()
                try:
                    cookie.load(raw)
                except Exception:  # noqa: BLE001
                    return ''
                morsel = cookie.get(COOKIE_NAME)
                return morsel.value if morsel else ''

            def _session(self) -> Tuple[str, Optional[Dict[str, Any]]]:
                token = self._session_token()
                return token, app.sessions.get(token)

            def _require_auth(self, api: bool = False) -> Optional[Dict[str, Any]]:
                _token, session = self._session()
                if session and session.get('auth'):
                    return session
                if api:
                    self._json({'ok': False, 'msg': 'unauthorized'}, 401)
                else:
                    self._redirect('/login')
                return None

            def _form(self) -> Dict[str, str]:
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                except (TypeError, ValueError):
                    length = 0
                if length <= 0 or length > 64 * 1024:
                    return {}
                body = self.rfile.read(length).decode('utf-8', 'replace')
                parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
                return {k: v[0] for k, v in parsed.items()}

            def _int(self, name: str, default: int, lo: int, hi: int) -> int:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    value = int((query.get(name) or [default])[0])
                except (TypeError, ValueError):
                    value = default
                return max(lo, min(hi, value))

            def _plain(self, code: int, text: str) -> None:
                self._send(text.encode('utf-8'), 'text/plain; charset=utf-8', code)

            # ---------------- routes ----------------
            def do_OPTIONS(self):  # noqa: N802
                self._headers('text/plain; charset=utf-8', 204)

            def do_GET(self):  # noqa: N802
                path = urllib.parse.urlsplit(self.path).path
                if path == '/health':
                    self._json({'ok': True, 'upstream_ok': app.relay.upstream_ok,
                                'events': app.relay.stats().get('events', 0)})
                    return
                if path.startswith('/static/'):
                    self._static(path)
                    return
                if path == '/login':
                    if not app.require_login:
                        self._redirect('/')
                        return
                    _token, session = self._session()
                    if session and session.get('auth'):
                        self._redirect('/')
                        return
                    token, session = app.sessions.create()
                    body = app.render('login.html', title=app.title,
                                      csrf=session['csrf'], error='')
                    self._html(body, extra={'Set-Cookie': self._cookie(token)})
                    return
                if path == '/logout':
                    self._redirect('/')
                    return
                if path == '/':
                    session = None
                    if app.require_login:
                        session = self._require_auth()
                        if not session:
                            return
                    logout_html = ''
                    if app.require_login:
                        csrf = (session or {}).get('csrf', '')
                        logout_html = (
                            '<form method="post" action="/logout" class="logout-form">'
                            f'<input type="hidden" name="csrf" value="{csrf}">'
                            '<button type="submit" class="logout">退出</button></form>')
                    body = app.render('index.html', title=app.title,
                                      logout_html=logout_html)
                    self._html(body)
                    return
                if path == '/api/messages':
                    if app.require_login and not self._require_auth(api=True):
                        return
                    since = self._int('since', 0, 0, 2 ** 62)
                    limit = self._int('limit', 50, 1, 100)
                    messages = app.relay.messages_since(since, limit)
                    self._json({
                        'ok': True,
                        'last_id': messages[-1]['id'] if messages else since,
                        'messages': messages,
                    })
                    return
                if path.startswith('/pic_http/'):
                    if app.require_login and not self._require_auth():
                        return
                    if not self._image(path):
                        self._plain(404, 'Not Found')
                    return
                # B 端对外图片：/文件名（不带 /pic_http/）
                if self._is_image_path(path) and self._image(path):
                    return
                self._plain(404, 'Not Found')

            def do_POST(self):  # noqa: N802
                path = urllib.parse.urlsplit(self.path).path
                if path == '/login':
                    if not app.require_login:
                        self._redirect('/')
                        return
                    self._login()
                    return
                if path == '/logout':
                    if not app.require_login:
                        self._redirect('/')
                        return
                    _token, session = self._session()
                    if session and session.get('auth'):
                        form = self._form()
                        if not hmac.compare_digest(form.get('csrf', ''), session['csrf']):
                            self._plain(403, 'CSRF check failed')
                            return
                        app.sessions.destroy(self._session_token())
                    self._redirect('/login', {'Set-Cookie': self._cookie('', clear=True)})
                    return
                self._plain(404, 'Not Found')

            # ---------------- actions ----------------
            def _login(self) -> None:
                token, session = self._session()
                if session is None:
                    # 没有会话就没有 CSRF，直接重新走登录页
                    self._redirect('/login')
                    return
                form = self._form()
                if not hmac.compare_digest(form.get('csrf', ''), session['csrf']):
                    self._plain(403, 'CSRF check failed')
                    return
                ip = self.client_address[0]
                if not app.limiter.allowed(ip):
                    app.sessions.destroy(token)
                    _new, fresh = app.sessions.create()
                    body = app.render('login.html', title=app.title,
                                      csrf=fresh['csrf'],
                                      error='尝试次数过多，请 10 分钟后再试。')
                    self._html(body, 429, {'Set-Cookie': self._cookie(_new)})
                    return
                if not app.password_hash:
                    app.sessions.destroy(token)
                    _new, fresh = app.sessions.create()
                    body = app.render('login.html', title=app.title,
                                      csrf=fresh['csrf'],
                                      error='管理员尚未配置密码哈希。')
                    self._html(body, 500, {'Set-Cookie': self._cookie(_new)})
                    return
                if not verify_password(form.get('password', ''), app.password_hash):
                    app.limiter.fail(ip)
                    app.sessions.destroy(token)
                    _new, fresh = app.sessions.create()
                    body = app.render('login.html', title=app.title,
                                      csrf=fresh['csrf'], error='密码错误。')
                    self._html(body, 401, {'Set-Cookie': self._cookie(_new)})
                    return
                app.limiter.reset(ip)
                new_token, _fresh = app.sessions.rotate(token)
                self._redirect('/', {'Set-Cookie': self._cookie(new_token)})

            def _static(self, path: str) -> None:
                name = os.path.basename(urllib.parse.unquote(path[len('/static/'):]))
                if name not in ('app.js', 'style.css'):
                    self._plain(404, 'Not Found')
                    return
                full = os.path.join(STATIC, name)
                try:
                    with open(full, 'rb') as f:
                        body = f.read()
                except OSError:
                    self._plain(404, 'Not Found')
                    return
                ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
                self._send(body, ctype, extra={'Cache-Control': 'public, max-age=3600'})

            def _is_image_path(self, path: str) -> bool:
                return path.count('/') == 1 and path.lower().endswith(
                    ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
                     '.mp4', '.m4v', '.mov'))

            def _image(self, path: str) -> bool:
                raw = path[len('/pic_http/'):] if path.startswith('/pic_http/') \
                    else path.lstrip('/')
                name = os.path.basename(urllib.parse.unquote(raw))
                if not name or name in ('.', '..'):
                    return False
                full = os.path.join(app.relay.pic_dir, name)
                if os.path.dirname(os.path.abspath(full)) != os.path.abspath(app.relay.pic_dir) \
                        or not os.path.isfile(full):
                    return False
                try:
                    size = os.path.getsize(full)
                    f = open(full, 'rb')
                except OSError:
                    return False
                ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
                self._headers(ctype, extra={
                    'Content-Length': str(size),
                    'Cache-Control': 'private, max-age=3600',
                })
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
                return True

            def log_message(self, *args):  # silence
                pass

        self._httpd = ThreadingHTTPServer((self.listen_host, self.listen_port), Handler)
        self._httpd.daemon_threads = True
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


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def load_config(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise SystemExit('配置文件必须是 JSON 对象')
    return cfg


def sync_loop(relay: Relay, stop: threading.Event) -> None:
    backoff = max(0.5, relay.poll_seconds)
    while not stop.is_set():
        try:
            added = relay.sync_once()
            if added:
                log(f'同步 {added} 条新消息（上游 id={relay._upstream_since}）')
            backoff = max(0.5, relay.poll_seconds)
        except Exception as e:  # noqa: BLE001
            relay.upstream_ok = False
            relay.last_error = str(e)
            log(f'同步失败：{e}（{backoff:.1f}s 后重试）')
            backoff = min(60.0, max(0.5, backoff * 2))
        stop.wait(backoff)


def main() -> None:
    parser = argparse.ArgumentParser(description='QQ 图片 Python Web 后台（B 端）')
    parser.add_argument('--config', required=True, help='relay.json 路径')
    parser.add_argument('--listen-host', default=None)
    parser.add_argument('--listen-port', type=int, default=None)
    parser.add_argument('--password-hash', default=None)
    parser.add_argument('--secure-cookie', action='store_true', default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    web = dict(cfg.get('web') or {})
    if args.listen_host:
        web['listen_host'] = args.listen_host
    if args.listen_port:
        web['listen_port'] = args.listen_port
    if args.password_hash:
        web['password_hash'] = args.password_hash
    if args.secure_cookie is not None:
        web['secure_cookie'] = args.secure_cookie

    if bool(web.get('require_login')) and not web.get('password_hash'):
        raise SystemExit('web.require_login=true 时必须配置 web.password_hash '
                         '（运行 hash_password.py 生成）')

    relay = Relay(cfg)
    relay.start_http()
    app = WebApp(relay, web)
    app.start()
    stop = threading.Event()
    sync_thread = threading.Thread(target=sync_loop, args=(relay, stop), daemon=True)
    sync_thread.start()
    log(f'Web 后台：http://{app.listen_host}:{app.listen_port}  '
        f'（登录：{"开启" if app.require_login else "关闭（公开访问）"}；'
        f'中继 API：http://{relay.listen_host}:{relay.listen_port}）')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log('收到 Ctrl+C，正在停止…')
    finally:
        stop.set()
        app.stop()
        relay.stop()


if __name__ == '__main__':
    main()
