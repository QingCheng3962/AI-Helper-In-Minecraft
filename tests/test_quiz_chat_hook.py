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

    def test_wrong_choice_answer_is_treated_as_attempt(self):
        c = self._ctrl()
        self.assertTrue(c._maybe_quiz_chat('Alice', 'A'))
        c.quiz.handle_answer.assert_called_once_with('Alice', 'A')

    def test_normal_chat_is_not_treated_as_answer(self):
        c = self._ctrl()
        c.quiz.current = {'type': 'text', 'q': '首都是', 'a': ['北京']}
        c.quiz._matches = lambda q, text: False
        self.assertFalse(c._maybe_quiz_chat('Alice', '今天服务器好卡啊'))

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
