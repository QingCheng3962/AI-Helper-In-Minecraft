# -*- coding: utf-8 -*-
"""好感度（affinity）：每个玩家一个分数，聊天/点歌会慢慢涨。

设计要点：
- 数据存本地 ``affinity.json``（**不要提交进仓库**），结构 ``{version, players:{名字: {...}}}``；
- 每次加分都有**冷却**（聊天默认 5 分钟才 +1），避免刷屏刷分；
- 每天第一次互动额外 +1（``lastDay``）；
- 分数 → 等级/称号，供 ``[触发词]好感度`` 查询。

这个模块只负责存取，不知道聊天/命令长什么样（方便单独测试）。
"""
from __future__ import annotations

import json
import copy
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# (起始分, 等级名, 备注)
LEVELS: List[Tuple[int, str, str]] = [
    (0, '陌生人', '刚认识'),
    (21, '脸熟', '见过几次'),
    (51, '熟人', '聊得挺来'),
    (101, '朋友', '关系不错'),
    (201, '好朋友', '经常一起玩'),
    (351, '挚友', '无话不谈'),
]

DEFAULT_CHAT_GAIN = 1
DEFAULT_CHAT_COOLDOWN = 300
DEFAULT_SONG_GAIN = 2
DEFAULT_DAILY_GAIN = 1


class AffinityStore:
    """好感度存取（线程安全，写盘用临时文件 + 原子替换）。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.data: Dict[str, Any] = {'version': 1, 'players': {}}
        self.last_error: str = ''
        self.load()

    # ------------------------------------------------------------------
    # 存取
    # ------------------------------------------------------------------
    def load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get('players'), dict):
                    self.data = data
                    self.data.setdefault('version', 1)
        except Exception:  # noqa: BLE001
            # 坏了就当空的，别让好感度把机器人拖崩
            self.data = {'version': 1, 'players': {}}

    def save(self) -> bool:
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self.last_error = ''
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            try:
                os.unlink(self.path + '.tmp')
            except OSError:
                pass
            return False

    def _entry(self, name: str) -> Dict[str, Any]:
        key = str(name or '').strip()
        players = self.data.setdefault('players', {})
        entry = players.get(key)
        if not isinstance(entry, dict):
            entry = {}
            players[key] = entry
        entry.setdefault('score', 0)
        entry.setdefault('chats', 0)
        entry.setdefault('songs', 0)
        entry.setdefault('lastChatAt', 0.0)
        entry.setdefault('lastSongAt', 0.0)
        entry.setdefault('lastDay', '')
        entry.setdefault('updated', 0.0)
        return entry

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @staticmethod
    def level_for(score: int) -> Tuple[str, str, Optional[int]]:
        """分数 → (等级名, 备注, 下一档所需分数)；已满级则第三项为 None。"""
        score = int(score or 0)
        name, blurb = LEVELS[0][1], LEVELS[0][2]
        for start, lname, lblurb in LEVELS:
            if score >= start:
                name, blurb = lname, lblurb
        nxt = None
        for start, _, _ in LEVELS:
            if start > score:
                nxt = start
                break
        return name, blurb, nxt

    def score(self, name: str) -> int:
        with self._lock:
            return int(self._entry(name).get('score') or 0)

    def info(self, name: str) -> Dict[str, Any]:
        with self._lock:
            entry = self._entry(name)
            score = int(entry.get('score') or 0)
            chats = int(entry.get('chats') or 0)
            songs = int(entry.get('songs') or 0)
        level, blurb, nxt = self.level_for(score)
        return {
            'name': str(name or ''),
            'score': score,
            'level': level,
            'blurb': blurb,
            'next': nxt,
            'toNext': (nxt - score) if nxt is not None else 0,
            'chats': chats,
            'songs': songs,
        }

    def top(self, n: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            items = [(k, int((v or {}).get('score') or 0))
                     for k, v in (self.data.get('players') or {}).items()]
        items.sort(key=lambda kv: kv[1], reverse=True)
        out = []
        for name, score in items[:max(0, n)]:
            level, _, _ = self.level_for(score)
            out.append({'name': name, 'score': score, 'level': level})
        return out

    # ------------------------------------------------------------------
    # 加分
    # ------------------------------------------------------------------
    def add(self, name: str, amount: int, reason: str = 'chat',
            cooldown: float = 0.0,
            daily_bonus: int = DEFAULT_DAILY_GAIN) -> Optional[int]:
        """加好感度。

        ``cooldown`` > 0 时，同一 ``reason`` 在冷却内**不重复加**（返回 None）。
        ``daily_bonus`` 是「每天第一次互动」的额外加分（当天只加一次）。
        返回加完后的分数（被冷却挡掉则返回 None）。
        """
        key = str(name or '').strip()
        if not key or amount == 0:
            return None
        now = time.time()
        today = time.strftime('%Y-%m-%d', time.localtime(now))
        with self._lock:
            entry = self._entry(key)
            snapshot = copy.deepcopy(entry)
            if cooldown > 0:
                field = 'lastChatAt' if reason == 'chat' else ('lastSongAt' if reason == 'song' else '')
                if field:
                    if now - float(entry.get(field) or 0.0) < cooldown:
                        return None
                    entry[field] = now
            gain = int(amount)
            if daily_bonus and entry.get('lastDay') != today:
                gain += int(daily_bonus)
                entry['lastDay'] = today
            entry['score'] = max(0, int(entry.get('score') or 0) + gain)
            if reason == 'chat':
                entry['chats'] = int(entry.get('chats') or 0) + 1
            elif reason == 'song':
                entry['songs'] = int(entry.get('songs') or 0) + 1
            entry['updated'] = now
            score = int(entry['score'])
            if not self.save():
                # 落盘失败：回滚内存，避免界面显示一个重启后不存在的分数。
                players = self.data.setdefault('players', {})
                players[key] = snapshot
                return None
        return score
