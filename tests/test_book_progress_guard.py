# -*- coding: utf-8 -*-
"""书与笔续写：文档/分页变化时不能默默沿用旧页码。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aafm_py.controller import Controller


class BookProgressGuardTest(unittest.TestCase):
    def _controller(self, logs, writes):
        ctrl = object.__new__(Controller)
        ctrl.config = SimpleNamespace(docBook=SimpleNamespace(
            pageLines=14, pagesPerBook=100, signBook=False,
            titleTemplate='', startText='', endText='',
            endDrop=False, autoGetBook=True))
        ctrl._engine = SimpleNamespace(
            running=True,
            write_book=lambda *a, **k: writes.append(k) or True)
        ctrl._post_ui = logs.append
        return ctrl

    def _settings(self):
        return {
            'pageLines': 14, 'pageChars': 1024, 'pagesPerBook': 100,
            'signBook': False, 'titleTemplate': '', 'startText': '',
            'endText': '', 'endDrop': False, 'autoGetBook': True,
        }

    def test_continue_rejects_changed_source(self):
        logs, writes = [], []
        with tempfile.TemporaryDirectory() as folder:
            src = os.path.join(folder, 'book.txt')
            with open(src, 'w', encoding='utf-8') as f:
                f.write('\n'.join(f'line-{i}' for i in range(30)))
            progress = os.path.join(folder, 'book_progress.json')
            ctrl = self._controller(logs, writes)
            settings = self._settings()
            sig = ctrl._book_source_signature(src, settings)
            with open(progress, 'w', encoding='utf-8') as f:
                json.dump({'file': src, 'settings': settings, 'nextPage': 1,
                           'nextBook': 1, 'totalPages': 3,
                           'sourceSignature': sig}, f)
            with patch('aafm_py.controller.BOOK_PROGRESS_FILE', progress):
                with open(src, 'a', encoding='utf-8') as f:
                    f.write('\nchanged')
                self.assertFalse(ctrl.continue_book())
        self.assertEqual(writes, [])
        self.assertTrue(any('已变化' in str(e.get('message', '')) for e in logs))

    def test_continue_allows_unchanged_source(self):
        logs, writes = [], []
        with tempfile.TemporaryDirectory() as folder:
            src = os.path.join(folder, 'book.txt')
            with open(src, 'w', encoding='utf-8') as f:
                f.write('\n'.join(f'line-{i}' for i in range(30)))
            progress = os.path.join(folder, 'book_progress.json')
            ctrl = self._controller(logs, writes)
            settings = self._settings()
            sig = ctrl._book_source_signature(src, settings)
            with open(progress, 'w', encoding='utf-8') as f:
                json.dump({'file': src, 'settings': settings, 'nextPage': 1,
                           'nextBook': 1, 'totalPages': 3,
                           'sourceSignature': sig}, f)
            with patch('aafm_py.controller.BOOK_PROGRESS_FILE', progress):
                self.assertTrue(ctrl.continue_book())
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]['start_page'], 1)


if __name__ == '__main__':
    unittest.main()
