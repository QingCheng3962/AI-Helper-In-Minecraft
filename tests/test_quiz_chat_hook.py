# -*- coding: utf-8 -*-
"""Controller 抢答拦截（防止 answer 被当成普通聊天丢给 AI）。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from aafm_py.config import QaConfig
from aafm_py.controller import Controller


class QuizChatHookTest(unittest.TestCase):
    def _ctrl(self, enabled=True):
        c = object.__new__(Controller)
        qa = QaConfig()
        qa.enabled = enabled
        qa.validate()
        c.config = SimpleNamespace(qa=qa)
        c.ai_config = SimpleNamespace(triggerRegex='[Bot]')
        c._is_self_name = Mock(return_value=False)
        c._quiz_private = Mock()
        c._post_ui = Mock()
        c.quiz = Mock()
        c.quiz.current = {'type': 'choice', 'q': '最大的行星', 'a': 'B',
                          'options': ['A. 地球', 'B. 木星']}
        q = c.quiz.current
        c.quiz._matches = lambda question, text: str(text).strip().upper() == 'B'
        return c

    def test_choice_answer_is_intercepted(self):
        c = self._ctrl()
        self.assertTrue(c._maybe_quiz_chat('Alice', 'B'))
        c.quiz.handle_answer.assert_called_once_with('Alice', 'B')

    def test_wrong_answer_does_not_go_to_ai_without_trigger(self):
        c = self._ctrl()
        self.assertFalse(c._maybe_quiz_chat('Alice', 'A'))
        c.quiz.handle_answer.assert_not_called()

    def test_trigger_prefixed_answer_is_checked(self):
        c = self._ctrl()
        self.assertTrue(c._maybe_quiz_chat('Alice', '[Bot]B'))
        c.quiz.handle_answer.assert_called_once_with('Alice', 'B')

    def test_ask_and_record_commands(self):
        c = self._ctrl()
        self.assertTrue(c._maybe_quiz_chat('Alice', '[Bot]出题'))
        c.quiz.ask_manual.assert_called_once_with('Alice')
        self.assertTrue(c._maybe_quiz_chat('Alice', '[Bot]答题记录'))
        c._quiz_private.assert_called_once()

    def test_disabled_quiz_does_not_intercept(self):
        c = self._ctrl(enabled=False)
        self.assertFalse(c._maybe_quiz_chat('Alice', 'B'))
