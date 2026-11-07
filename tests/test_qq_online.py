# -*- coding: utf-8 -*-
"""QQ 群内工具命令（[AI工具]在线玩家 / 视频 / 音乐）回归测试。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aafm_py.controller import Controller


class _FakeQq:
    def __init__(self):
        self.sent = []
        self.segments = []
        self.running = True

    def send_group(self, group_id, text, segments=None):
        self.sent.append((str(group_id), str(text)))
        self.segments.append(segments)
        return True


class _ImmediateThread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


class QqToolCommandTest(unittest.TestCase):
    def _controller(self, players=('Alice', 'Bob'), running=True,
                    trigger='[AI工具]'):
        ctrl = object.__new__(Controller)
        ctrl._engine = SimpleNamespace(running=running)
        ctrl._bot_username = 'Bot'
        ctrl._qq = _FakeQq()
        ctrl._post_ui = lambda _ev: None
        ctrl.refresh_player_list = lambda wait=0: list(players)
        ctrl.config = SimpleNamespace(qq=SimpleNamespace(toolTrigger=trigger))
        return ctrl

    def test_replies_with_online_players(self):
        ctrl = self._controller(('Bob', 'Alice', 'bot'))
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI工具]在线玩家', 'groupId': '123', 'sender': 'X'}))
        group_id, text = ctrl._qq.sent[0]
        self.assertEqual(group_id, '123')
        self.assertIn('在线玩家（2）', text)
        self.assertIn('Alice', text)
        self.assertIn('Bob', text)
        self.assertNotIn('bot', text.lower())

    def test_custom_trigger(self):
        ctrl = self._controller(trigger='[助手]')
        self.assertFalse(ctrl._handle_qq_command(
            {'content': '[AI工具]在线玩家', 'groupId': '123'}))
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[助手]在线玩家', 'groupId': '123'}))
        self.assertEqual(ctrl._qq.sent[0][0], '123')

    def test_old_ai_prefix_is_not_a_tool_command(self):
        ctrl = self._controller()
        self.assertFalse(ctrl._handle_qq_command(
            {'content': '[AI]在线玩家', 'groupId': '123'}))
        self.assertEqual(ctrl._qq.sent, [])

    def test_usage_and_unknown(self):
        ctrl = self._controller()
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI工具]', 'groupId': '123'}))
        self.assertIn('在线玩家', ctrl._qq.sent[0][1])
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI工具]不存在的命令', 'groupId': '123'}))
        self.assertIn('不认识', ctrl._qq.sent[-1][1])

    def test_video_and_music_dispatch(self):
        ctrl = self._controller()
        ctrl._qq_video_request = Mock()
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI工具]视频', 'groupId': '123', 'sender': 'Alice'}))
        ctrl._qq_video_request.assert_called_once_with('123', 'Alice')

        ctrl._qq_music_worker = Mock()
        with patch('aafm_py.controller.threading.Thread', _ImmediateThread):
            self.assertTrue(ctrl._handle_qq_command(
                {'content': '[AI工具]音乐 起风了', 'groupId': '123'}))
        ctrl._qq_music_worker.assert_called_once_with('123', '起风了')

    def test_at_qq_segments_parser(self):
        segments = Controller._qq_segments_from_text('hi @123456789 bye')
        self.assertEqual(segments[0]['type'], 'text')
        self.assertEqual(segments[1], {'type': 'at', 'data': {'qq': '123456789'}})
        self.assertEqual(segments[2]['data']['text'], ' bye')
        self.assertIsNone(Controller._qq_segments_from_text('no mention'))
        self.assertIsNone(Controller._qq_segments_from_text('mail a@12345.com'))

    def test_mc_to_qq_sends_at_segment(self):
        ctrl = object.__new__(Controller)
        ctrl.config = SimpleNamespace(qq=SimpleNamespace(
            mcToQqEnabled=True, mcTrigger='[sentQ]', mcTargetGroup='123',
            groups=[], mcCooldownSeconds=0, mcFormat='[MC] {player}：{message}'))
        ctrl._qq = _FakeQq()
        ctrl._engine = SimpleNamespace(running=True)
        ctrl._post_ui = lambda _ev: None
        ctrl._send_mc_feedback = lambda *a, **k: None
        ctrl._last_mc_to_qq_time = 0.0
        self.assertTrue(ctrl._maybe_relay_mc_to_qq(
            'Steve', '[sentQ] @123456789 来看烟花'))
        self.assertEqual(ctrl._qq.sent[0][0], '123')
        self.assertIn('@123456789', ctrl._qq.sent[0][1])
        segments = ctrl._qq.segments[0]
        self.assertEqual(segments[1], {'type': 'at', 'data': {'qq': '123456789'}})

    def test_offline_or_empty(self):
        ctrl = self._controller(running=False)
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI工具]在线玩家', 'groupId': '123'}))
        self.assertIn('未连接', ctrl._qq.sent[0][1])

        ctrl = self._controller(players=())
        self.assertTrue(ctrl._handle_qq_command(
            {'content': '[AI工具]在线玩家', 'groupId': '123'}))
        self.assertIn('没有其他在线玩家', ctrl._qq.sent[0][1])


if __name__ == '__main__':
    unittest.main()
