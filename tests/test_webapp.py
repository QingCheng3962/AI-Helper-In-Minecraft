# -*- coding: utf-8 -*-
"""other_qq_pic_sent/webapp.py 回归测试（登录 / 会话 / API / 图片 / 安全头）。"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import tempfile
import time
import unittest
import urllib.parse
from types import SimpleNamespace

from other_qq_pic_sent.relay import Relay
from other_qq_pic_sent.webapp import WebApp, make_password_hash, verify_password

PNG = b'\x89PNG\r\n\x1a\n' + b'webapp-test'


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class WebAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='qq-webapp-test-')
        self.relay = Relay({
            'upstream_base': 'http://127.0.0.1:1',
            'upstream_token': '',
            'listen_host': '127.0.0.1',
            'listen_port': _free_port(),
            'token': 'relay-token',
            'public_base': '',
            'data_dir': self.tmp,
            'poll_seconds': 0.5,
        })
        os.makedirs(self.relay.pic_dir, exist_ok=True)
        with open(os.path.join(self.relay.pic_dir, 'p.png'), 'wb') as f:
            f.write(PNG)
        with self.relay._lock:
            self.relay._conn.execute(
                'INSERT INTO messages'
                '(fingerprint, upstream_id, time, group_id, group_name, sender, text, images) '
                'VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
                ('fp1', 1, time.time(), 'g1', '测试群', 'Alice', 'hello',
                 json.dumps([{'name': 'p.png', 'url': '/pic_http/p.png'}])))
            self.relay._conn.commit()

        self.app = WebApp(self.relay, {
            'listen_host': '127.0.0.1',
            'listen_port': _free_port(),
            'require_login': True,
            'password_hash': make_password_hash('pw', 1000),
            'secure_cookie': False,
            'title': '测试图片墙',
        })
        self.app.start()
        self.conn = http.client.HTTPConnection('127.0.0.1', self.app.listen_port,
                                               timeout=5)

    def tearDown(self):
        self.conn.close()
        self.app.stop()
        self.relay.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    def _req(self, method, path, headers=None, body=None):
        self.conn.request(method, path, body=body, headers=headers or {})
        resp = self.conn.getresponse()
        data = resp.read()
        return resp.status, resp, data

    def _login(self, password='pw'):
        status, resp, body = self._req('GET', '/login')
        self.assertEqual(status, 200)
        cookie = resp.getheader('Set-Cookie', '')
        token = re.search(r'qqpic_sid=([^;]+)', cookie).group(1)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body).group(1).decode()
        payload = urllib.parse.urlencode({'csrf': csrf, 'password': password})
        status, resp2, _ = self._req(
            'POST', '/login',
            {'Content-Type': 'application/x-www-form-urlencoded',
             'Cookie': f'qqpic_sid={token}'},
            payload)
        return status, resp2, token, csrf

    def test_login_api_and_image(self):
        status, _resp, _body = self._req('GET', '/api/messages')
        self.assertEqual(status, 401)

        status, resp, token, _csrf = self._login()
        self.assertEqual(status, 303)
        new_cookie = resp.getheader('Set-Cookie', '')
        new_token = re.search(r'qqpic_sid=([^;]+)', new_cookie).group(1)
        self.assertNotEqual(token, new_token)

        status, _resp, body = self._req(
            'GET', '/api/messages?since=0&limit=10',
            {'Cookie': f'qqpic_sid={new_token}'})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['messages'][0]['text'], 'hello')

        status, resp, body = self._req(
            'GET', '/pic_http/p.png', {'Cookie': f'qqpic_sid={new_token}'})
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG)
        self.assertEqual(resp.getheader('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(resp.getheader('X-Frame-Options'), 'DENY')
        self.assertIn('frame-ancestors', resp.getheader('Content-Security-Policy', ''))

    def test_image_requires_login(self):
        status, _resp, _body = self._req('GET', '/pic_http/p.png')
        self.assertEqual(status, 303)

    def test_wrong_csrf_rejected(self):
        status, resp, _body = self._req('GET', '/login')
        token = re.search(r'qqpic_sid=([^;]+)', resp.getheader('Set-Cookie')).group(1)
        payload = urllib.parse.urlencode({'csrf': 'bad', 'password': 'pw'})
        status, _resp, _body = self._req(
            'POST', '/login',
            {'Content-Type': 'application/x-www-form-urlencoded',
             'Cookie': f'qqpic_sid={token}'},
            payload)
        self.assertEqual(status, 403)

    def test_no_login_public_mode_and_root_image(self):
        app = WebApp(self.relay, {
            'listen_host': '127.0.0.1',
            'listen_port': _free_port(),
            'require_login': False,
            'password_hash': '',
            'secure_cookie': False,
            'title': '公开图片墙',
        })
        app.start()
        conn = http.client.HTTPConnection('127.0.0.1', app.listen_port, timeout=5)
        try:
            def req(path):
                conn.request('GET', path)
                resp = conn.getresponse()
                return resp.status, resp.read()

            status, body = req('/')
            self.assertEqual(status, 200)
            self.assertIn('公开图片墙'.encode('utf-8'), body)

            status, body = req('/api/messages?since=0&limit=10')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['messages'][0]['text'], 'hello')

            # 根路径图片：/p.png（用户要求的 pic.qingchengsep.top/图片名）
            status, body = req('/p.png')
            self.assertEqual(status, 200)
            self.assertEqual(body, PNG)

            # 老路径也兼容
            status, body = req('/pic_http/p.png')
            self.assertEqual(status, 200)
            self.assertEqual(body, PNG)

            status, _body = req('/login')
            self.assertEqual(status, 303)
        finally:
            conn.close()
            app.stop()

    def test_password_hash(self):
        hashed = make_password_hash('secret', 1000)
        self.assertTrue(verify_password('secret', hashed))
        self.assertFalse(verify_password('wrong', hashed))
        self.assertFalse(verify_password('secret', 'broken'))


if __name__ == '__main__':
    unittest.main()
