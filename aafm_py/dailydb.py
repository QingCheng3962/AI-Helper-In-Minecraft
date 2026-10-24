# -*- coding: utf-8 -*-
"""每日数据（SQLite）：当日幸运值 + 当日是否签到。

为什么要这个库：幸运值以前是「日期+名字」现算的，看着没存盘；改配置或重启后
玩家当天的运气值会变，签到也只有内存态。现在全部落到 ``daily.db``：

- ``lucky(day, player, value)``   —— 当天**第一次查询时就随机定下来并存好**，重启不变；
- ``checkin(day, player, streak, total)`` —— 当天签到记录；
- ``player_stats(player, last_day, streak, total)`` —— 跨天累计（连签天数/总次数）；
- ``meta(key, value)``           —— 记当前是哪一天。

**跨天**：发现库里的 ``current_day`` 不是今天就
「备份 → 删除 → 重建」：先把 ``daily.db`` 复制到 ``db_backup/db_backup_<时间>.db``，
再删掉重建（当天数据清空，``player_stats`` 从备份里带过来，连签才不会断）。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS lucky (
    day     TEXT NOT NULL,
    player  TEXT NOT NULL,
    value   INTEGER NOT NULL,
    updated REAL,
    PRIMARY KEY (day, player)
);
CREATE TABLE IF NOT EXISTS checkin (
    day     TEXT NOT NULL,
    player  TEXT NOT NULL,
    streak  INTEGER NOT NULL DEFAULT 0,
    total   INTEGER NOT NULL DEFAULT 0,
    updated REAL,
    PRIMARY KEY (day, player)
);
CREATE TABLE IF NOT EXISTS player_stats (
    player   TEXT PRIMARY KEY,
    last_day TEXT,
    streak   INTEGER NOT NULL DEFAULT 0,
    total    INTEGER NOT NULL DEFAULT 0
);
"""


def today_str(now: Optional[float] = None) -> str:
    return time.strftime('%Y-%m-%d', time.localtime(time.time() if now is None else now))


