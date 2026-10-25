# -*- coding: utf-8 -*-
"""网站出站方式（qq.webMode）配置校验。"""
from __future__ import annotations

import unittest

from aafm_py.config import QqConfig


class WebModeTest(unittest.TestCase):
    def test_default_is_local(self):
        self.assertEqual(QqConfig().webMode, 'local')

    def test_relay_is_kept(self):
        cfg = QqConfig()
        cfg.webMode = 'relay'
        cfg.validate()
        self.assertEqual(cfg.webMode, 'relay')

    def test_unknown_falls_back_to_local(self):
        cfg = QqConfig()
        cfg.webMode = 'something-else'
        cfg.validate()
        self.assertEqual(cfg.webMode, 'local')

    def test_preserve_name_flag(self):
        cfg = QqConfig()
        cfg.imagePreserveName = True
        cfg.validate()
        self.assertTrue(cfg.imagePreserveName)


if __name__ == '__main__':
    unittest.main()
