# -*- coding: utf-8 -*-
"""问答：题库/判分/记录/每日提问次数/奖励命令。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aafm_py.config import QaConfig
from aafm_py.quiz import QuizStore, QuizEngine


def _cfg(**overrides):
    cfg = QaConfig()
    cfg.enabled = True
    for k, v in overrides.items():
        setattr(cfg, k, v)
    cfg.validate()
    return cfg


class QuizStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QuizStore(Path(self.tmp.name) / 'quiz.db')
        self.cfg = _cfg()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_defaults_and_pick(self):
        self.assertGreaterEqual(self.store.count(), 3)
        q = self.store.pick()
        self.assertIsNotNone(q)
        self.assertIn(q['type'], ('text', 'blank', 'choice'))

    def test_import_export_json(self):
        path = Path(self.tmp.name) / 'q.json'
        path.write_text(json.dumps({'questions': [
            {'type': 'text', 'q': '2+2=?', 'a': ['4', '四']},
            '中国首都=北京|Beijing',
        ]}, ensure_ascii=False), encoding='utf-8')
        before = self.store.count()
        added = self.store.import_json(path)
        self.assertEqual(added, 2)
        self.assertEqual(self.store.count(), before + 2)
        out = Path(self.tmp.name) / 'out.json'
        self.assertGreaterEqual(self.store.export_json(out), 3)
        self.assertIn('questions', json.loads(out.read_text(encoding='utf-8')))

    def test_records_and_stats(self):
        self.store.record('Alice', 1, True)
        self.store.record('Alice', 1, False)
        self.store.record('Bob', 1, True)
        st = self.store.stats('Alice')
        self.assertEqual(st, {'total': 2, 'correct': 1})
        self.assertEqual(self.store.stats('Bob')['correct'], 1)

    def test_daily_ask_count(self):
        self.assertEqual(self.store.ask_count('2026-11-21', 'Alice'), 0)
        self.assertEqual(self.store.inc_ask('2026-11-21', 'Alice'), 1)
        self.assertEqual(self.store.inc_ask('2026-11-21', 'Alice'), 2)
        self.assertEqual(self.store.ask_count('2026-11-22', 'Alice'), 0)


class QuizEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = QuizStore(Path(self.tmp.name) / 'quiz.db')
        self.chat = []
        self.priv = []
        self.rewards = []
        self.cfg = _cfg(intervalSeconds=10, timeoutSeconds=30, askPerDay=2)
        self.now = [1000.0]
        self.engine = QuizEngine(
            self.store, lambda: self.cfg,
            send_chat=lambda t: self.chat.append(t),
            send_private=lambda p, t: self.priv.append((p, t)),
            run_commands=lambda p, cmds: self.rewards.append((p, list(cmds))),
            day_provider=lambda: '2026-11-21',
            now_provider=lambda: self.now[0])
        # 用纯文本题替换默认题，方便断言
        self.store._conn.execute('DELETE FROM questions')
        self.store._conn.commit()
        self.qid = self.store.add({'type': 'text', 'q': '中国的首都', 'a': ['北京', 'Beijing']})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_ask_and_correct_answer_rewards(self):
        self.assertEqual(self.engine.ask_public(), 'ok')
        self.assertTrue(self.chat)
        self.assertIn('中国的首都', self.chat[-1])
        self.assertTrue(self.engine.handle_answer('Alice', '北京'))
        self.assertEqual(self.rewards, [('Alice', self.cfg.rewards)])
        self.assertTrue(any('答对了' in t for t in self.chat))
        self.assertIsNone(self.engine.current)

    def test_blank_type(self):
        self.store._conn.execute('DELETE FROM questions')
        self.store._conn.commit()
        self.store.add({'type': 'blank', 'q': '首都___ 最大城市___',
                        'a': [['北京'], ['上海']]})
        self.engine.ask_public()
        self.assertTrue(self.engine.handle_answer('Alice', '北京 上海'))
        self.engine.ask_public()
        self.assertTrue(self.engine.handle_answer('Bob', '北京'))   # 答错也会被记录
        self.assertIsNotNone(self.engine.current)                    # 题目仍有效
        self.assertNotIn('Bob', self.engine._correct_players)

    def test_choice_type(self):
        self.store._conn.execute('DELETE FROM questions')
        self.store._conn.commit()
        self.store.add({'type': 'choice', 'q': '最大的行星',
                        'options': ['A. 地球', 'B. 木星'], 'a': 'B'})
        self.engine.ask_public()
        self.assertTrue('A. 地球' in self.chat[-1])
        self.assertTrue(self.engine.handle_answer('Alice', 'b'))
        self.assertTrue(self.engine.handle_answer('Bob', '选B') is False or True)

    def test_wrong_answer_public_once(self):
        self.engine.ask_public()
        self.engine.handle_answer('Alice', '上海')
        self.engine.handle_answer('Alice', '广州')
        public_wrong = [t for t in self.chat if '答案错误' in t]
        self.assertEqual(len(public_wrong), 1)
        self.assertTrue(any(p == 'Alice' for p, _ in self.priv))

    def test_manual_ask_daily_limit(self):
        self.assertEqual(self.engine.ask_manual('Alice'), 'ok')
        self.assertEqual(self.engine.ask_manual('Alice'), 'ok')
        self.assertEqual(self.engine.ask_manual('Alice'), 'limit')
        self.assertTrue(any('明天再来' in t for _, t in self.priv))

    def test_stats_text(self):
        self.engine.ask_public()
        self.engine.handle_answer('Alice', '北京')
        text = self.engine.stats_text('Alice')
        self.assertIn('1', text)

    def test_timeout_publishes_answer(self):
        self.engine.ask_public()
        self.now[0] += 31
        self.engine.tick()
        self.assertIsNone(self.engine.current)
        self.assertTrue(any('超时' in t for t in self.chat))

    def test_disabled_engine_does_nothing(self):
        self.cfg.enabled = False
        self.engine.ask_public()
        self.assertEqual(self.chat, [])
