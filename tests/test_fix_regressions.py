# -*- coding: utf-8 -*-
"""回归测试：2026-10-25 修复的审计 Issues。

覆盖 #19（emoji 长度）、#20（备份失败不推进日期）、#21（改上限不改当天幸运值）、
#22（CSV 表头）、#23（推荐换批优先新候选）、#31（分页 emoji 预算）。
"""
from __future__ import annotations

import csv
import os
import tempfile
import unittest

from aafm_py import bilibili, dailydb, docbook
from aafm_py.controller import _mc_clip, _mc_len
from aafm_py.roster import _write_csv, _write_csv_aliases


class McLengthTest(unittest.TestCase):
    def test_emoji_counts_as_two_utf16_units(self):
        # Java 的 String.length() 里 emoji 占 2；Python len 只算 1（#19）
        self.assertEqual(_mc_len('abc'), 3)
        self.assertEqual(_mc_len('😀'), 2)
        self.assertEqual(len('😀'), 1)

    def test_clip_never_splits_surrogate_pair(self):
        clipped = _mc_clip('😀😀😀', 5)
        self.assertEqual(_mc_len(clipped), 4)   # 2 个 emoji，不拆半个
        self.assertEqual(_mc_len(_mc_clip('abc', 2)), 2)


class DailyDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='aafm-test-')
        self.path = os.path.join(self.tmp, 'daily.db')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_changing_max_keeps_same_day_value(self):
        # #21：当天的幸运值定下来后，改上限不应改变它
        db = dailydb.DailyDB(self.path, backup_dir=os.path.join(self.tmp, 'bak'))
        first = db.get_lucky('Steve', 100, day='2026-10-25')
        again = db.get_lucky('Steve', 5, day='2026-10-25')
        self.assertEqual(first, again)
        db.close()

    def test_backup_failure_does_not_advance_day(self):
        # #20：备份失败时必须保持旧日期（旧库不能被删掉）
        db = dailydb.DailyDB(self.path, backup_dir=os.path.join(self.tmp, 'bak'))
        db._set_meta('current_day', '2020-01-01')
        blocker = os.path.join(self.tmp, 'not-a-dir')
        with open(blocker, 'w', encoding='utf-8') as f:
            f.write('x')
        db.backup_dir = blocker          # os.makedirs 会失败
        info = db.ensure_day('2026-10-25')
        self.assertEqual(info['backup'], '')
        self.assertEqual(db._get_meta('current_day'), '2020-01-01')
        db.close()


class RosterCsvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='aafm-roster-')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows(self, path):
        with open(path, encoding='utf-8-sig', newline='') as f:
            return list(csv.reader(f))

    def test_main_column_gets_header(self):
        # #22：没有权威描述列时新增列必须带表头，重载后仍能识别
        path = os.path.join(self.tmp, 'r.csv')
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('玩家名,描述1,别名1\r\nAlice,旧描述,Ally\r\n')
        _write_csv(path, 'Alice', main='新描述')
        rows = self._rows(path)
        self.assertEqual(rows[0], ['玩家名', '玩家描述', '描述1', '别名1'])
        self.assertEqual(rows[1][1], '新描述')

    def test_alias_columns_get_header(self):
        path = os.path.join(self.tmp, 'r2.csv')
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write('玩家名,玩家描述\r\nAlice,旧描述\r\n')
        _write_csv_aliases(path, 'Alice', ['小明'])
        rows = self._rows(path)
        self.assertEqual(rows[0][2:5], ['别名1', '别名2', '别名3'])
        self.assertEqual(rows[1][2], '小明')


class BilibiliPickTest(unittest.TestCase):
    def test_fresh_candidates_are_kept(self):
        # #23：新候选不足整批时，也要先放新候选，再用旧候选补
        pool = [{'id': str(i), 'title': 't', 'owner': '', 'link': 'l'}
                for i in range(6)]
        orig_pool, orig_shuffle = bilibili._pool, bilibili.random.shuffle
        bilibili._pool = lambda *a, **k: (pool, '')
        bilibili.random.shuffle = lambda x: None
        try:
            items, err = bilibili.recommend_ex(
                'hot', limit=5, exclude={'0', '1', '2', '3', '4'})
        finally:
            bilibili._pool, bilibili.random.shuffle = orig_pool, orig_shuffle
        ids = [x['id'] for x in items]
        self.assertEqual(err, '')
        self.assertIn('5', ids)
        self.assertEqual(len(ids), len(set(ids)))


class DocbookEmojiTest(unittest.TestCase):
    def test_small_budget_is_measured_in_utf16(self):
        # #31：自訂 max_chars 时，emoji 页面不能按 Python 码点算
        pages = docbook.paginate('😀' * 10, max_chars=4, max_lines=14,
                                 max_width=10000)
        widths = [len(p.encode('utf-16-le')) // 2 for p in pages]
        self.assertTrue(all(w <= 4 for w in widths), widths)
        self.assertEqual(''.join(pages), '😀' * 10)


if __name__ == '__main__':
    unittest.main()
