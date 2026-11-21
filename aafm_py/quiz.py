"""问答（聊天抢答）：SQLite 题库 + 答题记录 + 出题/判分/奖励。

题库存在 `quiz.db`（questions 表），答题记录在 records 表；另外支持 JSON 导入/导出
（格式参考 LetMeAsk：{q, a, weight}，本模块扩展了 type/options/reward）。
"""
from __future__ import annotations

import json
import random
import sqlite3
import time
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

LOCK = threading.RLock()

DEFAULT_QUESTIONS: List[Dict[str, Any]] = [
    {'type': 'text', 'q': '中国的首都是哪里？', 'a': ['北京', 'Beijing'], 'weight': 1},
    {'type': 'blank', 'q': '中国的首都是___，最大的城市是___',
     'a': [['北京', 'Beijing'], ['上海', 'Shanghai']], 'weight': 1},
    {'type': 'choice', 'q': '太阳系最大的行星是？',
     'options': ['A. 地球', 'B. 木星', 'C. 火星', 'D. 土星'],
     'a': 'B', 'weight': 1},
]


def _norm(text: str) -> str:
    s = str(text or '').strip().lower()
    for ch in '，,。.、！!？?；;:：\'"“”‘’（）()[]【】 ':
        s = s.replace(ch, '')
    return s


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


