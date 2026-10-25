# -*- coding: utf-8 -*-
"""PhotoServer 网站 API 回归测试（QQ → 网站）。"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

from aafm_py.photo_server import PHOTO_DIR, PhotoServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class PhotoApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.server = PhotoServer()
        cls.server.start('127.0.0.1', cls.port, token='secret',
                         public_base='https://pic.example.top')
        cls.base = f'http://127.0.0.1:{cls.port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _get(self, path: str, token: str = 'secret', method: str = 'GET'):
        req = urllib.request.Request(self.base + path, method=method)
        if token is not None:
            req.add_header('X-Token', token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_requires_token(self):
        status, _headers, _body = self._get('/api/qq/messages', token=None)
        self.assertEqual(status, 401)
        status, _headers, body = self._get('/api/qq/messages')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['ok'])

    def test_records_message_and_images(self):
        self.server.record_qq_message(
            {'groupId': '123', 'groupName': '测试群', 'sender': 'Alice',
             'content': '[图片] 你好'},
            ['20261025_120000_000_abcd.jpg'])
        status, _headers, body = self._get('/api/qq/messages?since=0&limit=10')
        self.assertEqual(status, 200)
        data = json.loads(body)
        msg = data['messages'][-1]
        self.assertEqual(msg['group_name'], '测试群')
        self.assertEqual(msg['sender'], 'Alice')
        self.assertEqual(msg['images'][0]['name'], '20261025_120000_000_abcd.jpg')
        self.assertEqual(msg['images'][0]['url'],
                         '/pic_http/20261025_120000_000_abcd.jpg')
        self.assertEqual(msg['images'][0]['abs_url'],
                         'https://pic.example.top/pic_http/20261025_120000_000_abcd.jpg')

        status, _headers, body = self._get('/api/qq/images?limit=10')
        self.assertEqual(status, 200)
        images = json.loads(body)['images']
        self.assertTrue(any(i['name'] == '20261025_120000_000_abcd.jpg' for i in images))

    def test_cors_preflight_and_health(self):
        status, headers, _body = self._get('/api/qq/messages', token=None,
                                          method='OPTIONS')
        self.assertEqual(status, 204)
        self.assertEqual(headers.get('Access-Control-Allow-Origin'), '*')

        status, _headers, body = self._get('/health', token=None)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['ok'])

    def test_controller_prepares_images_and_mc_links(self):
        # 网站 API 与 MC 公屏共用同一次图片落盘，不重复下载。
        from aafm_py.controller import Controller
        ctrl = object.__new__(Controller)
        ctrl.config = SimpleNamespace(
            qq=SimpleNamespace(imagePublicBase='https://pic.example.top'))
        payload = b'\x89PNG\r\n\x1a\n' + b'test-image'
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(payload)
            path = f.name
        name = ''
        try:
            saved = ctrl._prepare_qq_images({
                'images': [[{'type': 'path', 'value': path}]]})
            self.assertEqual(len(saved), 1)
            name = saved[0]['name']
            self.assertTrue(os.path.isfile(os.path.join(PHOTO_DIR, name)))
            text = ctrl._attach_image_links(
                {}, '[图片] 你好', saved)
            self.assertIn('https://pic.example.top/pic_http/' + name, text)
        finally:
            os.unlink(path)
            if name:
                os.unlink(os.path.join(PHOTO_DIR, name))

    def test_image_file_is_served(self):
        os.makedirs(PHOTO_DIR, exist_ok=True)
        name = '_test_photo_api_%d.png' % os.getpid()
        full = os.path.join(PHOTO_DIR, name)
        payload = b'\x89PNG\r\n\x1a\n' + b'test'
        with open(full, 'wb') as f:
            f.write(payload)
        try:
            status, headers, body = self._get('/pic_http/' + name, token=None)
            self.assertEqual(status, 200)
            self.assertEqual(body, payload)
            self.assertIn('image/png', headers.get('Content-Type', ''))
        finally:
            os.unlink(full)


if __name__ == '__main__':
    unittest.main()
