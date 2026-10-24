# -*- coding: utf-8 -*-
"""每日数据（SQLite）：当日幸运值 + 当日是否签到。

为什么要这个库：幸运值以前是「日期+名字」现算的，看着没存盘；改配置或重启后
玩家当天的运气值会变，签到也只有内存态。现在全部落到 ``daily.db``：

- ``lucky(day, player, value)``   —— 当天**第一次查询时就随机定下来并存好**，重启不变；
- ``checkin(day, player, streak, total)`` —— 当天签到记录；
- ``player_stats(player, last_day, streak, total)`` —— 跨天累计（连签天数/总次数）；
- ``checkin_rewards`` —— 固定獎勵快照及逐項發送狀態，未完成項目跨日承接；
- ``meta(key, value)``           —— 记当前是哪一天。

**跨天**：发现库里的 ``current_day`` 不是今天就
先完整建立承接統計與未完成獎勵的新庫，再備份舊庫至
``db_backup/db_backup_<时间>.db`` 並替換 ``daily.db``；失敗保留原庫供重試。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

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
CREATE TABLE IF NOT EXISTS checkin_rewards (
    day TEXT NOT NULL,
    player TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('money', 'item')),
    snapshot TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending', 'sending', 'submitted', 'unknown')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated REAL,
    PRIMARY KEY(day, player, kind)
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
        try:
            self.ensure_day()
        except Exception:  # noqa: BLE001
            pass          # 启动时轮转失败不应让整个程序起不来；下次 tick 会重试
        # 上次進程可能在提交發送意圖後中斷；不能推論命令尚未送出。
        with self.conn:
            self.conn.execute("UPDATE checkin_rewards SET state='unknown', "
                              "last_error='interrupted', updated=? WHERE state='sending'", (time.time(),))
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
        """先完整建立新库再替换；备份失败时不推进日期（#20），未完成奖励不因跨日丢失。"""
        # 读取旧数据失败时不继续轮转（旧库保持原样，#20）
        stats = list(self.conn.execute(
            'SELECT player, last_day, streak, total FROM player_stats'))
        rewards = list(self.conn.execute(
            "SELECT day, player, kind, snapshot, state, attempts, last_error, updated "
            "FROM checkin_rewards WHERE state!='submitted' OR day>=?", (day,)))
        backup_path = self._backup_name()
        staging = self.path + '.rotate-' + uuid.uuid4().hex
        try:
            new = sqlite3.connect(staging)
            try:
                new.executescript(SCHEMA)
                new.executemany('INSERT INTO player_stats VALUES(?, ?, ?, ?)', stats)
                new.executemany('INSERT INTO checkin_rewards VALUES(?, ?, ?, ?, ?, ?, ?, ?)', rewards)
                new.execute('INSERT INTO meta(key, value) VALUES(?, ?)', ('current_day', day))
                new.commit()
            finally:
                new.close()
            os.makedirs(self.backup_dir, exist_ok=True)
            self.conn.commit()
            self.conn.close()
            self._conn = None
            try:
                if os.path.exists(self.path):
                    shutil.copy2(self.path, backup_path)
                    if not (os.path.exists(backup_path) and os.path.getsize(backup_path) > 0):
                        raise OSError('backup verification failed')
                os.replace(staging, self.path)
            finally:
                # 失败时 os.replace 没执行 → 重新打开的还是旧库（日期不推进，#20）
                self._open()
        finally:
            if os.path.exists(staging):
                try:
                    os.remove(staging)
                except OSError:
                    pass
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
            try:
                self.ensure_day(day)
            except Exception:  # noqa: BLE001
                pass          # 轮转失败：继续用旧库读，日期不会被推进（#20）
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
            try:
                self.ensure_day(day)
            except Exception:  # noqa: BLE001
                pass          # 轮转失败：按旧库回答，避免命令线程崩掉
            try:
                row = self.conn.execute('SELECT 1 FROM checkin WHERE day=? AND player=?',
                                        (day, key)).fetchone()
                return row is not None
            except Exception:  # noqa: BLE001
                return False

    def sign(self, player: str, day: Optional[str] = None, *,
             reward_factory: Optional[Callable[[int, int], List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
        """只在交易提交成功後回報成功；reason 區分重複與儲存失敗。"""
        key = str(player or '').strip()
        day = day or today_str()
        if not key:
            return {'ok': False, 'reason': 'invalid_player', 'streak': 0, 'total': 0, 'day': day}
        with self._lock:
            try:
                self.ensure_day(day)
                self.conn.execute('BEGIN IMMEDIATE')
                row = self.conn.execute(
                    'SELECT streak, total FROM checkin WHERE day=? AND player=?',
                    (day, key)).fetchone()
                if row is not None:
                    self.conn.rollback()
                    return {'ok': False, 'reason': 'already_signed', 'streak': int(row[0] or 0),
                            'total': int(row[1] or 0), 'day': day}
                prev = self.conn.execute(
                    'SELECT last_day, streak, total FROM player_stats WHERE player=?',
                    (key,)).fetchone()
                yday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
                streak = 1
                total = 1
                if prev is not None:
                    last_day = str(prev[0] or '')
                    streak = int(prev[1] or 0) + 1 if last_day == yday else 1
                    total = int(prev[2] or 0) + 1
                self.conn.execute(
                    'INSERT INTO checkin(day, player, streak, total, updated) '
                    'VALUES(?, ?, ?, ?, ?)', (day, key, streak, total, time.time()))
                self.conn.execute(
                    'INSERT INTO player_stats(player, last_day, streak, total) '
                    'VALUES(?, ?, ?, ?) ON CONFLICT(player) DO UPDATE SET '
                    'last_day=excluded.last_day, streak=excluded.streak, total=excluded.total',
                    (key, day, streak, total))
                if reward_factory is not None:
                    for reward in reward_factory(streak, total):
                        self.conn.execute(
                            'INSERT INTO checkin_rewards(day, player, kind, snapshot, state, updated) '
                            "VALUES(?, ?, ?, ?, 'pending', ?)",
                            (day, key, reward['kind'], json.dumps(reward, ensure_ascii=False), time.time()))
                self.conn.commit()
            except Exception as exc:  # noqa: BLE001
                try:
                    self.conn.rollback()
                except Exception:  # noqa: BLE001
                    # 不讓下一次操作繼續使用狀態不明的連線。
                    conn, self._conn = self._conn, None
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:  # noqa: BLE001
                            pass
                return {'ok': False, 'reason': 'invalid_reward' if isinstance(exc, ValueError) else 'storage_error',
                        'streak': 0, 'total': 0, 'day': day}
            return {'ok': True, 'reason': 'signed', 'streak': streak, 'total': total, 'day': day}

    def rewards(self, player: str, day: Optional[str] = None) -> List[Dict[str, Any]]:
        """當日全部項目及歷史未完成項目；舊版沒有快照的簽到不補建獎勵。"""
        with self._lock:
            rows = self.conn.execute(
                'SELECT day, player, kind, snapshot, state, attempts, last_error FROM checkin_rewards '
                "WHERE player=? AND (day=? OR state!='submitted') "
                "ORDER BY day, CASE kind WHEN 'money' THEN 0 ELSE 1 END",
                (str(player).strip(), day or today_str())).fetchall()
            return [dict(day=r[0], player=r[1], kind=r[2], snapshot=json.loads(r[3]),
                         state=r[4], attempts=r[5], last_error=r[6]) for r in rows]

    def claim_reward(self, reward: Dict[str, Any]) -> bool:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "UPDATE checkin_rewards SET state='sending', attempts=attempts+1, updated=? "
                "WHERE day=? AND player=? AND kind=? AND state='pending'",
                (time.time(), reward['day'], reward['player'], reward['kind']))
            return cur.rowcount == 1

    def finish_reward(self, reward: Dict[str, Any], status: str, error: str = '') -> None:
        state = {'submitted': 'submitted', 'not_sent': 'pending', 'unknown': 'unknown'}[status]
        with self._lock, self.conn:
            cur = self.conn.execute(
                'UPDATE checkin_rewards SET state=?, last_error=?, updated=? '
                "WHERE day=? AND player=? AND kind=? AND state='sending'",
                (state, error, time.time(), reward['day'], reward['player'], reward['kind']))
            if cur.rowcount != 1:
                raise RuntimeError('Reward dispatch state changed')

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
