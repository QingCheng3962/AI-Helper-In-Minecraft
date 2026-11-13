"""项目官方链接（支持与工单 / 开发者博客）。

集中放在这里，界面（CustomTkinter / tkinter）和文档都引用同一份，
改域名只改这里。
"""
from __future__ import annotations

import webbrowser

#: 支持与工单（提 issue / 找作者）
SUPPORT_URL = 'https://support.qingchengsep.top'
#: 开发者博客（更新日志 / 作者文章）
BLOG_URL = 'https://qingchengsep.top'
#: 项目仓库
REPO_URL = 'https://github.com/QingCheng3962/AI-Helper-In-Minecraft'


def open_url(url: str) -> bool:
    """用系统浏览器打开链接；失败返回 False（界面据此提示）。"""
    target = (url or '').strip()
    if not target:
        return False
    if not (target.startswith('http://') or target.startswith('https://')):
        return False
    try:
        return bool(webbrowser.open(target, new=2))
    except Exception:  # noqa: BLE001
        return False


def open_support() -> bool:
    return open_url(SUPPORT_URL)


def open_blog() -> bool:
    return open_url(BLOG_URL)


def open_repo() -> bool:
    return open_url(REPO_URL)
