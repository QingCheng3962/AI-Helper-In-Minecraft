# -*- coding: utf-8 -*-
"""配置容错：类型错误不能阻断启动，损坏文件要先备份（#43/#44）。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from aafm_py.config import AiPlayerConfig, AppConfig


class ConfigRecoveryTest(unittest.TestCase):
    def test_bad_types_keep_defaults_and_valid_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'config.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({
                    'server': {'host': 'example.test', 'port': '25566'},
                    'llm': {'provider': 123},
                    'misc': {'mcChatPaperWidth': 'oops',
                             'mcChatPrintEnabled': 'false'},
                }, f)
            cfg = AppConfig.load(path)
        self.assertEqual(cfg.server.host, 'example.test')
        self.assertEqual(cfg.server.port, 25566)
        self.assertEqual(cfg.llm.provider, 'openai')
        self.assertEqual(cfg.misc.mcChatPaperWidth, 58)
        self.assertFalse(cfg.misc.mcChatPrintEnabled)

    def test_ai_player_bad_types_do_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'mcai_chat.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'triggerRegex': 123, 'maxTokens': 'oops',
                           'enabled': 'maybe'}, f)
            cfg = AiPlayerConfig.load(path)
        self.assertIsInstance(cfg.triggerRegex, str)
        self.assertIsInstance(cfg.maxTokens, int)

    def test_corrupt_json_is_backed_up_before_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'config.json')
            corrupt = '{"server": {"host": "x", '  # 故意截断
            with open(path, 'w', encoding='utf-8') as f:
                f.write(corrupt)
            cfg = AppConfig.load(path)
            backups = [n for n in os.listdir(folder) if '.corrupt-' in n]
            self.assertEqual(len(backups), 1)
            with open(os.path.join(folder, backups[0]), 'r',
                      encoding='utf-8') as f:
                self.assertEqual(f.read(), corrupt)
            # 保存新配置不能把损坏原件覆盖掉（备份仍在）。
            self.assertTrue(cfg.save(path))
            self.assertTrue(os.path.exists(os.path.join(folder, backups[0])))


if __name__ == '__main__':
    unittest.main()
