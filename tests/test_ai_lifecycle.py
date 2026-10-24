"""AI 啟停使用真正控制器方法，不寫入真實設定。"""
from unittest import TestCase
from unittest.mock import Mock, patch

from aafm_py.config import AiPlayerConfig
from aafm_py.controller import Controller
import test_ai_work as work
from test_ai_work import BlockingProvider


class AiLifecycleTests(TestCase):
    # Share fixture helpers without inheriting/re-running #11 test cases.
    make_player = work.AiWorkTests.make_player
    cleanup_player = staticmethod(work.AiWorkTests.cleanup_player)
    drain = work.AiWorkTests.drain

    def controller(self, player):
        c = Controller.__new__(Controller)
        c.ai_player, c.ai_config = player, player.config
        c._persist_config = Mock(return_value=True)
        c._post_ui = Mock()
        return c

    def test_repeated_disable_enable_accepts_fresh_same_text_and_preserves_settings(self):
        player, sent, replied, _ = self.make_player(triggerCooldownSeconds=600)
        c = self.controller(player)
        worker = None
        for _ in range(3):
            c.set_ai_enabled(False)
            self.assertTrue(player._stopped)
            self.assertFalse(player.config.enabled)
            player.on_player_message('Alice', 'same text')
            self.drain(player)
            count = len(sent)
            c.set_ai_enabled(True)
            self.assertTrue(c.ai_config.enabled)
            self.assertFalse(player._stopped)
            player.on_player_message('Alice', 'same text')
            self.drain(player)
            self.assertEqual(len(sent), count + 1)
            self.assertEqual(player.config.triggerCooldownSeconds, 600)
            if worker is None:
                worker = player._work_thread
            self.assertIs(player._work_thread, worker)
        self.assertEqual(replied, ['Alice'] * 3)
        self.assertEqual(c._persist_config.call_count, 6)

    def test_enable_does_not_revive_inflight_queued_or_disabled_messages(self):
        provider = BlockingProvider()
        player, sent, replied, _ = self.make_player(provider)
        c = self.controller(player)
        player.on_player_message('Alice', 'old in flight')
        self.assertTrue(provider.entered.wait(2))
        player.on_player_message('Bob', 'old queued')
        c.set_ai_enabled(False)
        player.on_player_message('Carol', 'while disabled')
        c.set_ai_enabled(True)
        player.on_player_message('Dave', 'new request')
        provider.release.set()
        self.drain(player)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(sent, ['reply'])
        self.assertEqual(replied, ['Dave'])
        self.assertEqual(provider.calls[-1][1][-1]['content'], 'new request')
        player._next_scheduled_reply_time = 0
        player.tick()
        self.drain(player)
        self.assertEqual(len(provider.calls), 2)

    def test_pending_enable_is_cancelled_by_another_disable(self):
        provider = BlockingProvider()
        player, sent, _, _ = self.make_player(provider)
        c = self.controller(player)
        player.on_player_message('Alice', 'old')
        self.assertTrue(provider.entered.wait(2))
        c.set_ai_enabled(False)
        c.set_ai_enabled(True)
        player.on_player_message('Bob', 'also cancelled')
        c.set_ai_enabled(False)
        provider.release.set()
        self.drain(player)
        self.assertEqual(sent, [])
        self.assertTrue(player._stopped)
        c.set_ai_enabled(True)
        player.on_player_message('Carol', 'fresh')
        self.drain(player)
        self.assertEqual(sent, ['reply'])

    def test_redundant_enable_preserves_inflight_request_and_cooldown(self):
        provider = BlockingProvider()
        player, sent, _, _ = self.make_player(provider, triggerCooldownSeconds=600)
        c = self.controller(player)
        player.on_player_message('Alice', 'still valid')
        self.assertTrue(provider.entered.wait(2))
        generation = player._work_generation
        cooldown = player._next_trigger_reply_time
        c.set_ai_enabled(True)
        self.assertEqual(player._work_generation, generation)
        self.assertEqual(player._next_trigger_reply_time, cooldown)
        provider.release.set()
        self.drain(player)
        self.assertEqual(sent, ['reply'])

    def test_reenable_offline_waits_for_reconnect_and_fresh_request(self):
        provider = BlockingProvider()
        player, sent, replied, _ = self.make_player(provider)
        c = self.controller(player)
        player.set_online(True)
        player.on_player_message('Alice', 'old connection')
        self.assertTrue(provider.entered.wait(2))
        player.set_online(False)
        player.stop_now()
        c.set_ai_enabled(False)
        c.set_ai_enabled(True)
        player.on_player_message('Bob', 'offline')
        player.reload(c.ai_config)  # Controller reconnects with the same instance.
        player.set_online(True)
        player.on_player_message('Carol', 'new connection')
        provider.release.set()
        self.drain(player)
        self.assertEqual(sent, ['reply'])
        self.assertEqual(replied, ['Carol'])

    def test_disabled_reload_and_context_clear_then_enable_and_closed_is_terminal(self):
        player, sent, _, _ = self.make_player()
        c = self.controller(player)
        player._context.append({'role': 'user', 'content': 'old'})
        c.set_ai_enabled(False)
        player.clear_context()
        self.drain(player)
        self.assertEqual(list(player._context), [])
        cfg = AiPlayerConfig(enabled=False, autoReplyEnabled=True, triggerCooldownSeconds=0)
        with patch('aafm_py.controller.AiPlayerConfig.load', return_value=cfg):
            c.reload_ai_player()
        self.drain(player)
        player.on_player_message('Alice', 'disabled after reload')
        c.set_ai_enabled(True)
        player.on_player_message('Bob', 'enabled after reload')
        self.drain(player)
        self.assertEqual(sent, ['reply'])
        player.close()
        c.set_ai_enabled(True)
        player.on_player_message('Carol', 'after shutdown')
        self.drain(player)
        self.assertEqual(sent, ['reply'])
        player.close()
        self.assertEqual(player._work_queue.unfinished_tasks, 0)