class DailyDB:
    """当天的幸运值 / 签到（SQLite，线程安全，跨天自动备份重建）。"""

    def __init__(self, path: str, backup_dir: Optional[str] = None):
        self.path = path
        self.backup_dir = backup_dir or os.path.join(os.path.dirname(path), 'db_backup')
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._open()
        self.ensure_day()

    # ------------------------------------------------------------------
    # 连接 / 建表
    # ------------------------------------------------------------------
    def _open(self) -> None:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.executescript(SCHEMA)
        conn.commit()
        self._conn = conn

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._open()
        return self._conn

    # ------------------------------------------------------------------
    # meta
    # ------------------------------------------------------------------
    def _get_meta(self, key: str, default: str = '') -> str:
        try:
            row = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return str(row[0]) if row else default
        except Exception:  # noqa: BLE001
            return default

    def _set_meta(self, key: str, value: str) -> None:
        try:
            self.conn.execute('INSERT INTO meta(key, value) VALUES(?, ?) '
                              'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              (key, str(value)))
            self.conn.commit()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 跨天：备份 → 删除 → 重建
    # ------------------------------------------------------------------
    def ensure_day(self, day: Optional[str] = None) -> Dict[str, Any]:
        """保证库的「当天」就是今天；跨天了就备份+重建。返回处理信息。"""
        day = day or today_str()
        with self._lock:
            cur = self._get_meta('current_day', '')
            if cur == day:
                return {'rotated': False, 'day': day, 'backup': ''}
            backup = ''
            if cur:
                backup = self._rotate(day)
            else:
                self._set_meta('current_day', day)
            return {'rotated': bool(cur), 'day': day, 'backup': backup}

    def _rotate(self, day: str) -> str:
        """把旧库备份到 db_backup/，删掉重建，并把 player_stats 带过来。

        备份失败时**不推进日期**（#20）：旧库保持原样，返回空串。
        """
        stats: List[tuple] = []
        try:
            stats = list(self.conn.execute(
                'SELECT player, last_day, streak, total FROM player_stats'))
        except Exception:  # noqa: BLE001
            stats = []
        self._close()
        backup_path = self._backup_name()
        backup_ok = False
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            if os.path.exists(self.path):
                shutil.copy2(self.path, backup_path)
                backup_ok = os.path.exists(backup_path) and os.path.getsize(backup_path) > 0
            else:
                backup_ok = True          # 没旧库也算成功
            if not backup_ok:
                # 备份失败 → 不删除原库，重连后保持旧日期
                self._open()
                return ''
            for extra in ('-wal', '-shm', '-journal'):
                p = self.path + extra
                if os.path.exists(p):
                    os.remove(p)
            if os.path.exists(self.path):
                os.remove(self.path)
        except Exception:  # noqa: BLE001
            # 任何异常 → 不推进日期，尝试恢复连接
            try:
                self._open()
            except Exception:  # noqa: BLE001
                pass
            return ''
        self._open()                                  # 重建（空库）
        try:
            self.conn.executemany(
                'INSERT OR REPLACE INTO player_stats(player, last_day, streak, total) '
                'VALUES(?, ?, ?, ?)', stats)
            self.conn.commit()
        except Exception:  # noqa: BLE001
            pass
        self._set_meta('current_day', day)
        return backup_path

    def _backup_name(self) -> str:
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())
        name = f'db_backup_{stamp}.db'
        p = os.path.join(self.backup_dir, name)
        i = 1
        while os.path.exists(p):
            p = os.path.join(self.backup_dir, f'db_backup_{stamp}_{i}.db')
            i += 1
        return p

    # ------------------------------------------------------------------
    # 幸运值
    # ------------------------------------------------------------------
    def get_lucky(self, player: str, max_value: int = 100,
                  day: Optional[str] = None) -> int:
        """取玩家**今天**的幸运值；没有就随机定一个存起来（重启也不变）。"""
        import random
        key = str(player or '').strip()
        day = day or today_str()
        mx = max(2, int(max_value or 100))
        if not key:
            return random.randint(1, mx)
        with self._lock:
            self.ensure_day(day)
            try:
                row = self.conn.execute('SELECT value FROM lucky WHERE day=? AND player=?',
                                        (day, key)).fetchone()
            except Exception:  # noqa: BLE001
                row = None
            if row is not None:
                try:
                    # 已存好的幸运值直接返回，不再用当前上限裁剪（#21）
                    # —— 修改上限不应该改变同一玩家当天的查询结果
                    return max(1, int(row[0]))
                except (TypeError, ValueError):
                    pass
            value = random.randint(1, mx)
            try:
                self.conn.execute('INSERT OR REPLACE INTO lucky(day, player, value, updated) '
                                  'VALUES(?, ?, ?, ?)', (day, key, value, time.time()))
                self.conn.commit()
            except Exception:  # noqa: BLE001
                pass
            return value

    def lucky_count(self, day: Optional[str] = None) -> int:
        day = day or today_str()
        with self._lock:
            try:
                return int(self.conn.execute('SELECT COUNT(*) FROM lucky WHERE day=?',
                                             (day,)).fetchone()[0])
            except Exception:  # noqa: BLE001
                return 0

    # ------------------------------------------------------------------
    # 签到
    # ------------------------------------------------------------------
    def is_signed(self, player: str, day: Optional[str] = None) -> bool:
        key = str(player or '').strip()
        day = day or today_str()
        with self._lock:
            self.ensure_day(day)
            try:
                row = self.conn.execute('SELECT 1 FROM checkin WHERE day=? AND player=?',
                                        (day, key)).fetchone()
                return row is not None
            except Exception:  # noqa: BLE001
                return False

    def sign(self, player: str, day: Optional[str] = None) -> Dict[str, Any]:
        """签一次。``ok=False`` 表示今天已经签过了。"""
        key = str(player or '').strip()
        day = day or today_str()
        if not key:
            return {'ok': False, 'streak': 0, 'total': 0, 'day': day}
        with self._lock:
            self.ensure_day(day)
            try:
                row = self.conn.execute(
                    'SELECT streak, total FROM checkin WHERE day=? AND player=?',
                    (day, key)).fetchone()
            except Exception:  # noqa: BLE001
                row = None
            if row is not None:
                return {'ok': False, 'streak': int(row[0] or 0),
                        'total': int(row[1] or 0), 'day': day}
            # 连签：昨天签过就 +1，否则从 1 开始
            prev = None
            try:
                prev = self.conn.execute(
                    'SELECT last_day, streak, total FROM player_stats WHERE player=?',
                    (key,)).fetchone()
            except Exception:  # noqa: BLE001
                prev = None
            yday = time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400))
            streak = 1
            total = 1
            if prev is not None:
                last_day = str(prev[0] or '')
                streak = int(prev[1] or 0) + 1 if last_day == yday else 1
                total = int(prev[2] or 0) + 1
            try:
                self.conn.execute(
                    'INSERT OR REPLACE INTO checkin(day, player, streak, total, updated) '
                    'VALUES(?, ?, ?, ?, ?)', (day, key, streak, total, time.time()))
                self.conn.execute(
                    'INSERT INTO player_stats(player, last_day, streak, total) '
                    'VALUES(?, ?, ?, ?) ON CONFLICT(player) DO UPDATE SET '
                    'last_day=excluded.last_day, streak=excluded.streak, total=excluded.total',
                    (key, day, streak, total))
                self.conn.commit()
            except Exception:  # noqa: BLE001
                pass
            return {'ok': True, 'streak': streak, 'total': total, 'day': day}

    def stats(self, player: str) -> Dict[str, Any]:
        """累计信息（连签天数显示用；昨天没签要显示成断了）。"""
        key = str(player or '').strip()
        with self._lock:
            try:
                row = self.conn.execute(
                    'SELECT last_day, streak, total FROM player_stats WHERE player=?',
                    (key,)).fetchone()
            except Exception:  # noqa: BLE001
                row = None
        if row is None:
            return {'last_day': '', 'streak': 0, 'total': 0, 'today': False}
        last_day = str(row[0] or '')
        yday = time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400))
        streak = int(row[1] or 0) if last_day in (today_str(), yday) else 0
        return {'last_day': last_day, 'streak': streak, 'total': int(row[2] or 0),
                'today': last_day == today_str()}

    def signed_count(self, day: Optional[str] = None) -> int:
        day = day or today_str()
        with self._lock:
            try:
                return int(self.conn.execute('SELECT COUNT(*) FROM checkin WHERE day=?',
                                             (day,)).fetchone()[0])
            except Exception:  # noqa: BLE001
                return 0

    def close(self) -> None:
        with self._lock:
            self._close()
