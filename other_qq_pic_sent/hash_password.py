#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Web 登录密码哈希（PBKDF2-SHA256）。

用法：
    python other_qq_pic_sent/hash_password.py '你的密码'
把输出填到 relay.json 的 web.password_hash。
"""
from __future__ import annotations

import sys

try:
    from .webapp import make_password_hash
except ImportError:
    from webapp import make_password_hash


def main() -> None:
    password = sys.argv[1] if len(sys.argv) > 1 else ''
    if not password:
        print("用法: python hash_password.py '你的密码'", file=sys.stderr)
        raise SystemExit(1)
    print(make_password_hash(password))


if __name__ == '__main__':
    main()
