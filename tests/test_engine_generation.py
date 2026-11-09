# -*- coding: utf-8 -*-
"""引擎代次：旧进程迟到的事件不能污染新会话（#52）。"""
from __future__ import annotations

import queue
import unittest
from types import SimpleNamespace

from aafm_py.bot_engine import BotEngine
from aafm_py.controller import Controller
from aafm_py.qq_engine import QqEngine


class _FakeProc:
    def __init__(self, lines=()):
        self.stdout = iter(lines)
        self.stderr = iter(())
        self.stdin = None

    def poll(self):
        return None


class EngineGenerationTest(unittest.TestCase):
    def test_reader_drops_old_generation_events(self):
        events = []
        engine = BotEngine(on_event=events.append)
        engine.proc = _FakeProc(['{"event":"spawn","username":"Bot"}\n'])
        engine._running = True
        engine._generation = 1
        engine._read_stdout(1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['__engine_generation'], 1)

        events.clear()
        engine._generation = 2
        engine.proc = _FakeProc(['{"event":"end","reason":"old-eof"}\n'])
        engine._read_stdout(1)
        self.assertEqual(events, [])

    def test_qq_reader_drops_old_generation_events(self):
        events = []
        engine = QqEngine(on_event=events.append)
        engine.proc = _FakeProc(['{"event":"status","state":"connected"}\n'])
        engine._running = True
        engine._generation = 1
        engine._read_stdout(1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['__engine_generation'], 1)

        events.clear()
        engine._generation = 2
        engine.proc = _FakeProc(['{"event":"end","reason":"old-eof"}\n'])
        engine._read_stdout(1)
        self.assertEqual(events, [])

    def test_controller_drops_stale_queued_events(self):
        ctrl = object.__new__(Controller)
        ctrl._engine = SimpleNamespace(generation=2)
        ctrl._qq = SimpleNamespace(generation=2)
        ctrl._bot_events = queue.Queue()
        ctrl._qq_events = queue.Queue()
        ctrl._post_ui = lambda ev: self.fail('stale event was processed')

        ctrl._on_bot_event({'event': 'end', '__engine_generation': 1})
        ctrl._on_qq_event({'event': 'end', '__engine_generation': 1})
        self.assertTrue(ctrl._bot_events.empty())
        self.assertTrue(ctrl._qq_events.empty())

        # 已经入队后引擎换代：处理阶段再拦一次。
        ctrl._process_bot_event(
            {'event': 'log', 'message': 'old', '__engine_generation': 1})
        ctrl._process_qq_event(
            {'event': 'log', 'message': 'old', '__engine_generation': 1})


if __name__ == '__main__':
    unittest.main()
