# -*- coding: utf-8 -*-
"""tools/qq_relay.py 回归测试：A(bot) -> B(relay) -> 网站。"""
from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import time
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
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'relay-video'


class _Upstream:
    """极简假上游（模拟 A 的 /api/qq/messages）。"""

    def __init__(self):
        self.token = 'up-token'
        self.messages = []
        self.last_id = 0
        self.instance = 'inst-1'
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
                                     'instance': upstream.instance,
                                     'messages': rows})
                    return
                if path.startswith('/pic_http/'):
                    is_video = path.lower().endswith(('.mp4', '.m4v', '.mov'))
                    if 'big' in path.lower():
                        body = b'x' * (2 * 1024 * 1024)
                    else:
                        body = MP4 if is_video else PNG
                    self.send_response(200)
                    self.send_header('Content-Type',
                                     'video/mp4' if is_video else 'image/png')
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

    def add(self, text, images=None, ts=None, videos=None):
        self.last_id += 1
        msg = {
            'id': self.last_id,
            'time': ts if ts is not None else time.time() + self.last_id,
            'group_id': '123',
            'group_name': '测试群',
            'sender': 'Alice',
            'text': text,
            'images': images or [],
            'videos': videos or [],
        }
        self.messages.append(msg)
        return msg

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def restart(self, instance='inst-2'):
        """模拟 A 重启：清空消息、id 归零、实例 ID 变化。"""
        self.messages = []
        self.last_id = 0
        self.instance = instance


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

    def _get(self, path, token='web-token', headers=None):
        req = urllib.request.Request(self.base + path)
        if token is not None:
            req.add_header('X-Token', token)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_sync_download_and_serve(self):
        self.up.add('[图片] 你好', [{'name': '20261025_1.png',
                                    'url': '/pic_http/20261025_1.png'}])
        self.assertEqual(self.relay.sync_once(), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, 'pic_http',
                                                    '20261025_1.png')))

        self.relay.start_http()
        status, _headers, body = self._get('/api/qq/messages')
        self.assertEqual(status, 200)
        msg = json.loads(body)['messages'][0]
        self.assertEqual(msg['text'], '[图片] 你好')
        self.assertEqual(msg['images'][0]['url'], '/20261025_1.png')
        self.assertEqual(msg['images'][0]['abs_url'],
                         'https://pic.example.top/20261025_1.png')

        status, _headers, body = self._get('/pic_http/20261025_1.png', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG)

        # B 端对外根路径：/文件名
        status, _headers, body = self._get('/20261025_1.png', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG)

        status, _headers, _body = self._get('/api/qq/messages', token=None)
        self.assertEqual(status, 401)

    def test_sync_download_and_serve_mp4(self):
        self.up.add('[视频] 看这个', videos=[{
            'name': '20261025_2.mp4', 'url': '/pic_http/20261025_2.mp4'}])
        self.assertEqual(self.relay.sync_once(), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, 'pic_http',
                                                    '20261025_2.mp4')))

        self.relay.start_http()
        status, _headers, body = self._get('/api/qq/messages')
        self.assertEqual(status, 200)
        msg = json.loads(body)['messages'][0]
        self.assertEqual(msg['videos'][0]['url'], '/20261025_2.mp4')
        self.assertEqual(msg['videos'][0]['abs_url'],
                         'https://pic.example.top/20261025_2.mp4')

        status, _headers, body = self._get('/20261025_2.mp4', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body, MP4)

        status, headers, body = self._get(
            '/20261025_2.mp4', token=None,
            headers={'Range': 'bytes=4-11'})
        self.assertEqual(status, 206)
        self.assertEqual(body, MP4[4:12])
        self.assertEqual(headers.get('Content-Range'),
                         f'bytes 4-11/{len(MP4)}')

    def test_media_size_limit_and_cleanup(self):
        self.relay.media_max_mb = 1
        self.up.add('big', images=[{'name': 'big.png',
                                    'url': '/pic_http/big.png'}])
        self.assertEqual(self.relay.sync_once(), 1)
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, 'pic_http', 'big.png')))

        # 清理：把消息和文件都设成 10 天前，keep_days=1 应删除。
        self.up.messages = []
        self.up.last_id = 0
        self.relay._upstream_since = 0
        self.relay.media_max_mb = 100
        self.relay.media_keep_days = 0     # 先让 sync 不要顺手清掉它
        self.up.add('[图片] old', images=[{'name': 'old.png',
                                           'url': '/pic_http/old.png'}],
                    ts=time.time() - 10 * 86400)
        self.relay.sync_once()
        old_path = os.path.join(self.tmp, 'pic_http', 'old.png')
        self.assertTrue(os.path.isfile(old_path))
        self.relay.media_keep_days = 1
        self.relay.cleanup_media(force=True)
        self.assertFalse(os.path.exists(old_path))
        msgs = self.relay.messages_since(0, 50)
        self.assertFalse(any(m['text'] == '[图片] old' for m in msgs))

    def test_upstream_restart_resets_cursor(self):
        self.up.add('one')
        self.up.add('two')
        self.assertEqual(self.relay.sync_once(), 2)
        self.assertEqual(self.relay._upstream_since, 2)

        self.up.restart(instance='inst-2')
        self.up.add('new-after-restart')
        self.assertEqual(self.relay.sync_once(), 1)
        texts = [m['text'] for m in self.relay.messages_since(0, 50)]
        self.assertIn('new-after-restart', texts)
        self.assertEqual(self.relay._upstream_since, 1)

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
