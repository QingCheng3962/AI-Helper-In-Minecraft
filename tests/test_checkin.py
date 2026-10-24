"""簽到交易與控制器回歸；僅使用臨時資料和假引擎。"""
import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from aafm_py.config import AppConfig
from aafm_py.bot_engine import SendResult
from aafm_py.controller import Controller
from aafm_py.dailydb import DailyDB


class FaultConnection:
    def __init__(self, real, fail):
        self.real, self.fail = real, fail

    def execute(self, sql, *args):
        if self.fail in sql:
            raise sqlite3.OperationalError('injected SQL failure')
        return self.real.execute(sql, *args)

    def commit(self):
        if self.fail == 'commit':
            raise sqlite3.OperationalError('injected commit failure')
        self.real.commit()

    def executemany(self, sql, *args):
        if self.fail in sql:
            raise sqlite3.OperationalError('injected SQL failure')
        return self.real.executemany(sql, *args)

    def __getattr__(self, name):
        return getattr(self.real, name)

    def __enter__(self):
        return self

    def __exit__(self, typ, value, traceback):
        if typ is not None:
            self.real.rollback()
            return False
        try:
            self.commit()
        except Exception:
            self.real.rollback()
            raise
        return False


def controller(daily, engine=None):
    c = Controller.__new__(Controller)
    c.config = AppConfig()
    c.config.misc.checkinMoneyMin = c.config.misc.checkinMoneyMax = 20
    c.config.misc.checkinItem = ''
    c.daily = daily
    c._engine = engine or SimpleNamespace(say=Mock(return_value=True),
                                         say_result=Mock(return_value=SendResult('submitted')))
    c.send_chat = Mock()
    c._post_ui = Mock()
    c._remember_sent = Mock()
    return c


class CheckinTransactions(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = str(Path(self.folder.name) / 'daily.db')
        self.db = DailyDB(self.path)

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def assert_empty(self):
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM checkin').fetchone()[0], 0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM player_stats').fetchone()[0], 0)
        self.assertFalse(self.db.conn.in_transaction)

    def test_readonly_repeated_requests_never_reward(self):
        self.db.conn.execute('PRAGMA query_only=ON')
        c = controller(self.db)
        c._do_checkin('Alice')
        c._do_checkin('Alice')
        c._engine.say.assert_not_called()
        c._engine.say_result.assert_not_called()
        self.assertIn('未能儲存', c.send_chat.call_args.args[0])
        self.assert_empty()
        self.db.conn.execute('PRAGMA query_only=OFF')
        self.assertTrue(self.db.sign('Alice')['ok'])

    def test_lookup_write_and_commit_failures_rollback(self):
        real = self.db.conn
        for fail in ('SELECT streak, total FROM checkin', 'SELECT last_day',
                     'INSERT INTO checkin', 'INSERT INTO player_stats', 'commit'):
            with self.subTest(fail=fail):
                self.db._conn = FaultConnection(real, fail)
                c = controller(self.db)
                c._do_checkin('Alice')
                c._engine.say.assert_not_called()
                c._engine.say_result.assert_not_called()
                self.db._conn = real
                self.assert_empty()
        self.assertTrue(self.db.sign('Alice')['ok'])
        self.assertEqual(self.db.sign('Alice')['reason'], 'already_signed')

    def test_empty_player(self):
        self.assertEqual(self.db.sign(' ')['reason'], 'invalid_player')
        self.assert_empty()

    def test_concurrent_same_instance(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.db.sign, ['Alice'] * 20))
        self.assertEqual(sum(r['ok'] for r in results), 1)
        self.assertEqual(self.db.stats('Alice')['total'], 1)

    def test_concurrent_two_connections(self):
        other = DailyDB(self.path)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda i: (self.db if i % 2 else other).sign('Alice'), range(20)))
            self.assertEqual(sum(r['ok'] for r in results), 1)
            self.assertEqual(self.db.stats('Alice')['total'], 1)
        finally:
            other.close()


if __name__ == '__main__':
    unittest.main()
