# -*- coding: utf-8 -*-
"""MC 聊天小票机回归测试（不真的打印）。"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aafm_py import receipt_printer as rp


class ReceiptPrinterTest(unittest.TestCase):
    def test_render_escpos_and_gbk(self):
        data = rp.render([
            rp.Command('INIT'),
            rp.Command('ALIGN', 'center'),
            rp.Command('TEXT', '你好 Minecraft'),
            rp.Command('HR'),
            rp.Command('CUT'),
        ], 58)
        self.assertTrue(data.startswith(b'\x1b@'))
        self.assertIn('你好 Minecraft'.encode('gbk'), data)
        self.assertIn(b'-' * 32, data)
        self.assertTrue(data.endswith(b'\x1dVB\x00'))

    def test_wrap_text_and_width(self):
        self.assertEqual(rp.display_width('中文abc'), 7)
        lines = rp.wrap_text('这是一段会被自动换行的聊天内容', 10)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(rp.display_width(line), 10)

    def test_clean_chat_text(self):
        text = rp.clean_chat_text('§a你好 §c世界🙂\n第二行')
        self.assertNotIn('§', text)
        self.assertNotIn('🙂', text)
        self.assertIn('你好', text)
        self.assertIn('第二行', text)

    def test_build_chat_ticket_fields(self):
        msg = rp.ChatMessage(sender='Steve', text='来主城看烟花',
                             kind='chat', time=time.time())
        ticket = rp.build_chat_ticket(msg, paper_width=58)
        texts = [c.arg for c in ticket.commands if c.op == 'TEXT']
        self.assertTrue(texts)
        self.assertIn('<Steve>', texts[0])
        self.assertIn('来主城看烟花', texts[0])
        self.assertTrue(texts[0].startswith('['))
        preview = rp.render_preview(ticket.commands, 32)
        self.assertIn('Steve', preview)
        self.assertIn('来主城看烟花', preview)
        # 不再打印小票标题 / 分隔线 / 切纸等额外内容
        self.assertNotIn('MINECRAFT', preview)
        self.assertNotIn('聊天小票', preview)
        self.assertNotIn('[切纸]', preview)

    def test_dry_run_writes_preview(self):
        msg = rp.ChatMessage(sender='Alex', text='测试小票')
        with tempfile.TemporaryDirectory() as tmp:
            result = rp.print_chat_ticket(msg, dry_run=True,
                                          preview_dir=tmp)
            self.assertTrue(result.dry_run)
            self.assertIsNotNone(result.out_file)
            self.assertTrue(os.path.isfile(result.out_file))
            self.assertGreater(result.bytes_written, 0)

    def test_explicit_printer_name_is_used(self):
        self.assertEqual(rp.resolve_printer_name('My Printer'), 'My Printer')

    def test_controller_queue_prints_chat(self):
        from aafm_py.controller import Controller
        import queue as queue_mod
        ctrl = object.__new__(Controller)
        misc = SimpleNamespace(
            mcChatPrintEnabled=True, mcChatPrintMinInterval=0.0,
            mcChatPrintMaxPerMinute=0, mcChatPaperWidth=58,
            mcChatPrinterName='', mcChatPrintDryRun=True,
            mcChatPrintIgnoreCommands=True,
        )
        ctrl.config = SimpleNamespace(misc=misc)
        ctrl._misc = lambda: misc
        ctrl._post_ui = lambda _ev: None
        ctrl._receipt_queue = queue_mod.Queue(maxsize=10)
        ctrl._receipt_thread = None
        ctrl._receipt_stop = threading.Event()
        ctrl._receipt_last_at = 0.0
        ctrl._receipt_times = []
        ctrl._receipt_error_at = 0.0
        ctrl._receipt_full_warn_at = 0.0
        ctrl._receipt_rate_warn_at = 0.0
        fake = Mock(return_value=SimpleNamespace(dry_run=True, out_file='x'))
        with patch('aafm_py.receipt_printer.print_chat_ticket', fake):
            ctrl._queue_mc_receipt('chat', 'Steve', 'hello')
            ctrl._receipt_queue.join()
        fake.assert_called_once()
        msg = fake.call_args.args[0]
        self.assertEqual(msg.sender, 'Steve')
        self.assertEqual(msg.text, 'hello')

    def test_stop_receipt_worker_discards_queued_items(self):
        from aafm_py.controller import Controller
        import queue as queue_mod
        ctrl = object.__new__(Controller)
        misc = SimpleNamespace(
            mcChatPrintEnabled=True, mcChatPrintMinInterval=0.0,
            mcChatPrintMaxPerMinute=0, mcChatPaperWidth=58,
            mcChatPrinterName='', mcChatPrintDryRun=True,
            mcChatPrintIgnoreCommands=True,
        )
        ctrl.config = SimpleNamespace(misc=misc)
        ctrl._misc = lambda: misc
        ctrl._post_ui = lambda _ev: None
        ctrl._receipt_queue = queue_mod.Queue(maxsize=10)
        ctrl._receipt_thread = None
        ctrl._receipt_stop = threading.Event()
        ctrl._receipt_last_at = 0.0
        ctrl._receipt_times = []
        ctrl._receipt_error_at = 0.0
        ctrl._receipt_full_warn_at = 0.0
        ctrl._receipt_rate_warn_at = 0.0

        started = threading.Event()
        release = threading.Event()
        calls = []

        def fake_print(message, **kwargs):
            calls.append(message.text)
            started.set()
            release.wait(3)
            return SimpleNamespace(dry_run=True, out_file='x')

        with patch('aafm_py.receipt_printer.print_chat_ticket', fake_print):
            ctrl._queue_mc_receipt('chat', 'Steve', 'first')
            self.assertTrue(started.wait(2))
            ctrl._receipt_queue.put({'kind': 'chat', 'sender': 'Steve',
                                     'text': 'second', 'time': time.time()})
            stopper = threading.Thread(target=ctrl._stop_receipt_worker)
            stopper.start()
            time.sleep(0.1)
            release.set()
            stopper.join(3)
        self.assertEqual(calls, ['first'])
        self.assertTrue(ctrl._receipt_queue.empty())


if __name__ == '__main__':
    unittest.main()
