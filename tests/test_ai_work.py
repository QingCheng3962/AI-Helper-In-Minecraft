"""AI 工作排程回歸；不呼叫真實模型、QQ 或遊戲伺服器。"""
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aafm_py.ai_player import AiPlayer, MAX_PENDING_WORK
from aafm_py.config import AiPlayerConfig
from aafm_py.controller import Controller


class BlockingProvider:
    def __init__(self, result='reply'):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.result = result

    def send(self, system, messages):
        self.calls.append((threading.current_thread().name, messages))
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError('test provider was not released')
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def generate(self, *args):
        return self.send('', [])


class AiWorkTests(unittest.TestCase):
    def make_player(self, provider=None, **settings):
        cfg = AiPlayerConfig(enabled=True, autoReplyEnabled=True,
                             triggerCooldownSeconds=0, debugLog=False)
        for key, value in settings.items():
            setattr(cfg, key, value)
        provider = provider or Mock(send=Mock(return_value='reply'))
        factory = patch('aafm_py.ai_player.llm.create_provider', return_value=provider)
        factory.start()
        self.addCleanup(factory.stop)
        image_factory = patch('aafm_py.ai_player.llm.create_image_provider', return_value=provider)
        image_factory.start()
        self.addCleanup(image_factory.stop)
        sent, replied, logs = [], [], []
        player = AiPlayer(cfg, sent.append, log=logs.append, on_reply=replied.append)
        player._chunk_delay_ms = 0
        # LIFO cleanup releases blocked requests before joining the daemon.
        self.addCleanup(self.cleanup_player, player, provider)
        return player, sent, replied, logs

    @staticmethod
    def cleanup_player(player, provider):
        player.close()
        if isinstance(provider, BlockingProvider):
            provider.release.set()
        if player._work_thread:
            player._work_thread.join(3)

    def drain(self, player):
        with player._work_queue.all_tasks_done:
            self.assertTrue(player._work_queue.all_tasks_done.wait_for(
                lambda: player._work_queue.unfinished_tasks == 0, timeout=5))

    def test_blocked_provider_does_not_block_caller_and_preserves_fifo_and_player(self):
        provider = BlockingProvider()
        player, sent, replied, _ = self.make_player(provider)
        returned = threading.Event()
        caller = threading.Thread(target=lambda: (
            player.on_player_message('Alice', 'one'), returned.set()), name='event-worker')
        caller.start()
        self.assertTrue(provider.entered.wait(2))
        self.assertTrue(returned.wait(1), 'the event caller must return before the provider')
        player.on_player_message('Bob', 'two')
        self.assertEqual(sent, [])
        provider.release.set()
        caller.join(2)
        self.drain(player)
        self.assertEqual(replied, ['Alice', 'Bob'])
        self.assertEqual(sent, ['reply', 'reply'])
        self.assertEqual([m[-1]['content'] for _, m in provider.calls], ['one', 'two'])
        self.assertEqual({name for name, _ in provider.calls}, {'aafm-ai'})

    def test_controller_consumes_mc_qq_and_periodic_tasks_while_ai_waits(self):
        provider = BlockingProvider()
        player, _, _, _ = self.make_player(provider)
        c = Controller.__new__(Controller)
        c.ai_player = player
        c._running, c._qq = True, SimpleNamespace(running=False)
        c._bot_events, c._qq_events = queue.Queue(), queue.Queue()
        c._reconnect_at = c._lobby_at = None
        for name in ('_post_ui', '_process_qq_event', '_maybe_proactive_reconnect',
                     '_maybe_auto_lobby', '_maybe_online_check', '_maybe_daily_rotate', '_song_tick'):
            setattr(c, name, Mock())
        for name in ('_check_reconnect_triggers', '_maybe_relay_mc_to_qq',
                     '_maybe_bot_command', '_maybe_song_reply'):
            setattr(c, name, Mock(return_value=False))
        original = c._process_bot_event
        processed = threading.Event()

        def process(event):
            if event['event'] == 'after-ai':
                self.assertTrue(provider.entered.wait(2))
                c._qq_events.put({'event': 'qq-after-ai'})
                c._running = False
                processed.set()
            else:
                original(event)
        c._process_bot_event = process
        c._bot_events.put({'event': 'chat', 'player': 'Alice', 'content': 'one'})
        c._bot_events.put({'event': 'after-ai'})
        with patch('aafm_py.controller.time', SimpleNamespace(time=Mock(side_effect=[0, 2, 4]))):
            worker = threading.Thread(target=c._worker_loop)
            worker.start()
            self.assertTrue(processed.wait(2))
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertFalse(provider.release.is_set())
        c._process_qq_event.assert_called_once_with({'event': 'qq-after-ai'})
        self.assertGreater(c._maybe_daily_rotate.call_count, 0)
        self.assertGreater(c._song_tick.call_count, 0)

    def test_bounded_queue_and_coalesced_ticks(self):
        provider = BlockingProvider()
        player, _, _, logs = self.make_player(provider)
        player.on_player_message('Alice', 'one')
        self.assertTrue(provider.entered.wait(2))
        for _ in range(100):
            player.tick()
        self.assertEqual(player._work_queue.qsize(), 1)
        for n in range(100):
            player.on_player_message('Bob', 'queued' + str(n))
        self.assertEqual(player._work_queue.qsize(), MAX_PENDING_WORK)
        self.assertTrue(any('队列已满' in line for line in logs))
        provider.release.set()
        self.drain(player)
        self.assertLessEqual(len(provider.calls), MAX_PENDING_WORK + 1)

    def test_timeout_does_not_kill_worker_or_next_request(self):
        provider = Mock(send=Mock(side_effect=[TimeoutError('fake timeout'), 'next']))
        player, sent, replied, logs = self.make_player(provider)
        player.on_player_message('Alice', 'one')
        player.on_player_message('Bob', 'two')
        self.drain(player)
        self.assertEqual(sent, ['next'])
        self.assertEqual(replied, ['Bob'])
        self.assertTrue(any('fake timeout' in line for line in logs))

    def test_stop_offline_reload_and_close_discard_queued_and_late_results(self):
        for action in ('stop', 'offline', 'reload', 'close'):
            with self.subTest(action=action):
                provider = BlockingProvider()
                player, sent, replied, _ = self.make_player(provider)
                player.on_player_message('Alice', 'old')
                self.assertTrue(provider.entered.wait(2))
                player.on_player_message('Bob', 'queued-old')
                if action == 'stop':
                    player.stop_now()
                elif action == 'offline':
                    player.set_online(False)
                elif action == 'close':
                    player.close()
                else:
                    cfg = AiPlayerConfig(enabled=True, autoReplyEnabled=True,
                                         triggerCooldownSeconds=0, debugLog=False)
                    player.reload(cfg)
                provider.release.set()
                self.drain(player)
                self.assertEqual(sent, [])
                self.assertEqual(replied, [])
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(list(player._context), [] if action == 'reload' else
                                 [{'role': 'user', 'content': 'old'}])

    def test_reload_uses_new_settings_and_keeps_one_worker(self):
        provider = BlockingProvider()
        player, sent, _, _ = self.make_player(provider)
        player.on_player_message('Alice', 'old')
        self.assertTrue(provider.entered.wait(2))
        worker = player._work_thread
        cfg = AiPlayerConfig(enabled=True, autoReplyEnabled=True, systemPrompt='new-system',
                             triggerCooldownSeconds=0, debugLog=False)
        new_provider = Mock(send=Mock(return_value='new-result'))
        with patch('aafm_py.ai_player.llm.create_provider', return_value=new_provider):
            player.reload(cfg)
            player.on_player_message('Bob', 'new')
            provider.release.set()
            self.drain(player)
        self.assertIs(player._work_thread, worker)
        self.assertEqual(sent, ['new-result'])
        self.assertIn('new-system', new_provider.send.call_args.args[0])
        self.assertEqual(new_provider.send.call_args.args[1], [{'role': 'user', 'content': 'new'}])

    def test_image_is_background_and_canceled_url_is_not_sent(self):
        provider = BlockingProvider('https://invalid.test/fake.png')
        player, sent, _, _ = self.make_player(provider, imageGenerationEnabled=True)
        player.generate_image('fake image')
        self.assertTrue(provider.entered.wait(2))
        player.stop_now()
        provider.release.set()
        self.drain(player)
        self.assertEqual(sent, [])

    def test_scheduled_and_identity_requests_use_same_worker(self):
        player, sent, _, _ = self.make_player(scheduleEnabled=True, triggerEnabled=False)
        player.config.autoReplyEnabled = False
        player.on_player_message('Alice', 'scheduled')
        self.drain(player)
        player._next_scheduled_reply_time = 0
        player.tick()
        self.drain(player)
        self.assertEqual(sent, ['reply'])
        roster = Mock(enabled=True, personality_enabled=False, alias_enabled=False,
                      research_interval_ms=0, research_min_messages=0)
        roster.match_at_end.return_value = {'name': 'Alice', 'main': 'description'}
        player._roster = roster
        player.config.autoReplyEnabled = True
        player.on_player_message('Bob', 'Alice是谁')
        self.drain(player)
        self.assertEqual(sent, ['reply', 'reply'])

    def test_search_and_research_waits_do_not_publish_after_cancel(self):
        for kind in ('search', 'research', 'alias'):
            with self.subTest(kind=kind):
                provider = BlockingProvider('a valid note')
                player, sent, _, _ = self.make_player(provider, searchEnabled=(kind == 'search'),
                                                      searchTriggerRegex='search')
                roster = Mock()
                roster.find.return_value = {'name': 'Alice', 'source': 'fake.csv'}
                player._roster = roster
                player._chat_logs['alice'] = [{'text': 'fake chat'}]
                if kind == 'search':
                    # Search uses the same bounded worker as the subsequent send.
                    search = patch('aafm_py.ai_player.websearch.search',
                                   side_effect=lambda *args: provider.send('', []))
                    search.start()
                    self.addCleanup(search.stop)
                    player._roster = None
                    player.on_player_message('Alice', 'search')
                else:
                    callback = player._start_research if kind == 'research' else player._start_alias
                    player._enqueue(kind, callback, 'Alice', True)
                self.assertTrue(provider.entered.wait(2))
                player.stop_now()
                provider.release.set()
                self.drain(player)
                self.assertEqual(sent, [])
                roster.add_ai_record.assert_not_called()
                roster.add_aliases.assert_not_called()

    def test_stop_between_chunks_cancels_remaining_chunks_and_affinity(self):
        player, sent, replied, _ = self.make_player(maxCharsPerMessage=3)
        player._trigger_provider.send.return_value = 'abcdef'
        def stop_after_chunk(chunk):
            sent.append(chunk)
            player.stop_now()
        player._send_chat_callback = stop_after_chunk
        player.on_player_message('Alice', 'hello')
        self.drain(player)
        self.assertEqual(sent, ['abc'])
        self.assertEqual(replied, [])


if __name__ == '__main__':
    unittest.main()
