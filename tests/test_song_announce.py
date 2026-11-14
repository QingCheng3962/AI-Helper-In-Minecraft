# -*- coding: utf-8 -*-
"""点歌播报：谁点歌了 + 可点击「一起听」（客户端命令 /songmod join → HTTPS API）。"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from aafm_py.controller import Controller


def _controller(**overrides):
    ctrl = object.__new__(Controller)
    song = SimpleNamespace(
        announceRequest=True, announceStyle='click',
        announceText='{player} 点了一首《{title}》')
    for key, value in overrides.items():
        setattr(song, key, value)
    ctrl.config = SimpleNamespace(song=song)
    ctrl._engine = Mock()
    ctrl._engine.say = Mock(return_value=True)
    ctrl.send_chat = Mock()
    ctrl._song_private = Mock(return_value=True)
    return ctrl


class SongAnnounceTest(unittest.TestCase):
    def test_click_announce_uses_tellraw_and_client_command(self):
        ctrl = _controller()
        ctrl._announce_song_entry({'who': 'Alice', 'title': '起风了', 'artist': '冯沁苑'})
        self.assertEqual(ctrl._engine.say.call_count, 1)
        line = ctrl._engine.say.call_args[0][0]
        self.assertTrue(line.startswith('/tellraw @a '))
        component = json.loads(line[len('/tellraw @a '):])
        button = component['extra'][-1]
        self.assertEqual(button['clickEvent']['action'], 'run_command')
        self.assertEqual(button['clickEvent']['value'], '/songmod join Alice')
        self.assertIn('Alice', component['extra'][1]['text'])
        self.assertIn('起风了', component['extra'][1]['text'])
        ctrl.send_chat.assert_not_called()

    def test_plain_style_falls_back_to_chat(self):
        ctrl = _controller(announceStyle='plain')
        ctrl._announce_song_entry({'who': 'Bob', 'title': '孤勇者'})
        ctrl._engine.say.assert_not_called()
        ctrl.send_chat.assert_called_once()
        self.assertIn('Bob', ctrl.send_chat.call_args[0][0])

    def test_disabled_or_empty_is_ignored(self):
        ctrl = _controller(announceRequest=False)
        ctrl._announce_song_entry({'who': 'Bob', 'title': '孤勇者'})
        ctrl._engine.say.assert_not_called()
        ctrl.send_chat.assert_not_called()
        ctrl2 = _controller()
        ctrl2._announce_song_entry({'who': '', 'title': '孤勇者'})
        ctrl2._engine.say.assert_not_called()

    def test_tellraw_failure_falls_back(self):
        ctrl = _controller()
        ctrl._engine.say = Mock(return_value=False)
        ctrl._announce_song_entry({'who': 'Alice', 'title': '起风了'})
        ctrl.send_chat.assert_called_once()


if __name__ == '__main__':
    unittest.main()
