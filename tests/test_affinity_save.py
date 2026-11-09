# -*- coding: utf-8 -*-
"""好感度落盘失败时不能回报名义新分数（#45）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from aafm_py.affinity import AffinityStore


class AffinitySaveTest(unittest.TestCase):
    def test_failed_save_rolls_back_memory_and_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'affinity.json')
            store = AffinityStore(path)
            self.assertEqual(store.add('Alice', 1, daily_bonus=0), 1)

            with patch('aafm_py.affinity.os.replace',
                       side_effect=PermissionError('fixture')):
                self.assertIsNone(store.add('Alice', 2, daily_bonus=0))
                self.assertEqual(store.score('Alice'), 1)
                self.assertTrue(store.last_error)

            reloaded = AffinityStore(path)
            self.assertEqual(reloaded.score('Alice'), 1)

            # 故障解除后可以重试，且不会重复加之前失败的那次。
            self.assertEqual(store.add('Alice', 2, daily_bonus=0), 3)
            self.assertFalse(store.last_error)
            self.assertEqual(AffinityStore(path).score('Alice'), 3)


if __name__ == '__main__':
    unittest.main()
