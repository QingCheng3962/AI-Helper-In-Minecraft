import concurrent.futures
import io
import sqlite3
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aafm_py.bot_engine import BotEngine, SendResult
from aafm_py.dailydb import DailyDB, today_str
import test_checkin
from test_checkin import FaultConnection, controller


class RewardCases(unittest.TestCase):
    setUp = test_checkin.CheckinTransactions.setUp
    tearDown = test_checkin.CheckinTransactions.tearDown

    def engine(self, statuses):
        return SimpleNamespace(say_result=Mock(side_effect=[SendResult(s) for s in statuses]))

    def states(self):
        return {r['kind']: r['state'] for r in self.db.rewards('Alice')}

    def test_not_sent_retries_frozen_reward_once(self):
        engine = self.engine(['not_sent', 'submitted'])
        c = controller(self.db, engine)
        with patch('aafm_py.controller.random.randint', return_value=23) as rand:
            c._do_checkin('Alice')
            self.assertIn('待補送 1', c.send_chat.call_args.args[0])
            c.config.misc.checkinMoneyCmd = 'changed {value}'
            c._do_checkin('Alice')
            c._do_checkin('Alice')
            rand.assert_called_once()
        self.assertEqual([x.args[0] for x in engine.say_result.call_args_list],
                         ['/eco give Alice 23', '/eco give Alice 23'])
        self.assertEqual(self.db.stats('Alice')['total'], 1)
        self.assertEqual(self.states(), {'money': 'submitted'})

    def test_partial_success_only_retries_missing_item(self):
        engine = self.engine(['submitted', 'not_sent', 'submitted'])
        c = controller(self.db, engine)
        c.config.misc.checkinItem = 'diamond'
        # 命令的分類與資料庫讀取順序無關，逐項決定發送結果。
        def send(cmd):
            if cmd.startswith('/eco'):
                return SendResult('submitted')
            item_calls.append(cmd)
            return SendResult('not_sent' if len(item_calls) == 1 else 'submitted')
        item_calls = []
        engine.say_result.side_effect = send
        c._do_checkin('Alice')
        self.assertIn('待補送 1', c.send_chat.call_args.args[0])
        c._do_checkin('Alice')
        sent = [x.args[0] for x in engine.say_result.call_args_list]
        self.assertEqual(sent.count('/eco give Alice 20'), 1)
        self.assertEqual(sent.count('/give Alice diamond 1'), 2)
        self.assertEqual(set(self.states().values()), {'submitted'})

    def test_unknown_never_retried(self):
        c = controller(self.db, self.engine(['unknown']))
        c._do_checkin('Alice')
        c._do_checkin('Alice')
        c._engine.say_result.assert_called_once()
        self.assertEqual(self.states(), {'money': 'unknown'})
        self.assertIn('未知結果請管理者核對', c.send_chat.call_args.args[0])

    def test_result_save_failure_recovered_as_unknown(self):
        c = controller(self.db)
        c.config.misc.checkinItem = 'diamond'
        with patch.object(self.db, 'finish_reward', side_effect=sqlite3.OperationalError('failure')):
            c._do_checkin('Alice')
        self.assertEqual(self.states(), {'money': 'sending', 'item': 'pending'})
        c._engine.say_result.assert_called_once()
        self.db.close()
        self.db = DailyDB(self.path)
        retry = controller(self.db)
        retry._do_checkin('Alice')
        retry._engine.say_result.assert_called_once_with('/give Alice diamond 1')
        self.assertEqual(self.states(), {'money': 'unknown', 'item': 'submitted'})

    def test_claim_commit_failure_does_not_send(self):
        c = controller(self.db)
        original = self.db.claim_reward
        def claim(reward):
            real = self.db.conn
            self.db._conn = FaultConnection(real, 'commit')
            try:
                return original(reward)
            finally:
                self.db._conn = real
        with patch.object(self.db, 'claim_reward', side_effect=claim):
            c._do_checkin('Alice')
        c._engine.say_result.assert_not_called()
        self.assertEqual(self.states(), {'money': 'pending'})

    def test_claim_failure_does_not_send(self):
        c = controller(self.db)
        with patch.object(self.db, 'claim_reward', side_effect=sqlite3.OperationalError('failure')):
            c._do_checkin('Alice')
        c._engine.say_result.assert_not_called()
        self.assertEqual(self.states(), {'money': 'pending'})

    def test_snapshot_failure_rolls_back_checkin_and_statistics(self):
        self.db._conn = FaultConnection(self.db.conn, 'INSERT INTO checkin_rewards')
        c = controller(self.db)
        c._do_checkin('Alice')
        c._engine.say_result.assert_not_called()
        self.assertEqual(self.db.signed_count(), 0)
        self.assertEqual(self.db.stats('Alice')['total'], 0)

    def test_pending_survives_restart(self):
        controller(self.db, self.engine(['not_sent']))._do_checkin('Alice')
        self.db.close()
        self.db = DailyDB(self.path)
        c = controller(self.db)
        c._do_checkin('Alice')
        c._engine.say_result.assert_called_once_with('/eco give Alice 20')
        self.assertEqual(self.db.stats('Alice')['total'], 1)

    def test_cross_day_carries_pending_and_retries_old_snapshot(self):
        controller(self.db, self.engine(['not_sent']))._do_checkin('Alice')
        tomorrow = (date.fromisoformat(today_str()) + timedelta(days=1)).isoformat()
        with patch('aafm_py.dailydb.today_str', return_value=tomorrow):
            c = controller(self.db)
            c.config.misc.checkinMoneyMin = c.config.misc.checkinMoneyMax = 30
            c._do_checkin('Alice')
            self.assertEqual([x.args[0] for x in c._engine.say_result.call_args_list],
                             ['/eco give Alice 20', '/eco give Alice 30'])
            self.assertEqual(self.db.stats('Alice')['total'], 2)
            self.assertEqual(self.db.stats('Alice')['streak'], 2)
        self.assertEqual(len(list(Path(self.folder.name).glob('db_backup/*.db'))), 1)

    def test_cross_day_read_or_restore_or_backup_failure_preserves_pending(self):
        controller(self.db, self.engine(['not_sent']))._do_checkin('Alice')
        tomorrow = (date.fromisoformat(today_str()) + timedelta(days=1)).isoformat()
        real = self.db.conn
        self.db._conn = FaultConnection(real, 'FROM checkin_rewards')
        with self.assertRaises(sqlite3.Error):
            self.db.ensure_day(tomorrow)
        self.db._conn = real
        connect = sqlite3.connect
        with patch('aafm_py.dailydb.sqlite3.connect',
                   side_effect=lambda *a, **k: FaultConnection(connect(*a, **k), 'INSERT INTO checkin_rewards')):
            with self.assertRaises(sqlite3.Error):
                self.db.ensure_day(tomorrow)
        with patch('aafm_py.dailydb.shutil.copy2', side_effect=OSError('backup failed')):
            with self.assertRaises(OSError):
                self.db.ensure_day(tomorrow)
        self.assertEqual(self.db._get_meta('current_day'), today_str())
        self.assertEqual(self.states(), {'money': 'pending'})
        self.assertEqual(self.db.signed_count(), 1)
        self.assertFalse(list(Path(self.folder.name).glob('*.rotate-*')))

    def test_concurrent_retries_claim_each_reward_once(self):
        controller(self.db, self.engine(['not_sent']))._do_checkin('Alice')
        other = DailyDB(self.path)
        engine = SimpleNamespace(say_result=Mock(return_value=SendResult('submitted')))
        try:
            def retry(i):
                controller(self.db if i % 2 else other, engine)._do_checkin('Alice')
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(retry, range(20)))
            engine.say_result.assert_called_once()
            self.assertEqual(self.db.stats('Alice')['total'], 1)
        finally:
            other.close()

    def test_replacement_failure_keeps_original_then_can_retry(self):
        controller(self.db, self.engine(['not_sent']))._do_checkin('Alice')
        tomorrow = (date.fromisoformat(today_str()) + timedelta(days=1)).isoformat()
        with patch('aafm_py.dailydb.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                self.db.ensure_day(tomorrow)
        self.assertEqual(self.db._get_meta('current_day'), today_str())
        self.assertEqual(self.states(), {'money': 'pending'})
        self.assertTrue(self.db.ensure_day(tomorrow)['rotated'])
        self.assertEqual(self.db.rewards('Alice', tomorrow)[0]['state'], 'pending')

    def test_successful_rotation_archives_daily_rows_and_keeps_statistics(self):
        self.db.get_lucky('Alice')
        self.db.sign('Alice')
        tomorrow = (date.fromisoformat(today_str()) + timedelta(days=1)).isoformat()
        info = self.db.ensure_day(tomorrow)
        self.assertEqual(self.db.signed_count(tomorrow), 0)
        self.assertEqual(self.db.lucky_count(tomorrow), 0)
        self.assertEqual(self.db.stats('Alice')['total'], 1)
        old = sqlite3.connect(info['backup'])
        try:
            self.assertEqual(old.execute('SELECT COUNT(*) FROM lucky').fetchone()[0], 1)
            self.assertEqual(old.execute('SELECT COUNT(*) FROM checkin').fetchone()[0], 1)
        finally:
            old.close()

    def test_legacy_checkin_migrates_without_new_rewards(self):
        self.db.sign('Alice')
        self.db.conn.execute('DROP TABLE checkin_rewards')
        self.db.conn.commit()
        self.db.close()
        self.db = DailyDB(self.path)
        c = controller(self.db)
        c._do_checkin('Alice')
        c._engine.say_result.assert_not_called()
        self.assertEqual(self.db.stats('Alice')['total'], 1)

    def test_unknown_carried_but_submitted_archived_across_day(self):
        controller(self.db, self.engine(['unknown']))._do_checkin('Alice')
        controller(self.db)._do_checkin('Bob')
        tomorrow = (date.fromisoformat(today_str()) + timedelta(days=1)).isoformat()
        self.db.ensure_day(tomorrow)
        self.assertEqual(self.db.rewards('Alice', tomorrow)[0]['state'], 'unknown')
        self.assertEqual(self.db.rewards('Bob', tomorrow), [])
        backup = next(Path(self.folder.name).glob('db_backup/*.db'))
        old = sqlite3.connect(backup)
        try:
            self.assertEqual(old.execute("SELECT state FROM checkin_rewards WHERE player='Bob'").fetchone()[0], 'submitted')
        finally:
            old.close()

    def test_zero_rewards(self):
        c = controller(self.db)
        c.config.misc.checkinMoneyMin = c.config.misc.checkinMoneyMax = 0
        c._do_checkin('Alice')
        c._engine.say_result.assert_not_called()
        self.assertIn('未設定獎勵', c.send_chat.call_args.args[0])

    def test_invalid_commands_do_not_consume_checkin(self):
        c = controller(self.db)
        for value in ('/', 'eco {missing}', 'eco\ngive Alice 20', 'a' * 251, '😀' * 126):
            with self.subTest(value=value):
                c.config.misc.checkinMoneyCmd = value
                c._do_checkin('Alice')
                self.assertEqual(self.db.signed_count(), 0)
                self.assertEqual(self.db.stats('Alice')['total'], 0)
        c._engine.say_result.assert_not_called()

    def test_custom_success_text_only_used_for_submitted_rewards(self):
        c = controller(self.db, self.engine(['not_sent', 'submitted']))
        c.config.misc.checkinText = 'CUSTOM {player} {money}'
        c._do_checkin('Alice')
        self.assertNotIn('CUSTOM', c.send_chat.call_args.args[0])
        c._do_checkin('Bob')
        self.assertIn('CUSTOM Bob 20', c.send_chat.call_args.args[0])
        self.assertIn('到帳尚未確認', c.send_chat.call_args.args[0])

    def test_old_builtin_message_migrates_but_custom_text_is_kept(self):
        from aafm_py.config import MiscConfig
        cfg = MiscConfig()
        cfg.checkinText = '【签到】{player} 签到成功！获得 {money} 金币{items}（连续 {streak} 天，累计 {total} 次）'
        cfg.validate()
        self.assertIn('{reward_status}', cfg.checkinText)
        cfg.checkinText = 'custom {player}'
        cfg.validate()
        self.assertEqual(cfg.checkinText, 'custom {player}')


class TransportResults(unittest.TestCase):
    def engine(self, pipe):
        engine = BotEngine()
        engine.proc = SimpleNamespace(poll=lambda: None, stdin=pipe)
        engine._running = True
        return engine

    def test_no_process_is_definitely_not_sent(self):
        self.assertEqual(BotEngine().say_result('/eco give Alice 20').status, 'not_sent')

    def test_full_write_is_submitted_and_bool_interface_compatible(self):
        pipe = io.StringIO()
        engine = self.engine(pipe)
        self.assertEqual(engine.say_result('one').status, 'submitted')
        self.assertTrue(engine.say('two'))
        self.assertEqual(len(pipe.getvalue().splitlines()), 2)

    def test_write_short_write_and_flush_failures_are_unknown(self):
        for step in ('write', 'short', 'flush'):
            with self.subTest(step=step):
                pipe = Mock()
                pipe.write.side_effect = OSError('partial') if step == 'write' else lambda s: 1 if step == 'short' else len(s)
                if step == 'flush':
                    pipe.flush.side_effect = OSError('flush failed')
                self.assertEqual(self.engine(pipe).say_result('one').status, 'unknown')


if __name__ == '__main__':
    unittest.main()
