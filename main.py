#!/usr/bin/env python3
"""Aafm AI 
用法:
    pip install -r requirements.txt
    python main.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aafm_py.gui import main  # noqa: E402

if __name__ == '__main__':
    main()
