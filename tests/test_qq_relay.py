# -*- coding: utf-8 -*-
"""tools/qq_relay.py 回归测试：A(bot) -> B(relay) -> 网站。"""
from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from other_qq_pic_sent.relay import Relay, _fingerprint


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


PNG = b'\x89PNG\r\n\x1a\n' + b'relay-test'


class _Upstream:
    """极简假上游（模拟 A 的 /api/qq/messages）。"""

    def __init__(self):
        self.token = 'up-token'
        self.messages = []
        self.last_id = 0
        self.port = _free_port()
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == '/api/qq/messages':
                    if self.headers.get('X-Token') != upstream.token:
                        self._json(401, {'ok': False})
                        return
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    since = int((q.get('since') or [0])[0])
                    rows = [m for m in upstream.messages if m['id'] > since]
                    self._json(200, {'ok': True, 'last_id': upstream.last_id,
                                     'messages': rows})
                    return
                if path.startswith('/pic_http/'):
                    body = PNG
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, text, images=None, ts=None):
        self.last_id += 1
        msg = {
            'id': self.last_id,
            'time': ts if ts is not None else 1792912345.0 + self.last_id,
            'group_id': '123',
            'group_name': '测试群',
            'sender': 'Alice',
            'text': text,
            'images': images or [],
        }
        self.messages.append(msg)
        return msg

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='qq-relay-test-')
        self.up = _Upstream()
        self.relay = Relay({
            'upstream_base': f'http://127.0.0.1:{self.up.port}',
            'upstream_token': self.up.token,
            'listen_host': '127.0.0.1',
            'listen_port': _free_port(),
            'token': 'web-token',
            'public_base': 'https://pic.example.top',
            'data_dir': self.tmp,
            'poll_seconds': 0.5,
        })
        self.base = f'http://127.0.0.1:{self.relay.listen_port}'

    def tearDown(self):
        self.relay.stop()
        self.up.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _get(self, path, token='web-token'):
        req = urllib.request.Request(self.base + path)
        if token is not None:
            req.add_header('X-Token', token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_sync_download_and_serve(self):
        self.up.add('[图片] 你好', [{'name': '20261025_1.png',
                                    'url': '/pic_http/20261025_1.png'}])
        self.assertEqual(self.relay.sync_once(), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, 'pic_http',
                                                    '20261025_1.png')))

        self.relay.start_http()
        status, body = self._get('/api/qq/messages')
        self.assertEqual(status, 200)
        msg = json.loads(body)['messages'][0]
        self.assertEqual(msg['text'], '[图片] 你好')
        self.assertEqual(msg['images'][0]['url'], '/20261025_1.png')
        self.assertEqual(msg['images'][0]['abs_url'],
                         'https://pic.example.top/20261025_1.png')

        status, body = self._get('/pic_http/20261025_1.png', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG)

        # B 端对外根路径：/文件名
        status, body = self._get('/20261025_1.png', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG)

        status, _body = self._get('/api/qq/messages', token=None)
        self.assertEqual(status, 401)

    def test_duplicate_and_upstream_id_reset(self):
        msg = self.up.add('hello')
        self.assertEqual(self.relay.sync_once(), 1)
        self.assertEqual(self.relay.sync_once(), 0)   # 同一批不重复

        # 模拟 A 重启：上游 id 归零；旧消息重放 -> 指纹去重，不新增
        self.relay._upstream_since = 5
        self.relay._set_meta('upstream_since', 5)
        self.up.last_id = 0
        self.up.messages = []
        self.assertEqual(self.relay.sync_once(), 0)
        self.assertEqual(self.relay._upstream_since, 0)

        self.up.add(msg['text'], ts=msg['time'])      # 同内容同时间
        self.assertEqual(self.relay.sync_once(), 0)
        self.up.add('new message')                    # 新内容
        self.assertEqual(self.relay.sync_once(), 1)

    def test_fingerprint_stable(self):
        msg = {'group_id': '1', 'sender': 'A', 'time': 1.5, 'text': 'x'}
        self.assertEqual(_fingerprint(msg, ['a.png']),
                         _fingerprint(dict(msg), ['a.png']))


if __name__ == '__main__':
    unittest.main()
