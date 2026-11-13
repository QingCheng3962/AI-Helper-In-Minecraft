#!/usr/bin/env python3
"""Aafm AI Helper —— 启动入口。

用法:
    pip install -r requirements.txt
    python main.py            # 新的 CustomTkinter 界面（推荐）
    python main.py --legacy   # 旧的 tkinter 界面（11 个标签页，功能最全）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    legacy = '--legacy' in sys.argv[1:]
    if legacy:
        from aafm_py.gui import main as legacy_main
        legacy_main()
        return
    try:
        from aafm_py.gui_ctk import main as ctk_main
    except SystemExit as exc:
        print('[启动] CustomTkinter 界面不可用：', exc)
        print('[启动] 回退到旧界面（pip install customtkinter 后可再用新界面）')
        from aafm_py.gui import main as legacy_main
        legacy_main()
        return
    ctk_main()


if __name__ == '__main__':
    main()
