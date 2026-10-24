"""命令攔截回歸；不連線、不執行真實遊戲命令。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from aafm_py.config import AppConfig
from aafm_py.controller import Controller


class CommandBlockTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller.__new__(Controller)
        self.controller.config = AppConfig()
        self.controller._engine = SimpleNamespace(say=Mock(return_value=True))
        self.controller._post_ui = Mock()
        self.controller.ai_player = SimpleNamespace(_current_player='ReviewPlayer')

    def test_default_rules_cover_namespaces_case_and_whitespace(self):
        for text in ('/give ReviewBot diamond 64', '/minecraft:give ReviewBot diamond 64',
                     '/essentials:give ReviewBot diamond 64', ' /MiNeCrAfT:GiVe\tReviewBot diamond 64 '):
            with self.subTest(text=text):
                self.assertEqual(self.controller._blocked_command(text), 'give')

    def test_multiword_rules_preserve_path_boundaries(self):
        self.controller.config.commandBlock.commands = [' /LAND  create ']
        for text in ('/land create plot', '/plugin:land\tCREATE plot'):
            self.assertEqual(self.controller._blocked_command(text), 'land create')
        for text in ('/land claim plot', '/land createother', '/land', '/other land create'):
            self.assertEqual(self.controller._blocked_command(text), '')

    def test_explicit_namespace_rules_remain_specific(self):
        self.controller.config.commandBlock.commands = ['minecraft:give']
        self.assertEqual(self.controller._blocked_command('/MINECRAFT:GIVE Steve stone'), 'minecraft:give')
        for text in ('/give Steve stone', '/plugin:give Steve stone'):
            self.assertEqual(self.controller._blocked_command(text), '')

    def test_allowed_chat_commands_and_disabled_filter_pass(self):
        for text in ('hello /give', '/giver Steve', '/minecraft:giveaway', '/minecraft:msg Steve hi', '', '/'):
            self.assertEqual(self.controller._blocked_command(text), '')
        self.controller.config.commandBlock.enabled = False
        self.assertEqual(self.controller._blocked_command('/minecraft:give Steve stone'), '')

    def test_blocked_command_never_reaches_engine_and_feedback_is_clear(self):
        text = '/minecraft:give ReviewBot diamond 64'
        self.controller.config.commandBlock.message = '{player}：已攔截 /{command} ({cmd})'
        self.controller._on_ai_send_chat(text)
        self.controller._engine.say.assert_called_once_with('ReviewPlayer：已攔截 /give (give)')
        self.assertIn('/give', self.controller._post_ui.call_args.args[0]['message'])
        self.controller.config.commandBlock.message = ''
        self.controller._engine.say.reset_mock()
        self.controller._on_ai_send_chat(text)
        self.controller._engine.say.assert_not_called()

    def test_allowed_command_reaches_engine_unchanged(self):
        text = '/minecraft:msg ReviewPlayer hello'
        self.controller._on_ai_send_chat(text)
        self.controller._engine.say.assert_called_once_with(text)


if __name__ == '__main__':
    unittest.main()
