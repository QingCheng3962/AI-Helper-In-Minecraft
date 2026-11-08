"""Persistence regressions using synthetic files only."""
import csv
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from aafm_py import roster
from aafm_py.dailydb import DailyDB
from aafm_py.log_saver import LogSaver


class PersistenceBoundaries(unittest.TestCase):
    def test_two_column_csv_first_record_and_rollover(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'players.csv'
            path.write_text('玩家名,玩家描述\nAlice,Builder\nBob,Keep\n', encoding='utf-8-sig')
            for i in range(7):
                roster.write_ai_record(str(path), 'Alice', f'record-{i}')
                alice = roster.parse_file(str(path))[0]
                self.assertEqual(alice['main'], 'Builder')
                self.assertEqual(alice['ai'][0], f'record-{i}')
            self.assertEqual(alice['ai'], [f'record-{i}' for i in range(6, 1, -1)])
            self.assertEqual(roster.parse_file(str(path))[1]['main'], 'Keep')
            with path.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(len(rows[0]), len(rows[1]))

    def test_concurrent_flush_reserves_distinct_names(self):
        with tempfile.TemporaryDirectory() as folder:
            saver = LogSaver(folder)
            first = threading.Event()
            barrier = threading.Barrier(2)
            original = saver._next_index
            errors = []
            def next_index(prefix):
                index = original(prefix)
                first.set()
                barrier.wait(5)
                return index
            saver._next_index = next_index
            def flush():
                try:
                    saver.flush()
                except BaseException as error:
                    errors.append(error)
            saver.add('batch-A')
            a = threading.Thread(target=flush)
            a.start()
            self.assertTrue(first.wait(5))
            saver.add('batch-B')
            b = threading.Thread(target=flush)
            b.start()
            a.join(5)
            b.join(5)
            self.assertFalse(a.is_alive() or b.is_alive())
            self.assertEqual(errors, [])
            contents = [p.read_text(encoding='utf-8') for p in (Path(folder) / 'logs').glob('*.txt')]
            self.assertEqual(len(contents), 2)
            self.assertEqual(sum('batch-A' in s for s in contents), 1)
            self.assertEqual(sum('batch-B' in s for s in contents), 1)

    def test_log_failure_keeps_buffer_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            saver = LogSaver(folder)
            saver.add('keep-me')
            with patch('builtins.open', side_effect=PermissionError('fixture')):
                self.assertIsNone(saver.flush())
            path = saver.flush()
            self.assertIn('keep-me', Path(path).read_text(encoding='utf-8'))

    def test_startup_rotation_failure_preserves_rewards_and_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'daily.db')
            with patch('aafm_py.dailydb.today_str', return_value='2026-11-07'):
                db = DailyDB(path)
                db.sign('Alice')
                with db.conn:
                    for player, state in [('Alice', 'sending'), ('Bob', 'pending')]:
                        db.conn.execute('INSERT INTO checkin_rewards(day,player,kind,snapshot,state) VALUES(?,?,?,?,?)',
                                        ('2026-11-07', player, 'money', '{}', state))
                db.close()
            db = DailyDB.__new__(DailyDB)
            try:
                with patch('aafm_py.dailydb.today_str', return_value='2026-11-08'):
                    with patch('aafm_py.dailydb.shutil.copy2', side_effect=OSError('fixture backup failure')):
                        db.__init__(path)
                        self.assertEqual(db._get_meta('current_day'), '2026-11-07')
                        self.assertEqual(dict(db.conn.execute('SELECT player,state FROM checkin_rewards')),
                                         {'Alice': 'unknown', 'Bob': 'pending'})
                    db.ensure_day()
                    self.assertEqual(db._get_meta('current_day'), '2026-11-08')
                    self.assertEqual(dict(db.conn.execute('SELECT player,state FROM checkin_rewards')),
                                     {'Alice': 'unknown', 'Bob': 'pending'})
                    self.assertEqual(db.conn.execute('SELECT total FROM player_stats WHERE player=?', ('Alice',)).fetchone()[0], 1)
                    self.assertTrue(list((Path(folder) / 'db_backup').glob('*.db')))
            finally:
                db.close()
