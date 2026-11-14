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

    def test_response_is_compact_json(self):
        # Mod 端解析依赖 {"ok":true}（无空格）——服务端必须输出紧凑 JSON
        req = urllib.request.Request(self.base + '/api/song/ping',
                                     headers={'X-Token': self.token})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode('utf-8')
        self.assertIn('"ok":true', raw)
        self.assertNotIn('"ok": true', raw)

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
    @staticmethod
    def _ctrl():
        from aafm_py.controller import Controller
        ctrl = object.__new__(Controller)
        ctrl.config = SimpleNamespace(
            song=SimpleNamespace(neteaseCookie='MUSIC_U=abc'))
        ctrl._song_seq = 0
        ctrl._song_current = None
        ctrl._remember_listener = Mock()
        ctrl._post_ui = Mock()
        ctrl._announce_song_entry = Mock()
        ctrl.strip_speed_token = Controller.strip_speed_token.__get__(ctrl)
        return ctrl

    def test_api_request_resolves_with_server_cookie_and_returns_playback(self):
        from unittest.mock import patch
        ctrl = self._ctrl()
        resolved = {'id': '123', 'title': '起风了', 'artists': ['冯沁苑'],
                    'url': 'https://music.example/1.mp3', 'duration': 219000,
                    'trial': 0}
        with patch('aafm_py.netease.resolve', return_value=resolved) as resolve, \
                patch('aafm_py.netease.lyric', return_value={'lrc': '[00:00.00]hi'}) as lyric:
            result = ctrl._api_song_request({'query': '起风了 1.5', 'player': 'Bob'})
        self.assertTrue(result['ok'])
        data = result['data']
        self.assertEqual(data['title'], '起风了')
        self.assertEqual(data['artist'], '冯沁苑')
        self.assertEqual(data['url'], 'https://music.example/1.mp3')
        self.assertEqual(data['duration'], 219000)
        self.assertEqual(data['lyrics'], '[00:00.00]hi')
        resolve.assert_called_once_with('起风了', 'MUSIC_U=abc')
        lyric.assert_called_once_with('123', 'MUSIC_U=abc')
        ctrl._remember_listener.assert_called_once()

    def test_api_request_returns_error_when_not_found(self):
        from unittest.mock import patch
        ctrl = self._ctrl()
        with patch('aafm_py.netease.resolve', return_value=None):
            result = ctrl._api_song_request({'query': '不存在'})
        self.assertFalse(result['ok'])
        self.assertIn('没找到', result['error'])
if __name__ == '__main__':
    unittest.main()
