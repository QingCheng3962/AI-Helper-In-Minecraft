# -*- coding: utf-8 -*-
"""一起听：只把「真的还在播」的玩家列出来（防刷屏/防幽灵条目）。"""
from __future__ import annotations

import unittest

from aafm_py.controller import Controller


class SongListenActiveTest(unittest.TestCase):
    def test_active_only_within_song_duration(self):
        now = 1000.0
        # 20 秒的歌，放到第 10 秒 → 还在播
        self.assertTrue(Controller._listener_active(
            {'posted_at': now - 10, 'duration': 20000}, now))
        # 放到第 25 秒 → 已经放完
        self.assertFalse(Controller._listener_active(
            {'posted_at': now - 25, 'duration': 20000}, now))
        # 刚点（0 秒）也算在播
        self.assertTrue(Controller._listener_active(
            {'posted_at': now, 'duration': 20000}, now))

    def test_unknown_duration_capped_at_five_minutes(self):
        now = 1000.0
        self.assertTrue(Controller._listener_active({'posted_at': now - 60}, now))
        self.assertFalse(Controller._listener_active({'posted_at': now - 400}, now))

    def test_missing_items_are_not_active(self):
        self.assertFalse(Controller._listener_active(None, 1000.0))
        self.assertFalse(Controller._listener_active({}, 1000.0))

    def test_elapsed_never_negative(self):
        now = 1000.0
        self.assertEqual(Controller._listener_elapsed({'posted_at': now + 50}, now), 0.0)


if __name__ == '__main__':
    unittest.main()
