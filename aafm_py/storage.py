# -*- coding: utf-8 -*-
"""统一数据存储（SQLite data.db）。

v0.3.6-26.11.14 起，运行数据从「一个功能一个 JSON 文件」改成「一个 SQLite 数据库」：

| 表 | 原文件 | 内容 |
| --- | --- | --- |
| affinity | affinity.json | 每个玩家一行（JSON 存细节） |
| kv | book_progress.json | 通用键值（写书进度等） |
| prompt_templates | mcai_prompt_templates.json | 系统提示词模板 |
| qq_accounts | qq_accounts.json | 自动写配置用过的 QQ 号 / 昵称 |

要点：

- 自动迁移：老 JSON 存在而库里没有数据时，第一次访问会导入并把老文件改名成
  <名字>.json.bak（不删，出问题还能翻回去）；
- 线程安全：一个库一把 RLock（GUI 渲染线程 + 后台网络/播放线程都会写）；
- WAL：journal_mode=WAL 读写不互相卡；写失败会抛异常，由各调用方决定回滚/提示；
- 设置文件（config.json / mcai_chat.json）仍是 JSON：它们需要人手改、好备份，
  而且带 API Key，放 DB 里反而不方便。

纯标准库（sqlite3/os/json），不新增依赖。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import weakref
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: 数据库文件名（放在项目根目录）
DATA_FILE = 'data.db'

#: 表结构（加字段请改这里 + 写迁移）
SCHEMA = (
    'CREATE TABLE IF NOT EXISTS kv ('
    ' key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL DEFAULT 0)',
    'CREATE TABLE IF NOT EXISTS affinity ('
    ' name TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL DEFAULT 0)',
    'CREATE TABLE IF NOT EXISTS prompt_templates ('
    ' name TEXT PRIMARY KEY, prompt TEXT NOT NULL, updated REAL NOT NULL DEFAULT 0)',
    'CREATE TABLE IF NOT EXISTS qq_accounts ('
    ' uin TEXT PRIMARY KEY, nickname TEXT NOT NULL DEFAULT \'\','
    ' updated REAL NOT NULL DEFAULT 0)',
    'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)',
)

#: 表名 → 允许的列（防止 SQL 拼接被人从外部塞表名）
_TABLES = {
    'kv': ('key', 'value'),
    'affinity': ('name', 'data'),
    'prompt_templates': ('name', 'prompt'),
    'qq_accounts': ('uin', 'nickname'),
    'meta': ('key', 'value'),
}


def _now() -> float:
    return time.time()


class Database:
    """一个 SQLite 连接 + 一把锁；按库路径缓存（同进程共享）。"""

    #: 弱引用缓存：没人用的时候连接会自动关掉（Windows 下临时目录才能删干净）
    _instances = weakref.WeakValueDictionary()
    _instances_lock = threading.Lock()

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA synchronous=NORMAL')
            self._conn.execute('PRAGMA busy_timeout=15000')
            for sql in SCHEMA:
                self._conn.execute(sql)
            self._conn.commit()

    @classmethod
    def for_base(cls, base_dir: str) -> 'Database':
        path = os.path.join(base_dir or '.', DATA_FILE)
        key = os.path.abspath(path).lower()
        with cls._instances_lock:
            db = cls._instances.get(key)
            if db is None:
                db = cls(path)
                cls._instances[key] = db
            return db

    # ------------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            if self._conn is None:
                raise sqlite3.ProgrammingError('database is closed')
            cursor = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cursor

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            if self._conn is None:
                raise sqlite3.ProgrammingError('database is closed')
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    # ------------------------------------------------------------------
    # 通用表操作（列名固定、表名白名单）
    # ------------------------------------------------------------------
    def _columns(self, table: str) -> Tuple[str, str]:
        if table not in _TABLES:
            raise ValueError('unknown table: ' + table)
        return _TABLES[table]

    def get(self, table: str, key: str) -> Optional[str]:
        key_col, value_col = self._columns(table)
        rows = self.query('SELECT %s FROM %s WHERE %s = ?' % (value_col, table, key_col), (key,))
        return rows[0][0] if rows else None

    def set(self, table: str, key: str, value: str) -> None:
        key_col, value_col = self._columns(table)
        self.execute(
            'INSERT INTO %s (%s, %s, updated) VALUES (?, ?, ?) '
            'ON CONFLICT(%s) DO UPDATE SET %s = excluded.%s, updated = excluded.updated'
            % (table, key_col, value_col, key_col, value_col, value_col),
            (key, value, _now()))

    def delete(self, table: str, key: str) -> None:
        key_col, _ = self._columns(table)
        self.execute('DELETE FROM %s WHERE %s = ?' % (table, key_col), (key,))

    def items(self, table: str) -> List[Tuple[str, str]]:
        key_col, value_col = self._columns(table)
        rows = self.query('SELECT %s, %s FROM %s' % (key_col, value_col, table))
        return [(row[0], row[1]) for row in rows]

    def count(self, table: str) -> int:
        rows = self.query('SELECT COUNT(*) FROM %s' % table)
        return int(rows[0][0]) if rows else 0

    def clear(self, table: str) -> None:
        self.execute('DELETE FROM %s' % table)

    def close(self) -> None:
        with self._lock:
            conn = getattr(self, '_conn', None)
            if conn is None:
                return
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __del__(self):  # noqa: D401  - 没人引用时自动关连接
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def close_all(cls) -> None:
        """退出时关掉所有连接（Windows 下不关会占着 data.db / WAL 文件）。"""
        with cls._instances_lock:
            for db in list(cls._instances.values()):
                db.close()
            cls._instances.clear()


def database_for(path: str) -> Database:
    """由旧数据文件的路径推出同一个 data.db（同目录）。"""
    folder = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
    return Database.for_base(folder or os.getcwd())


def backup_json(path: str) -> None:
    """迁移成功后把老 JSON 改名成 .json.bak（不删）。"""
    if not path or not os.path.exists(path):
        return
    target = path + '.bak'
    if os.path.exists(target):
        target = path + '.bak.' + time.strftime('%Y%m%d_%H%M%S')
    try:
        os.replace(path, target)
    except OSError:
        pass


class JsonTable:
    """「每个键一行 JSON」的表（好感度）。会自动从老 JSON 文件迁移。"""

    def __init__(self, legacy_path: str, table: str = 'affinity',
                 dict_key: str = 'players'):
        self.legacy_path = legacy_path
        self.table = table
        self.dict_key = dict_key
        self.db = database_for(legacy_path)
        self._migrated = False

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        self.migrate()
        out: Dict[str, Dict[str, Any]] = {}
        for key, raw in self.db.items(self.table):
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            if isinstance(value, dict):
                out[key] = value
        return out

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        self.migrate()
        raw = self.db.get(self.table, key)
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def set(self, key: str, value: Dict[str, Any]) -> None:
        self.db.set(self.table, key, json.dumps(value, ensure_ascii=False))

    def delete(self, key: str) -> None:
        self.db.delete(self.table, key)

    def bulk_set(self, mapping: Dict[str, Dict[str, Any]]) -> None:
        for key, value in mapping.items():
            self.set(key, value)

    def migrate(self) -> None:
        """老 JSON → 库（只在库为空时做一次；失败下次还会重试）。"""
        if self._migrated:
            return
        try:
            self._migrate_once()
            self._migrated = True
        except Exception:  # noqa: BLE001
            self._migrated = False
            raise

    def _migrate_once(self) -> None:
        if self.db.count(self.table) > 0:
            return
        if not self.legacy_path or not os.path.exists(self.legacy_path):
            return
        try:
            with open(self.legacy_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        players = data.get(self.dict_key) if isinstance(data, dict) else None
        if isinstance(players, dict):
            for name, entry in players.items():
                if isinstance(entry, dict):
                    self.set(str(name), entry)
        backup_json(self.legacy_path)


class NamedTable:
    """「键 → 一个字符串值」的表（提示词模板 / QQ 账号）。"""

    def __init__(self, legacy_path: str, table: str, legacy_format: str = 'list'):
        #: legacy_format: list = [{name, prompt}]（模板）；dict = {uin: nickname}
        self.legacy_path = legacy_path
        self.table = table
        self.legacy_format = legacy_format
        self.db = database_for(legacy_path)
        self._migrated = False

    def load_all(self) -> Dict[str, str]:
        self.migrate()
        return {key: value for key, value in self.db.items(self.table)}

    def get(self, key: str) -> Optional[str]:
        self.migrate()
        return self.db.get(self.table, key)

    def set(self, key: str, value: str) -> None:
        self.db.set(self.table, key, value or '')

    def delete(self, key: str) -> None:
        self.db.delete(self.table, key)

    def replace_all(self, mapping: Dict[str, str]) -> None:
        self.migrate()          # 先把老 JSON 迁进来（并备份），再整体替换
        self.db.clear(self.table)
        for key, value in mapping.items():
            self.set(key, value)

    def migrate(self) -> None:
        if self._migrated:
            return
        try:
            self._migrate_once()
            self._migrated = True
        except Exception:  # noqa: BLE001
            self._migrated = False
            raise

    def _migrate_once(self) -> None:
        if self.db.count(self.table) > 0:
            return
        if not self.legacy_path or not os.path.exists(self.legacy_path):
            return
        try:
            with open(self.legacy_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if self.legacy_format == 'dict' and isinstance(data, dict):
            for key, value in data.items():
                self.set(str(key), '' if value is None else str(value))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get('name'):
                    self.set(str(item['name']), str(item.get('prompt') or ''))
        backup_json(self.legacy_path)


class KeyValueStore:
    """kv 表里某个键的 JSON 值（写书进度等）。"""

    def __init__(self, legacy_path: str, key: str):
        self.legacy_path = legacy_path
        self.key = key
        self.db = database_for(legacy_path)
        self._migrated = False

    def get(self) -> Optional[Dict[str, Any]]:
        self.migrate()
        raw = self.db.get('kv', self.key)
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def set(self, value: Dict[str, Any]) -> None:
        self.db.set('kv', self.key, json.dumps(value, ensure_ascii=False))

    def delete(self) -> None:
        self.db.delete('kv', self.key)

    def migrate(self) -> None:
        if self._migrated:
            return
        try:
            self._migrate_once()
            self._migrated = True
        except Exception:  # noqa: BLE001
            self._migrated = False
            raise

    def _migrate_once(self) -> None:
        if self.db.get('kv', self.key) is not None:
            return
        if not self.legacy_path or not os.path.exists(self.legacy_path):
            return
        try:
            with open(self.legacy_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data:
            self.set(data)
        backup_json(self.legacy_path)
