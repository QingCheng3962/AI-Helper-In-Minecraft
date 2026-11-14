# -*- coding: utf-8 -*-
"""点歌 HTTP API（/api/song/*）：不用聊天也能点歌/查状态/看一起听名单。"""
from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from aafm_py.photo_server import PhotoServer


def _request(url, token='', data=None):
    headers = {}
    if token:
        headers['X-Token'] = token
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method='POST' if body else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode('utf-8') or '{}')


class SongApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = PhotoServer()
        self.requests = []
        self.server.song_request = lambda data: (self.requests.append(data)
                                                or {'ok': True, 'query': data.get('query')})
        self.server.song_state = lambda: {'connected': True,
                                          'current': {'title': '起风了', 'who': 'Alice'}}
        self.server.song_listeners = lambda: [{'player': 'Alice', 'title': '起风了',
                                               'elapsed': 12}]
        self.token = 'test-token'
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        free_port = probe.getsockname()[1]
        probe.close()
        self.server.start('127.0.0.1', free_port, token=self.token)
        self.base = 'http://127.0.0.1:%d' % free_port

    def tearDown(self) -> None:
        self.server.stop()

    def test_state_and_listeners(self):
        status, body = _request(self.base + '/api/song/state', self.token)
        self.assertEqual(status, 200)
        self.assertTrue(body['connected'])
        self.assertEqual(body['current']['title'], '起风了')
        status, body = _request(self.base + '/api/song/listeners', self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body['listeners'][0]['player'], 'Alice')

    def test_request_requires_token(self):
        status, body = _request(self.base + '/api/song/request', '',
                                {'query': '孤勇者'})
        self.assertEqual(status, 401)
        self.assertFalse(body['ok'])

    def test_request_posts_to_handler(self):
        status, body = _request(self.base + '/api/song/request', self.token,
                                {'query': '孤勇者', 'player': 'Bob', 'speed': '1.5'})
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])
        self.assertEqual(self.requests[0]['player'], 'Bob')
        self.assertEqual(self.requests[0]['speed'], '1.5')


class SongApiControllerTest(unittest.TestCase):
    def test_api_request_rejects_when_offline(self):
        from aafm_py.controller import Controller
        ctrl = object.__new__(Controller)
        ctrl._connected = False
        ctrl._engine = SimpleNamespace(running=False)
        result = ctrl._api_song_request({'query': '孤勇者'})
        self.assertFalse(result['ok'])
        self.assertIn('未连接', result['error'])

    def test_api_request_enqueues_when_online(self):
        from aafm_py.controller import Controller
        ctrl = object.__new__(Controller)
        ctrl._connected = True
        ctrl._engine = SimpleNamespace(running=True)
        ctrl.enqueue_song = Mock()
        ctrl._post_ui = Mock()
        result = ctrl._api_song_request({'query': '孤勇者', 'player': 'Bob',
                                         'speed': '1.5'})
        self.assertTrue(result['ok'])
        args = ctrl.enqueue_song.call_args[0]
        self.assertEqual(args[0], '孤勇者 1.5')
        self.assertEqual(args[1], 'Bob')


if __name__ == '__main__':
    unittest.main()
