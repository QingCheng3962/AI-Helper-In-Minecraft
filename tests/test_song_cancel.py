"""點歌取消回歸：以可控解析器驗證停止與結果發布的時序。"""
import threading
import unittest
from unittest.mock import Mock, patch

from aafm_py.config import AppConfig
from aafm_py.controller import Controller


class SongCancelTests(unittest.TestCase):
    def make_controller(self):
        c = Controller.__new__(Controller)
        c.config = AppConfig()
        c.config.affinity.enabled = False
        c._song_lock = threading.RLock()
        c._song_epoch, c._song_player_epochs = 0, {}
        c._song_seq, c._song_queue = 0, []
        c._song_current, c._song_next_at = None, 0
        c._song_recent, c._song_posted_at, c._song_reply_at = {}, {}, {}
        c._post_ui, c._remember_sent = Mock(), Mock()
        c._send_song_line = Mock(return_value=True)
        c._is_own_echo, c._is_self_name = Mock(return_value=False), Mock(return_value=False)
        c.affinity = Mock()
        return c

    @staticmethod
    def info(title='Song'):
        return {'title': title, 'artists': ['Artist'], 'url': 'https://invalid.test/song.mp3',
                'id': '123456', 'duration': 10000}

    def start_resolve(self, c, who='Alice', query='old', result=None):
        entered, release = threading.Event(), threading.Event()
        def resolve(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test did not release resolver')
            return result or self.info(query)
        resolver = patch('aafm_py.netease.resolve', side_effect=resolve)
        resolver.start()
        self.addCleanup(resolver.stop)
        ticket = c._song_ticket(who)
        thread = threading.Thread(target=c._resolve_into_queue, args=(query, who, 'player', ticket))
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        return release, thread

    def test_stop_during_resolve_does_not_enqueue_play_or_award_affinity(self):
        c = self.make_controller()
        c.config.affinity.enabled = True
        release, thread = self.start_resolve(c)
        c.clear_song_queue('Alice')
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(c.song_state(), {'current': None, 'queue': []})
        c._song_tick()
        c._send_song_line.assert_not_called()
        c.affinity.add.assert_not_called()

    def test_player_stop_is_case_insensitive_and_other_player_still_resolves(self):
        c = self.make_controller()
        release, thread = self.start_resolve(c, who=' ALICE ')
        c.clear_song_queue('alice')
        release.set()
        thread.join(2)
        with patch('aafm_py.netease.resolve', return_value=self.info('Bob song')):
            c._resolve_into_queue('new', 'Bob', 'player')
        self.assertEqual([e['who'] for e in c._song_queue], ['Bob'])
        c._song_tick()
        self.assertEqual(c._song_current['who'], 'Bob')

    def test_global_stop_invalidates_every_outstanding_ticket_and_new_request_works(self):
        c = self.make_controller()
        tickets = {who: c._song_ticket(who) for who in ('Alice', 'Bob')}
        c.clear_song_queue()
        with patch('aafm_py.netease.resolve', return_value=self.info()) as resolve:
            for who, ticket in tickets.items():
                c._resolve_into_queue('old', who, 'player', ticket)
            resolve.assert_not_called()  # already-cancelled work does not start HTTP
            c._resolve_into_queue('fresh', 'Alice', 'player')
        self.assertEqual(len(c._song_queue), 1)
        c._song_tick()
        self.assertEqual(c._song_current['query'], 'fresh')

    def test_stopped_player_can_request_again_while_old_resolve_is_waiting(self):
        c = self.make_controller()
        release, thread = self.start_resolve(c)
        c.clear_song_queue('Alice')
        with patch('aafm_py.netease.resolve', return_value=self.info('Fresh')):
            c._resolve_into_queue('fresh', 'Alice', 'player')
        release.set()
        thread.join(2)
        self.assertEqual([e['title'] for e in c._song_queue], ['Fresh'])

    def test_queue_tick_and_delayed_publish_cannot_restore_cancelled_entry(self):
        c = self.make_controller()
        with patch('aafm_py.netease.resolve', return_value=self.info()):
            c._resolve_into_queue('one', 'Alice', 'player')
            c._resolve_into_queue('two', 'Bob', 'player')
        old = dict(c._song_queue[0])
        c.clear_song_queue('Alice')
        c._post_song_entry(old)
        c._send_song_line.assert_not_called()
        c._song_tick()
        self.assertEqual(c._song_current['who'], 'Bob')
        c.clear_song_queue('Alice')
        self.assertEqual(c._song_current['who'], 'Bob')

    def test_cached_replay_and_existing_entry_receive_new_generation_after_stop(self):
        c = self.make_controller()
        c.config.song.postPlayLink = True
        c.config.song.replyEnabled = False
        with patch('aafm_py.netease.resolve', return_value=self.info('Cached')):
            c._resolve_into_queue('Cached', 'Alice', 'player')
        entry = dict(c._song_queue[0])
        c._song_tick()  # populate the normal replay cache
        c.clear_song_queue('Alice')
        self.assertTrue(c._maybe_song_reply('Alice', '[点歌]Cached'))
        self.assertEqual(c._song_queue[0]['_generation'], c._song_ticket('Alice'))
        c._song_tick()
        self.assertEqual(c._song_current['title'], 'Cached')
        c.clear_song_queue()
        c.enqueue_existing(entry, 'Bob')
        self.assertEqual(c._song_queue[0]['_generation'], c._song_ticket('Bob'))
        c._song_tick()
        self.assertEqual(c._song_current['who'], 'Bob')

    def test_enqueue_captures_generation_before_background_thread_starts(self):
        c = self.make_controller()
        with patch('aafm_py.controller.threading.Thread') as factory:
            c.enqueue_song('old', 'Alice', 'player')
        args = factory.call_args.kwargs['args']
        c.clear_song_queue('Alice')
        with patch('aafm_py.netease.resolve') as resolve:
            c._resolve_into_queue(*args)
        resolve.assert_not_called()
        self.assertEqual(c._song_queue, [])


if __name__ == '__main__':
    unittest.main()