class QuizStore:
    """quiz.db：题库 + 答题记录 + 每日提问次数。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with LOCK:
            cur = self._conn.cursor()
            cur.execute("""CREATE TABLE IF NOT EXISTS questions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL DEFAULT 'text',
                q TEXT NOT NULL,
                a TEXT NOT NULL,
                options TEXT DEFAULT '[]',
                weight INTEGER DEFAULT 1,
                reward TEXT DEFAULT '[]',
                enabled INTEGER DEFAULT 1,
                created_at REAL DEFAULT 0)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS records(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player TEXT NOT NULL,
                question_id INTEGER,
                correct INTEGER DEFAULT 0,
                answer TEXT DEFAULT '',
                reward TEXT DEFAULT '',
                ts REAL DEFAULT 0)""")
            cur.execute("""CREATE TABLE IF NOT EXISTS ask_counts(
                day TEXT NOT NULL,
                player TEXT NOT NULL,
                cnt INTEGER DEFAULT 0,
                PRIMARY KEY(day, player))""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_records_player ON records(player)")
            if cur.execute('SELECT COUNT(*) FROM questions').fetchone()[0] == 0:
                for item in DEFAULT_QUESTIONS:
                    self._insert(cur, item)
            self._conn.commit()

    # ---------------- 题库 ----------------
    def _insert(self, cur, item: Dict[str, Any]) -> int:
        qtype = str(item.get('type') or 'text').lower()
        if qtype not in ('text', 'blank', 'choice'):
            qtype = 'text'
        answers = item.get('a', item.get('answers', ''))
        cur.execute(
            'INSERT INTO questions(type,q,a,options,weight,reward,enabled,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (qtype, str(item.get('q') or '').strip(),
             json.dumps(answers, ensure_ascii=False),
             json.dumps(item.get('options') or [], ensure_ascii=False),
             max(1, min(10000, int(item.get('weight') or 1))),
             json.dumps(item.get('reward') or [], ensure_ascii=False),
             1 if item.get('enabled', True) else 0, time.time()))
        return int(cur.lastrowid)

    def add(self, item: Dict[str, Any]) -> int:
        with LOCK:
            cur = self._conn.cursor()
            qid = self._insert(cur, item)
            self._conn.commit()
            return qid

    def delete(self, question_id: int) -> bool:
        with LOCK:
            cur = self._conn.cursor()
            cur.execute('DELETE FROM questions WHERE id=?', (int(question_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def count(self) -> int:
        with LOCK:
            return int(self._conn.execute('SELECT COUNT(*) FROM questions WHERE enabled=1').fetchone()[0])

    def list_questions(self) -> List[Dict[str, Any]]:
        with LOCK:
            rows = self._conn.execute('SELECT * FROM questions ORDER BY id').fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        def loads(text, default):
            try:
                return json.loads(text) if text else default
            except Exception:  # noqa: BLE001
                return default
        return {'id': row['id'], 'type': row['type'], 'q': row['q'],
                'a': loads(row['a'], ''), 'options': loads(row['options'], []),
                'weight': row['weight'], 'reward': loads(row['reward'], []),
                'enabled': bool(row['enabled'])}

    def pick(self) -> Optional[Dict[str, Any]]:
        questions = [q for q in self.list_questions() if q.get('enabled')]
        if not questions:
            return None
        weights = [max(1, int(q.get('weight') or 1)) for q in questions]
        return random.choices(questions, weights=weights, k=1)[0]

    def import_json(self, path: Path) -> int:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        items = data.get('questions') if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError('JSON 里需要 questions 数组')
        added = 0
        for item in items:
            if isinstance(item, str):
                if '=' not in item:
                    continue
                q, a = item.split('=', 1)
                item = {'q': q.strip(), 'a': [x.strip() for x in a.split('|') if x.strip()]}
            if not isinstance(item, dict) or not str(item.get('q') or '').strip():
                continue
            self.add(item)
            added += 1
        return added

    def export_json(self, path: Path) -> int:
        items = []
        for q in self.list_questions():
            items.append({'type': q['type'], 'q': q['q'], 'a': q['a'],
                          'options': q['options'], 'weight': q['weight'],
                          'reward': q['reward']})
        Path(path).write_text(json.dumps({'questions': items}, ensure_ascii=False, indent=2),
                              encoding='utf-8')
        return len(items)

    # ---------------- 记录 ----------------
    def record(self, player: str, question_id: Optional[int], correct: bool,
               answer: str = '', reward: str = '') -> None:
        with LOCK:
            self._conn.execute(
                'INSERT INTO records(player,question_id,correct,answer,reward,ts) VALUES(?,?,?,?,?,?)',
                (str(player or '?'), question_id, 1 if correct else 0, str(answer or '')[:120],
                 str(reward or '')[:200], time.time()))
            self._conn.commit()

    def stats(self, player: str) -> Dict[str, int]:
        with LOCK:
            row = self._conn.execute(
                'SELECT COUNT(*) AS total, SUM(correct) AS correct FROM records WHERE player=?',
                (str(player),)).fetchone()
        return {'total': int(row['total'] or 0), 'correct': int(row['correct'] or 0)}

    def ask_count(self, day: str, player: str) -> int:
        with LOCK:
            row = self._conn.execute(
                'SELECT cnt FROM ask_counts WHERE day=? AND player=?',
                (str(day), str(player))).fetchone()
        return int(row['cnt'] or 0) if row else 0

    def inc_ask(self, day: str, player: str) -> int:
        with LOCK:
            cur = self._conn.cursor()
            cur.execute('INSERT INTO ask_counts(day,player,cnt) VALUES(?,?,1) '
                        'ON CONFLICT(day,player) DO UPDATE SET cnt=cnt+1', (str(day), str(player)))
            self._conn.commit()
            return self.ask_count(day, player)

    def close(self) -> None:
        with LOCK:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


class QuizEngine:
    """出题/判分状态机（不依赖 Minecraft，便于单测）。"""

    def __init__(self, store: QuizStore, cfg_provider, send_chat, send_private,
                 send_actionbar=None, send_title=None, run_commands=None,
                 post_log=None, day_provider=None, now_provider=None):
        self.store = store
        self.cfg = cfg_provider
        self.send_chat = send_chat
        self.send_private = send_private
        self.send_actionbar = send_actionbar
        self.send_title = send_title
        self.run_commands = run_commands
        self.post_log = post_log or (lambda text: None)
        self.day_provider = day_provider or (lambda: time.strftime('%Y-%m-%d'))
        self.now = now_provider or time.time
        self.current: Optional[Dict[str, Any]] = None
        self.asked_at = 0.0
        self.next_at = 0.0
        self._correct_players = set()
        self._wrong_notified = set()

    # ---------------- 出题 ----------------
    def ask_public(self, by: str = '', manual: bool = False) -> str:
        cfg = self.cfg()
        if not cfg.enabled:
            return 'disabled'
        if self.current is not None and not manual:
            return 'busy'
        question = self.store.pick()
        if question is None:
            return 'empty'
        self.current = question
        self.asked_at = self.now()
        self._correct_players = set()
        self._wrong_notified = set()
        text = self.format_question(question)
        mode = cfg.broadcastMode
        if mode in ('actionbar', 'both') and self.send_actionbar:
            self.send_actionbar(text)
        if mode in ('title',) and self.send_title:
            self.send_title(text)
        if mode in ('chat', 'both') or not (self.send_actionbar or self.send_title):
            self.send_chat(text)
        if manual and by:
            self.post_log(f'{by} 发起了抢答：{question.get("q")}')
        else:
            self.post_log('出题：' + str(question.get('q')))
        return 'ok'

    def format_question(self, question: Dict[str, Any]) -> str:
        cfg = self.cfg()
        text = cfg.promptTemplate.replace('{question}', str(question.get('q') or ''))
        options = question.get('options') or []
        if question.get('type') == 'choice' and options:
            text += '  ' + '  '.join(str(o) for o in options)
        return text

    def ask_manual(self, player: str) -> str:
        cfg = self.cfg()
        day = self.day_provider()
        if cfg.askPerDay > 0:
            used = self.store.ask_count(day, player)
            if used >= cfg.askPerDay:
                self.send_private(player, cfg.askLimitMessage.replace('{limit}', str(cfg.askPerDay)))
                return 'limit'
        status = self.ask_public(by=player, manual=True)
        if status == 'disabled':
            self.send_private(player, '问答功能未开启。')
            return status
        if status == 'empty':
            self.send_private(player, '题库是空的，先让管理员导入题目吧。')
            return status
        if status == 'ok' and cfg.askPerDay > 0:
            self.store.inc_ask(day, player)
        return status

    def stats_text(self, player: str) -> str:
        cfg = self.cfg()
        st = self.store.stats(player)
        used = self.store.ask_count(self.day_provider(), player)
        limit = '不限' if cfg.askPerDay <= 0 else str(cfg.askPerDay)
        template = cfg.statsTemplate or '你已答对 {correct} 题 / 共 {total} 次'
        return (template.replace('{correct}', str(st['correct']))
                .replace('{total}', str(st['total']))
                .replace('{asked}', str(used)).replace('{limit}', limit)
                .replace('{player}', player))

    # ---------------- 判分 ----------------
    def handle_answer(self, player: str, text: str) -> bool:
        question = self.current
        if question is None:
            return False
        if player in self._correct_players:
            return False
        cfg = self.cfg()
        if not cfg.enabled:
            return False
        if self._matches(question, text):
            self._correct_players.add(player)
            answer_text = self.answer_text(question)
            reward_cmds = self._reward_commands(question)
            self.store.record(player, question.get('id'), True, text,
                              '; '.join(reward_cmds))
            try:
                if self.run_commands and reward_cmds:
                    self.run_commands(player, reward_cmds)
            except Exception as exc:  # noqa: BLE001
                self.post_log(f'发奖励失败：{exc}')
            self.send_chat(cfg.correctMessage.replace('{player}', player)
                           .replace('{answer}', answer_text))
            self.current = None
            self.next_at = self.now() + max(5, int(cfg.intervalSeconds))
            return True
        # 答错
        self.store.record(player, question.get('id'), False, text)
        if cfg.wrongPublicOnce and player in self._wrong_notified:
            self.send_private(player, cfg.wrongMessage.replace('{player}', player))
        else:
            self._wrong_notified.add(player)
            self.send_chat(cfg.wrongMessage.replace('{player}', player))
        return True

    def _matches(self, question: Dict[str, Any], text: str) -> bool:
        cfg = self.cfg()
        qtype = question.get('type') or 'text'
        answers = question.get('a')
        if qtype == 'choice':
            letter = _norm(text).upper().strip('.')
            correct = str(answers or '').strip().upper()
            if letter and letter[0] == correct[:1]:
                return True
            options = question.get('options') or []
            for opt in options:
                if _similar(opt, text) >= max(0.6, cfg.fuzzyThreshold):
                    return True
            return False
        if qtype == 'blank':
            blanks = answers if isinstance(answers, list) else [answers]
            parts = [p for p in str(text or '').replace('，', ' ').replace(',', ' ')
                     .replace('、', ' ').split() if p]
            if len(parts) != len(blanks):
                return False
            for part, alts in zip(parts, blanks):
                if not self._match_one(alts, part):
                    return False
            return True
        alts = answers if isinstance(answers, list) else [answers]
        return self._match_one(alts, text)

    def _match_one(self, alternatives, text: str) -> bool:
        cfg = self.cfg()
        alts = alternatives if isinstance(alternatives, list) else [alternatives]
        for alt in alts:
            if _norm(alt) == _norm(text):
                return True
            if cfg.fuzzyEnabled and _similar(str(alt), text) >= cfg.fuzzyThreshold:
                return True
        return False

    def answer_text(self, question: Dict[str, Any]) -> str:
        answers = question.get('a')
        if question.get('type') == 'blank' and isinstance(answers, list):
            return ' / '.join(str(x[0] if isinstance(x, list) and x else x) for x in answers)
        if isinstance(answers, list):
            return str(answers[0] if answers else '')
        return str(answers or '')

    def _reward_commands(self, question: Dict[str, Any]) -> List[str]:
        own = [str(x).strip() for x in (question.get('reward') or []) if str(x).strip()]
        return own or list(self.cfg().rewards or [])

    # ---------------- 定时 ----------------
    def tick(self) -> None:
        cfg = self.cfg()
        if not cfg.enabled:
            return
        now = self.now()
        if self.current is not None:
            if cfg.timeoutSeconds > 0 and now - self.asked_at >= cfg.timeoutSeconds:
                q = self.current
                self.current = None
                self.next_at = now + max(5, int(cfg.intervalSeconds))
                self.send_chat(cfg.timeoutMessage
                               .replace('{answer}', self.answer_text(q)))
                self.post_log('超时，答案：' + self.answer_text(q))
            return
        if self.next_at <= 0:
            self.next_at = now + max(5, int(cfg.intervalSeconds))
        if now >= self.next_at:
            self.ask_public()
