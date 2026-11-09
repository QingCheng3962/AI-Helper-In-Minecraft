# -*- coding: utf-8 -*-
"""Roster 写回：插列要修公式，玩家文本不能变成公式（#42/#54）。"""
from __future__ import annotations

import os
import tempfile
import unittest

from aafm_py.roster import write_main


class RosterFormulaTest(unittest.TestCase):
    def test_insert_main_column_rewrites_formula_references(self):
        try:
            from openpyxl import Workbook, load_workbook
        except Exception as e:  # noqa: BLE001
            self.skipTest('openpyxl unavailable: ' + str(e))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'roster.xlsx')
            wb = Workbook()
            ws = wb.active
            ws.append(['玩家名', '描述1', '描述2', '总分'])
            ws.append(['Alice', 1, 2, '=SUM(B2:C2)'])
            ws.append(['Bob', 3, 4, '=SUM(B2:C2)'])
            wb.save(path)

            write_main(path, 'Alice', '新描述')

            wb = load_workbook(path)
            ws = wb.active
            self.assertEqual(ws['E1'].value, '总分')
            self.assertEqual(ws['B2'].value, '新描述')
            self.assertEqual(ws['E2'].value, '=SUM(C2:D2)')
            self.assertEqual(ws['E3'].value, '=SUM(C2:D2)')
            wb.close()

    def test_formula_like_player_text_is_escaped(self):
        try:
            from openpyxl import Workbook, load_workbook
        except Exception as e:  # noqa: BLE001
            self.skipTest('openpyxl unavailable: ' + str(e))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'roster.xlsx')
            wb = Workbook()
            ws = wb.active
            ws.append(['玩家名', '玩家描述'])
            wb.save(path)

            write_main(path, '=2+2', '=HYPERLINK("https://example.test")')
            wb = load_workbook(path)
            ws = wb.active
            self.assertTrue(str(ws['A2'].value).startswith("'="))
            self.assertTrue(str(ws['B2'].value).startswith("'="))
            wb.close()

    def test_csv_formula_like_text_is_escaped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'roster.csv')
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                f.write('玩家名,玩家描述\nAlice,x\n')
            write_main(path, '=cmd', '@SUM(A1)')
            with open(path, 'r', encoding='utf-8-sig') as f:
                text = f.read()
        self.assertIn("'=cmd", text)
        self.assertIn("'@SUM(A1)", text)


if __name__ == '__main__':
    unittest.main()
