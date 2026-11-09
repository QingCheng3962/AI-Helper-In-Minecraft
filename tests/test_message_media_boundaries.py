"""Text and media boundaries with fake engines/downloads/providers."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from aafm_py.config import AppConfig
from aafm_py.controller import Controller, QQ_IMAGE_TOKEN, QQ_VIDEO_TOKEN, _mc_len, _mc_clip
from aafm_py import netease


class MessageMediaBoundaries(unittest.TestCase):
    def controller(self):
        c = Controller.__new__(Controller)
        c.config = AppConfig()
        c.config.qq.imagePublicBase = 'https://media.invalid'
        return c

    def test_api_full_command_utf16_edges(self):
        import run_with_api as api
        sent = []
        fake = NS(running=True, say=lambda text: sent.append(text) or True)
        for units in (249, 250, 251):
            for emoji in (False, True):
                prefix = '/msg Alice '
                rest = units - len(prefix)
                text = prefix + ('😀' * (rest // 2) + '中' * (rest % 2) if emoji else '中' * rest)
                h = api.Handler.__new__(api.Handler)
                h.path = '/say'
                h._auth = lambda: True
                h._read = lambda: {'text': text}
                replies = []
                h._json = lambda code, obj: replies.append(code)
                before = len(sent)
                with patch.object(api, '_engine', return_value=fake):
                    h.do_POST()
                self.assertEqual(replies, [200 if units <= 250 else 400], (units, emoji))
                self.assertEqual(len(sent) - before, int(units <= 250))
                if units <= 250:
                    self.assertEqual(sent[-1], text)

    def test_qq_budget_includes_template_and_url_separator(self):
        c = self.controller()
        for budget in (220, 250, 999):
            c.config.qq.maxLength = budget
            for body in ('😀' * 200, '中' * 300, 'https://media.invalid/a.png[哭]' + '😀' * 200):
                result = c._format_qq_message({'sender': 'Alice', 'groupId': '1', 'content': body})
                self.assertLessEqual(_mc_len(result), min(budget, 250))
                self.assertTrue(result.endswith('…'))
        c.config.qq.maxLength = 250
        for units in (249, 250, 251):
            for text in ('中' * units, '😀' * (units // 2) + '中' * (units % 2)):
                c.config.qq.format = '{message}'
                result = c._format_qq_message({'content': text})
                self.assertLessEqual(_mc_len(result), min(units, 250))
                self.assertEqual(result, text if units <= 250 else _mc_clip(text, 249) + '…')
        url = 'https://media.invalid/a.png'
        text = '中' * (250 - len(url) - 3) + url + '[哭]'
        result = c._format_qq_message({'content': text})
        self.assertLessEqual(_mc_len(result), 250)
        self.assertIn(url + ' ', result)
        self.assertEqual(_mc_clip('中😀文', 2), '中')
        self.assertEqual(_mc_clip('中😀文', 3), '中😀')

    def test_links_match_original_media_positions(self):
        c = self.controller()
        source = 'literal[图片] A:' + QQ_IMAGE_TOKEN + ' B:' + QQ_VIDEO_TOKEN + ' C:' + QQ_IMAGE_TOKEN
        result = c._attach_image_links({}, source,
                                       [{'name': 'one.png', 'index': 0}, {'name': 'two.png', 'index': 1}],
                                       [{'name': 'movie.mp4', 'index': 0}])
        self.assertTrue(result.startswith('literal[图片] A:[图片] https://media.invalid/pic_http/one.png'))
        self.assertLess(result.index('one.png'), result.index('movie.mp4'))
        self.assertLess(result.index('movie.mp4'), result.index('two.png'))
        self.assertEqual(result.count('/pic_http/'), 3)

    def test_failed_first_download_keeps_second_placeholder(self):
        c = self.controller()
        c._media_limits = lambda: (1024, 0)
        c._image_hint = lambda candidates: 'fake.png'
        # Use the actual download boundary and skip the failed first candidate.
        with patch.object(c, '_fetch_image_bytes', side_effect=[None, b'png']), \
             patch('aafm_py.controller.save_image_bytes', return_value='second.png'):
            saved = c._prepare_qq_media([[{'url': 'https://fake.invalid/first'}], [{'url': 'https://fake.invalid/second'}]])
        result = c._attach_image_links({}, 'A:' + QQ_IMAGE_TOKEN + ' B:' + QQ_IMAGE_TOKEN, saved, [])
        a, b = result.split(' B:')
        self.assertNotIn('second.png', a)
        self.assertIn('second.png', b)
        self.assertEqual([x.get('index') for x in saved], [1])

    def test_vip_requires_confirmed_full_length(self):
        for trial, url, expected in [(0, 'https://fake.invalid/full', True), (30, 'https://fake.invalid/trial', False),
                                     (-1, 'https://fake.invalid/unknown', False), (0, '', False)]:
            with patch.object(netease, 'eapi_post', return_value={'profile': {'nickname': 'Fake', 'vipType': 0}}), \
                 patch.object(netease, 'player_url', return_value=(url, trial, 1, 30000)):
                self.assertEqual(netease.check_login('MUSIC_U=fake')['can_play_vip'], expected)
