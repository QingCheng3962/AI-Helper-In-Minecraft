# -*- coding: utf-8 -*-
"""QQ 群内命令：``[AI]在线玩家`` 回归测试。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from aafm_py.controller import Controller


class _FakeQq:
    def __init__(self):
        self.sent = []
        self.running = True

    def send_group(self, group_id, text):
        self.sent.append((str(group_id), str(text)))
        return True


class QqOnlineCommandTest(unittest.TestCase):
    def _controller(self, players=('Alice', 'Bob'), running=True):
        ctrl = object.__new__(Controller)
        ctrl._engine = SimpleNamespace(running=running)
        ctrl._bot_username = 'Bot'
        ctrl._qq = _FakeQq()
        ctrl._post_ui = lambda _ev: None
        ctrl.refresh_player_list = lambda wait=0: list(players)
        return ctrl

    def test_replies_with_online_players(self):
        ctrl = self._controller(('Bob', 'Alice', 'bot'))
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI]在线玩家', 'groupId': '123', 'sender': 'X'}))
        self.assertEqual(len(ctrl._qq.sent), 1)
        group_id, text = ctrl._qq.sent[0]
        self.assertEqual(group_id, '123')
        self.assertIn('在线玩家（2）', text)
        self.assertIn('Alice', text)
        self.assertIn('Bob', text)
        self.assertNotIn('bot', text.lower())

    def test_accepts_space_and_lowercase(self):
        ctrl = self._controller()
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[ai] 在线玩家', 'groupId': '456'}))
        self.assertEqual(ctrl._qq.sent[0][0], '456')

    def test_ignores_other_messages(self):
        ctrl = self._controller()
        self.assertFalse(ctrl._handle_qq_command(
            {'content': '[AI]你好', 'groupId': '123'}))
        self.assertEqual(ctrl._qq.sent, [])

    def test_offline_or_empty(self):
        ctrl = self._controller(running=False)
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI]在线玩家', 'groupId': '123'}))
        self.assertIn('未连接', ctrl._qq.sent[0][1])

        ctrl = self._controller(players=())
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI]在线玩家', 'groupId': '123'}))
        self.assertIn('没有其他在线玩家', ctrl._qq.sent[0][1])


if __name__ == '__main__':
    unittest.main()
