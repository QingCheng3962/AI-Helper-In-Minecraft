"""Startup online update check for NapCat and this program.

Runs in a background thread and reports results through a log callback so the
GUI stays responsive and offline failures are non-fatal.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAPCAT_VERSION_FILE = os.path.join(BASE_DIR, 'NapCat', 'napcat', '.aafm_version')

NAPCAT_REPO = 'NapNeko/NapCatQQ'
PROGRAM_REPO = 'QingCheng3962/AI-Helper-In-Minecraft'
NAPCAT_URL = f'https://github.com/{NAPCAT_REPO}/releases/latest'
PROGRAM_URL = f'https://github.com/{PROGRAM_REPO}'

_LOG = Callable[[str, str], None]  # (level, message)


def _http_json(url: str):
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return None
    try:
        r = httpx.get(url, timeout=8.0, follow_redirects=True,
                      headers={'User-Agent': 'aafm-updater', 'Accept': 'application/vnd.github+json'})
        if r.status_code == 200:
            return r.json()
    except Exception:  # noqa: BLE001
        return None
    return None


def _ver_tuple(text: str):
    nums = re.findall(r'\d+', str(text or ''))
    return tuple(int(n) for n in nums[:4]) if nums else ()


def read_napcat_version() -> str:
    try:
        with open(NAPCAT_VERSION_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def latest_napcat_version() -> str:
    data = _http_json(f'https://api.github.com/repos/{NAPCAT_REPO}/releases/latest')
    if isinstance(data, dict):
        tag = str(data.get('tag_name') or '')
        return tag.lstrip('vV')
    return ''


def local_git_head() -> str:
    try:
        out = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=BASE_DIR,
                             capture_output=True, text=True, timeout=8)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return ''


def latest_program_commit() -> Optional[dict]:
    data = _http_json(f'https://api.github.com/repos/{PROGRAM_REPO}/commits?per_page=1')
    if isinstance(data, list) and data:
        item = data[0]
        return {
            'sha': str(item.get('sha') or ''),
            'message': str((item.get('commit') or {}).get('message') or '').splitlines()[0],
        }
    return None


def check_updates(log: _LOG) -> None:
    """Run both checks; ``log(level, message)`` for each result."""
    checked_any = False

    # --- NapCat ---
    local = read_napcat_version()
    latest = latest_napcat_version()
    if latest:
        checked_any = True
        if local and _ver_tuple(latest) > _ver_tuple(local):
            log('warn', f'[更新] NapCat 有新版本 v{latest}（当前 v{local}），可到 {NAPCAT_URL} 下载。')
        elif local:
            log('info', f'[更新] NapCat 已是最新（v{local}）。')

    # --- Program ---
    remote = latest_program_commit()
    if remote:
        checked_any = True
        local_sha = local_git_head()
        if local_sha and remote['sha'] and remote['sha'] != local_sha:
            log('warn', f"[更新] 本程序有更新：{remote['message']}（{remote['sha'][:7]}），仓库 {PROGRAM_URL}")
        elif local_sha:
            log('info', '[更新] 本程序已是最新。')

    if not checked_any:
        log('info', '[更新] 无法联网检查更新（可忽略）。')
