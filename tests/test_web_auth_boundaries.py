"""媒體別名與 Unicode 憑證回歸，僅使用本機虛構資料。"""
import os
import re
import unittest
import urllib.parse
from types import SimpleNamespace

import test_webapp as existing
from aafm_py.photo_server import _PhotoHandler


class WebAuthBoundaries(unittest.TestCase):
    setUp = existing.WebAppTest.setUp
    tearDown = existing.WebAppTest.tearDown
    _req = existing.WebAppTest._req
    _login = existing.WebAppTest._login

    def test_root_and_prefixed_media_require_session_then_work_after_login(self):
        with open(os.path.join(self.relay.pic_dir, 'p.mp4'), 'wb') as f:
            f.write(b'fake-video')
        paths = ['/p.png', '/pic_http/p.png', '/p.mp4', '/pic_http/p.mp4']
        for path in paths:
            self.assertEqual(self._req('GET', path)[0], 303, path)
        status, resp, old, _ = self._login()
        self.assertEqual(status, 303)
        cookie = resp.getheader('Set-Cookie').split(';', 1)[0]
        self.assertIsNone(self.app.sessions.get(old))
        for path in paths:
            self.assertEqual(self._req('GET', path, {'Cookie': cookie})[0], 200, path)
        sid = cookie.split('=', 1)[1]
        csrf = self.app.sessions.get(sid)['csrf']
        self.assertEqual(self._req('POST', '/logout', {'Cookie': cookie},
                                   urllib.parse.urlencode({'csrf': csrf}))[0], 303)
        self.assertEqual(self._req('GET', '/p.png', {'Cookie': cookie})[0], 303)

    def test_public_mode_and_public_routes_remain_available(self):
        self.app.require_login = False
        for path in ['/', '/api/messages', '/p.png', '/pic_http/p.png',
                     '/health', '/static/app.js', '/static/style.css']:
            self.assertEqual(self._req('GET', path)[0], 200, path)

    def test_unicode_csrf_rejects_login_and_logout_without_losing_session(self):
        _, resp, _ = self._req('GET', '/login')
        cookie = resp.getheader('Set-Cookie').split(';', 1)[0]
        sid = cookie.split('=', 1)[1]
        csrf = self.app.sessions.get(sid)['csrf']
        wrong = urllib.parse.urlencode({'csrf': '錯誤😀', 'password': 'pw'})
        self.assertEqual(self._req('POST', '/login', {'Cookie': cookie}, wrong)[0], 403)
        self.assertFalse(self.app.sessions.get(sid)['auth'])
        payload = urllib.parse.urlencode({'csrf': csrf, 'password': 'pw'})
        status, resp, _ = self._req('POST', '/login', {'Cookie': cookie}, payload)
        self.assertEqual(status, 303)
        cookie = resp.getheader('Set-Cookie').split(';', 1)[0]
        self.assertEqual(self._req('POST', '/logout', {'Cookie': cookie}, wrong)[0], 403)
        self.assertEqual(self._req('GET', '/api/messages', {'Cookie': cookie})[0], 200)


class PhotoCredentialBoundaries(unittest.TestCase):
    def handler(self, token, path='/', headers=None):
        h = _PhotoHandler.__new__(_PhotoHandler)
        h.server = SimpleNamespace(photo=SimpleNamespace(token=token))
        h.path = path
        h.headers = headers or {}
        return h

    def test_unicode_query_rejected_then_correct_token_accepted(self):
        h = self.handler('secret', '/api/qq/messages?token=%E4%B8%AD')
        self.assertFalse(h._authorized())
        h.headers = {'X-Token': 'secret'}
        self.assertTrue(h._authorized())
        h.headers = {'Authorization': 'Bearer wrong', 'X-Token': 'secret'}
        self.assertFalse(h._authorized())

    def test_unicode_config_token_and_unencodable_input(self):
        h = self.handler('密碼😀', headers={'X-Token': '密碼😀'})
        self.assertTrue(h._authorized())
        h.headers = {'X-Token': '\ud800'}
        self.assertFalse(h._authorized())
        self.assertTrue(self.handler('')._authorized())
