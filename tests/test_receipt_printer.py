# -*- coding: utf-8 -*-
"""MC 聊天小票机回归测试（不真的打印）。"""
from __future__ import annotations

import os
import tempfile
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
        ticket = rp.build_chat_ticket(msg, paper_width=58, body_lines=10,
                                      serial=7)
        texts = [c.arg for c in ticket.commands if c.op == 'TEXT']
        self.assertIn('MINECRAFT', texts)
        self.assertIn('聊天小票', texts)
        self.assertIn('#000007', texts)
        self.assertIn('玩家：Steve', texts)
        self.assertIn('来主城看烟花', texts)
        preview = rp.render_preview(ticket.commands, 32)
        self.assertIn('Steve', preview)
        self.assertIn('来主城看烟花', preview)

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
            mcChatBodyLines=10, mcChatPrinterName='', mcChatPrintDryRun=True,
            mcChatPrintServer=True, mcChatPrintBot=True,
            mcChatPrintActionBar=False, mcChatPrintIgnoreCommands=True,
        )
        ctrl.config = SimpleNamespace(misc=misc)
        ctrl._misc = lambda: misc
        ctrl._post_ui = lambda _ev: None
        ctrl._receipt_queue = queue_mod.Queue(maxsize=10)
        ctrl._receipt_thread = None
        ctrl._receipt_serial = 0
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


if __name__ == '__main__':
    unittest.main()
