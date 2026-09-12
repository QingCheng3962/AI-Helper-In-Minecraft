"""AI Player chat responder (port of aafm's com.mcai.chat.ChatResponder).

Auto-replies to in-game chat using an LLM. Supports trigger replies, auto
reply, scheduled replies, context window, message restriction, blocked
players and optional image generation.
"""
from __future__ import annotations

import random
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from . import llm
from .config import AiPlayerConfig
from .roster import RosterError, split_aliases

MAX_LOG_LINES = 200

_LOG_CALLBACK = Callable[[str], None]

# ---------------------------------------------------------------------------
# Identity-lookup ("xxx是谁") helpers
# ---------------------------------------------------------------------------
# Words that don't name a real person; asking "他是谁" etc. is left to the LLM.
_STOP_SUBJECTS = {
    '这', '那', '他', '她', '它', '你', '我', '谁', '大家', '你们', '我们',
    '他们', '她们', '它们', '这个', '那个', '自己',
}

# Leading chatter that may be glued onto the subject, e.g. "请问小明是谁".
_NOISE_PREFIXES = (
    '请问一下', '请问', '你知道', '有人知道', '有谁知道', '谁知道', '帮我问一下',
    '帮我问', '帮我查一下', '帮我查', '查一下', '问一下', '问问', '知不知道',
)

# Characters allowed right after "是谁" so the question still counts as a bare
# lookup ("小明是谁呀？") rather than a longer sentence ("这坑是谁挖的").
_SUFFIX_PUNCT = ' \u3000吗呢啊呀哦喔吧嘛么？?！!。.～~…'

# Verbs used to point out who is who: "X就是Y / X其实是Y / X是Y".
_IDENTITY_VERB_RE = re.compile(r'就是|其实是|实际上是|是')

_ROSTER_LEARN_ACK = '好，我记住了。'

# Trailing particles allowed after the Y name in an identity statement, so
# "老王" / "老王啊" both count but "老王挖的" does not.
_Y_TAIL_OK = ' \u3000呀啊嘛呢哦喔吧呗啦咯哦'

# System prompt used to study a player's personality from recent chat lines.
_PERSONALITY_SYSTEM = (
    '你是 Minecraft 服务器里擅长观察的 AI。下面会给出一位玩家的最近聊天记录。'
    '请阅读并提炼 TA 的性格/人设特点，只输出一句不超过 60 字的中文概括，'
    '例如"爱开玩笑，自称服里的建筑大师。"。不要加称呼、不要解释、不要引号，'
    '直接给出结论。'
)

# Defaults for the automatic personality research.
_PERSONALITY_DEFAULT_INTERVAL_MS = 180 * 1000
_PERSONALITY_DEFAULT_MIN_MSGS = 6
_PERSONALITY_LOG_CAP = 40  # max remembered chat lines per player
_PERSONALITY_NOTE_MAX = 120  # max characters kept per note

# Prompt used to analyse which aliases / nicknames a player goes by.
_ALIAS_SYSTEM_TMPL = (
    '你是 Minecraft 服务器里擅长观察的 AI。下面给出玩家 {player} 的最近聊天记录。'
    '请找出大家如何称呼 TA、或 TA 自称的别名/昵称/人物名（游戏ID本身不算）。'
    '只输出用顿号或逗号分隔的别名列表，最多 3 个；没发现就只输出“无”。不要解释。'
)
_ALIAS_MAX_RESULTS = 3
_ALIAS_NOTE_MAX = 60


def _now_ms() -> int:
    return int(time.time() * 1000)


def _norm(t) -> str:
    """Collapse whitespace in a roster name."""
    return re.sub(r'\s+', ' ', str(t or '')).strip()


def _strip_noise_prefix(t: str) -> str:
    """Repeatedly cut glued-on leading chatter such as ``请问小明`` -> ``小明``."""
    while True:
        changed = False
        for ph in _NOISE_PREFIXES:
            if t.casefold().startswith(ph.casefold()):
                t = t[len(ph):]
                changed = True
        if not changed:
            break
    return t


def _clean_roster_prefix(prefix: str) -> str:
    """Prefix text (before ``是谁``) with chatter/separators trimmed."""
    t = _strip_noise_prefix(prefix)
    t = re.sub(r'^[\s@:：,，。.、+~!]+', '', t)
    return t


def _roster_subject(prefix: str) -> Optional[str]:
    """Best-guess subject name (last name-like token) in ``prefix``.

    Returns ``None`` when the subject is empty or a stopword ("他是谁"),
    letting the caller fall back to the normal AI flow.
    """
    tokens = re.split(r'[\s，。？！!?,.;；:：、@~]+', prefix)
    for tok in reversed(tokens):
        t = tok.strip(':：.。·')
        if not t:
            continue
        if t.casefold() in _STOP_SUBJECTS:
            return None
        return t
    return None


def _split_top_level_commas(text: str) -> List[str]:
    """Split a trigger string on ',' that are not inside (), [] or {}.

    Lets users write ``[Player],@helper,!ai`` while keeping commas that belong
    to a single regex (e.g. inside a character class ``[，, ]``) intact.
    """
    parts: List[str] = []
    buf = ''
    depth = 0
    escaped = False
    for ch in str(text or ''):
        if escaped:
            buf += ch
            escaped = False
            continue
        if ch == '\\':
            buf += ch
            escaped = True
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth = max(0, depth - 1)
        if ch == ',' and depth == 0:
            parts.append(buf)
            buf = ''
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


_BRACKET_LITERAL = re.compile(r'\[[^\[\]]*\]')


def _compile_trigger(part: str) -> 're.Pattern':
    """Compile one trigger token.

    A bare ``[....]`` token (e.g. ``[DiaoHelper]``) is treated as a LITERAL
    mention and auto-escaped + matched case-insensitively, so a plain "？"
    message never matches by accident. Everything else is a real regex.
    """
    if _BRACKET_LITERAL.fullmatch(part):
        return re.compile(re.escape(part), re.IGNORECASE)
    return re.compile(part)


class AiPlayer:
    def __init__(self,
                 config: AiPlayerConfig,
                 on_send_chat: Callable[[str], None],
                 log: _LOG_CALLBACK = print,
                 error: _LOG_CALLBACK = None,
                 roster: Optional[Any] = None):
        self.config = config
        self.on_send_chat = on_send_chat
        self._log_cb = log
        self._error_cb = error if error is not None else log
        self._roster = roster

        self._trigger_provider = None
        self._schedule_provider = None
        self._image_provider = None
        self._bot_username: Optional[str] = None

        self._trigger_reply_in_progress = False
        self._schedule_reply_in_progress = False
        self._scheduled_request_queued = False
        self._image_generating = False
        self._stopped = False

        self._last_player_message: Optional[str] = None
        self._last_player_raw: Optional[str] = None
        self._last_player_message_time = 0
        self._last_handled_user_message_time = 0
        self._next_scheduled_reply_time = 0
        self._next_trigger_reply_time = 0
        self._next_image_cooldown_time = 0

        self._recent_ai_messages: Dict[str, int] = {}
        self._recent_player_messages: Dict[str, int] = {}
        self._context: Deque[Dict[str, str]] = deque()
        self._activity_log: Deque[str] = deque()

        # Personality research state (chat log capture + auto/manual research)
        self._chat_logs: Dict[str, List[Dict[str, Any]]] = {}
        self._last_research_ms: Dict[str, int] = {}
        self._next_auto_research_ms = _now_ms() + _PERSONALITY_DEFAULT_INTERVAL_MS
        self._research_running = False
        # Alias analysis state (independent of personality research)
        self._last_alias_ms: Dict[str, int] = {}
        self._next_auto_alias_ms = _now_ms() + _PERSONALITY_DEFAULT_INTERVAL_MS
        self._alias_running = False

        self._trigger_patterns: List[re.Pattern] = []
        self._image_trigger_pattern: Optional[re.Pattern] = None
        self._blocked_patterns: List[re.Pattern] = []

        self._chunk_delay_ms = 600  # spacing between reply chunks

        self._build_providers()
        self._compile_patterns()
        self._next_scheduled_reply_time = _now_ms() + self.config.scheduleIntervalSeconds * 1000
        self._next_trigger_reply_time = _now_ms()
        self._next_image_cooldown_time = _now_ms()

    # ------------------------------------------------------------------
    # Lifecycle / reload
    # ------------------------------------------------------------------
    def _build_providers(self) -> None:
        cfg = self.config
        try:
            self._trigger_provider = llm.create_provider(cfg, cfg.apiKey)
        except Exception as e:  # noqa: BLE001
            self._error('创建聊天 AI 提供方失败: ' + str(e))
            self._trigger_provider = None
        try:
            self._schedule_provider = llm.create_provider(cfg, cfg.apiKey)
        except Exception as e:  # noqa: BLE001
            self._error('创建定时 AI 提供方失败: ' + str(e))
            self._schedule_provider = None
        if cfg.imageGenerationEnabled:
            self._image_provider = llm.create_image_provider(cfg)
        else:
            self._image_provider = None

    def _compile_patterns(self) -> None:
        cfg = self.config
        # Trigger: several regexes separated by "," (top-level only, so commas
        # inside a single pattern like a character class stay untouched).
        # A message matches when ANY of them hits. Empty => never trigger.
        # A bare "[Name]" token is treated as a literal mention (auto-escaped,
        # case-insensitive) so it won't act as a broad character class.
        trigger_patterns = []
        for part in _split_top_level_commas(cfg.triggerRegex):
            try:
                trigger_patterns.append(_compile_trigger(part))
            except re.error as e:
                self._error(f"无效的触发正则: '{part}' -> {e}")
        self._trigger_patterns = trigger_patterns
        try:
            self._image_trigger_pattern = re.compile(cfg.imageTriggerRegex)
        except re.error as e:
            self._image_trigger_pattern = None
            self._error(f"无效的文生图触发正则: '{cfg.imageTriggerRegex}' -> {e}")
        patterns = []
        for pat in cfg.blockedRegexPatterns or []:
            try:
                patterns.append(re.compile(pat))
            except re.error as e:
                self._error(f"无效的拦截正则: '{pat}' -> {e}")
        self._blocked_patterns = patterns

    def reload(self, new_config: AiPlayerConfig) -> None:
        self.config = new_config
        self._build_providers()
        self._compile_patterns()
        self._context.clear()
        self._last_player_message = None
        self._last_player_raw = None
        self._last_player_message_time = 0
        self._last_handled_user_message_time = 0
        self._next_scheduled_reply_time = _now_ms() + new_config.scheduleIntervalSeconds * 1000
        self._next_trigger_reply_time = _now_ms()
        self._next_image_cooldown_time = _now_ms()
        self._recent_ai_messages.clear()
        self._recent_player_messages.clear()
        self._trigger_reply_in_progress = False
        self._schedule_reply_in_progress = False
        self._image_generating = False
        self._stopped = False

    def stop_now(self) -> None:
        self._stopped = True
        self._trigger_reply_in_progress = False
        self._schedule_reply_in_progress = False
        self._image_generating = False
        self._scheduled_request_queued = False

    def clear_context(self) -> None:
        self._context.clear()

    def get_activity_log(self) -> List[str]:
        return list(self._activity_log)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        self._log_cb(msg)

    def _error(self, msg: str) -> None:
        self._error_cb(msg)

    def _log_activity(self, line: str) -> None:
        self._activity_log.append(line)
        while len(self._activity_log) > MAX_LOG_LINES:
            self._activity_log.popleft()

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------
    def on_player_message(self, player_name: Optional[str], text: str,
                          raw: Optional[str] = None) -> None:
        cfg = self.config
        if text is None or not text.strip():
            return

        original = text
        if player_name is None:
            player_name = self._extract_player_name(original)

        if self._is_self(player_name):
            if cfg.debugLog:
                self._log(f"忽略自己的消息: '{player_name}'")
            return

        if self._is_player_blocked(player_name):
            if cfg.debugLog:
                self._log(f"忽略被屏蔽玩家的消息: '{player_name}'")
            return

        if player_name:
            text = self._strip_player_prefix(text, player_name)

        # Trigger detection should look at the whole chat line as seen in the
        # game (channel / prefix / title included), falling back to the content.
        raw_text = raw if isinstance(raw, str) and raw.strip() else None
        match_text = raw_text or text

        dedupe_key = (player_name or 'null') + '|' + text
        now = _now_ms()
        last_time = self._recent_player_messages.get(dedupe_key)
        if last_time is not None and now - last_time < 2000:
            if cfg.debugLog:
                self._log(f"忽略重复消息 (player='{player_name}', text='{text}')")
            return
        self._recent_player_messages[dedupe_key] = now

        if cfg.debugLog and (raw_text and raw_text != text):
            self._log(f"触发匹配用原文: '{raw_text}'  内容: '{text}'")

        self._log_activity((player_name or '?') + ' > ' + text)
        self._capture_chat(player_name, text, now)

        # Image generation trigger (uses content so the prompt stays clean)
        if cfg.enabled and cfg.imageGenerationEnabled and self._matches_image_trigger(text):
            if now >= self._next_image_cooldown_time:
                self._next_image_cooldown_time = now + cfg.imageCooldownSeconds * 1000
                prompt = self._extract_image_prompt(text)
                if prompt is not None and prompt.strip():
                    self._last_handled_user_message_time = now
                    self._last_player_message = text
                    self._last_player_raw = match_text
                    self._last_player_message_time = now
                    self._log('文生图已触发，提示词: ' + prompt)
                    self.generate_image(prompt)
            else:
                remaining = self._next_image_cooldown_time - now
                self._log(f'文生图冷却中 (剩余 {remaining} ms)。')
                self._last_handled_user_message_time = now
            return

        self._last_player_message = text
        self._last_player_raw = match_text
        self._last_player_message_time = now

        if not cfg.enabled:
            return

        if cfg.contextEnabled and cfg.contextLength > 0:
            self._add_to_context({'role': 'user', 'content': text})

        matches = cfg.triggerEnabled and self._matches_trigger(match_text)
        auto_reply = cfg.autoReplyEnabled
        should_reply = auto_reply or matches
        cooldown_passed = self._next_trigger_reply_time <= now

        if cfg.debugLog:
            self._log(f"消息='{text}', autoReply={auto_reply}, regexMatch={matches}, "
                      f"cooldownPassed={cooldown_passed}")

        if should_reply and cooldown_passed:
            self._next_trigger_reply_time = now + cfg.triggerCooldownSeconds * 1000
            self._last_handled_user_message_time = self._last_player_message_time
            self._log(f'回复已启动，冷却 {cfg.triggerCooldownSeconds} 秒。')
            if self._maybe_alias_request(text):
                return
            if self._maybe_roster_research(text):
                return
            if self._maybe_roster_learn(text):
                return
            if self._maybe_roster_reply(text):
                return
            self._start_ai_reply(text, self._last_player_message_time, True)
        elif should_reply:
            remaining = self._next_trigger_reply_time - now
            self._log(f'命中回复但冷却中 (剩余 {remaining} ms)。')
            self._last_handled_user_message_time = now

    def tick(self) -> None:
        """Called periodically (>=1s): auto alias/personality research + scheduled replies."""
        self._maybe_auto_alias()
        self._maybe_auto_research()
        cfg = self.config
        if not cfg.enabled or not cfg.scheduleEnabled:
            return
        if (self._schedule_reply_in_progress or self._scheduled_request_queued
                or self._image_generating):
            return

        self._cleanup_recent_messages()
        now = _now_ms()
        if now < self._next_scheduled_reply_time:
            return

        msg = self._last_player_message
        if msg is None or not msg.strip():
            return
        if self._last_player_message_time <= self._last_handled_user_message_time:
            return
        if cfg.triggerEnabled and self._last_player_message_time == self._last_handled_user_message_time:
            return
        # Strict trigger gating: with trigger mode on, only messages that match the
        # trigger regex may produce a (scheduled) reply. Others stay silent.
        # Use the full raw line (prefix included) when available.
        gate_text = self._last_player_raw or msg
        if cfg.triggerEnabled and not self._matches_trigger(gate_text):
            self._last_handled_user_message_time = self._last_player_message_time
            return

        if not self._scheduled_request_queued:
            self._scheduled_request_queued = True
            try:
                self._last_handled_user_message_time = self._last_player_message_time
                latest = self._last_player_message
                if latest and latest.strip():
                    self._log('自动回复已触发。')
                    self._start_ai_reply(latest, self._last_player_message_time, False)
            finally:
                self._scheduled_request_queued = False

    # ------------------------------------------------------------------
    # AI reply
    # ------------------------------------------------------------------
    def _start_ai_reply(self, user_message: str, user_message_time: int, is_trigger: bool) -> None:
        cfg = self.config
        flag_key = 'trigger' if is_trigger else 'schedule'
        flag = self._trigger_reply_in_progress if is_trigger else self._schedule_reply_in_progress
        if flag:
            self._log(('触发' if is_trigger else '定时') + '回复已在进行中，跳过。')
            return
        if is_trigger:
            self._trigger_reply_in_progress = True
        else:
            self._schedule_reply_in_progress = True
        try:
            provider = self._trigger_provider if is_trigger else self._schedule_provider
            if provider is None:
                self._error(('触发' if is_trigger else '定时') + 'AI 提供方为 null，请检查 AI API 设置。')
                return
            if not cfg.enabled:
                return
            if user_message is None or not user_message.strip():
                return
            messages = self._build_context(user_message)
            self._log(f'请求 AI (provider={cfg.provider}, model={cfg.model}, '
                      f'type={("trigger" if is_trigger else "schedule")})...')
            reply = provider.send(cfg.systemPrompt, messages)
            if reply is None or not reply.strip():
                return
            self._handle_ai_reply(reply)
        except Exception as e:  # noqa: BLE001
            self._error('AI 请求失败: ' + str(e))
        finally:
            if is_trigger:
                self._trigger_reply_in_progress = False
            else:
                self._schedule_reply_in_progress = False

    def _handle_ai_reply(self, reply: str) -> None:
        cfg = self.config
        if self._stopped or not cfg.enabled:
            return
        chunks = self._split_reply(reply)
        if not chunks:
            return
        sent = 0
        for i, chunk in enumerate(chunks):
            if self._stopped or not cfg.enabled:
                break
            if self._is_blocked(chunk):
                self._log('跳过被拦截的 AI 消息: ' + chunk)
                continue
            self.on_send_chat(chunk)
            self._recent_ai_messages[chunk] = _now_ms()
            self._log_activity('AI > ' + chunk)
            sent += 1
            if i < len(chunks) - 1 and self._chunk_delay_ms > 0:
                time.sleep(self._chunk_delay_ms / 1000.0)
        if sent > 0 and cfg.contextEnabled and cfg.contextLength > 0:
            self._add_to_context({'role': 'assistant', 'content': reply})
        self._next_scheduled_reply_time = _now_ms() + cfg.scheduleIntervalSeconds * 1000
        self._log(f'已发送 {sent} 条 AI 消息。')

    # ------------------------------------------------------------------
    # Local identity lookup ("xxx是谁")
    # ------------------------------------------------------------------
    def _strip_bot_mention(self, text: str) -> str:
        """Remove a leading "@Bot " / "Bot " mention before parsing."""
        t = str(text or '').strip(' \u3000')
        bot = (self._bot_username or '').strip()
        if bot:
            m = re.match(r'^(?:@\s*)?' + re.escape(bot) + r'\s*(?:[,，:：]\s*)?',
                         t, re.IGNORECASE)
            if m:
                t = t[m.end():]
        return t.strip(' \u3000')

    def _match_y(self, text: str, roster) -> Optional[Dict[str, Any]]:
        """Return the roster name ``text`` starts with (name only + particles).

        Used for the object side of "X是Y": the statement only counts as an
        identity reveal when ``Y`` is basically a bare roster name, e.g.
        "老王" or "老王啊" — never "老王挖的".
        """
        low = str(text or '').strip().casefold()
        for e in sorted(roster._entries if hasattr(roster, '_entries') else [],
                        key=lambda x: len(_norm(x['name'])), reverse=True):
            nm = _norm(e['name']).casefold()
            if not nm or not low.startswith(nm):
                continue
            rest = low[len(nm):].strip(_Y_TAIL_OK)
            if not rest:
                return e
        return None

    def _maybe_roster_learn(self, text: str) -> bool:
        """Learn "X是Y / X就是Y / X其实是Y" statements from triggered chat.

        When a server player tells the bot who is who (X or Y already in the
        table), the whole statement is written into that player's authoritative
        玩家描述 cell of the source workbook; the fixed 描述1..N columns stay
        in the file but stop being used for that player afterwards.
        """
        roster = self._roster
        if roster is None or not getattr(roster, 'enabled', False):
            return False
        if not text or '是谁' in text:  # a question, not a statement
            return False
        s = self._strip_bot_mention(text)
        m = _IDENTITY_VERB_RE.search(s)
        if not m:
            return False
        # Avoid learning from questions ("X是Y吗?") or guesses
        # ("我猜X是Y", "X好像是Y", "X是不是Y").
        if '吗' in s:
            return False
        if '是不是' in s:
            return False
        if s.rstrip(' \u3000').endswith(('？', '?', '？')):
            return False
        if re.search(r'(?:我猜|我觉得|我认为|我觉着|可能|应该|估计|大概|难道|好像是)', s):
            return False
        left = s[:m.start()].strip(' \u3000:：,，.。')
        right = s[m.end():].strip(' \u3000,，.。;；!！?？')
        if not left or not right:
            return False
        if right.startswith(('不', '不是')):
            return False

        cleaned_left = _clean_roster_prefix(left)
        x_entry = roster.match_at_end(left)
        if x_entry is None and cleaned_left:
            x_entry = roster.find(cleaned_left)
        if x_entry is None:
            subject = _roster_subject(cleaned_left)
            if subject:
                x_entry = roster.find(subject)
        else:
            subject = None

        y_entry = None
        if x_entry is None:
            # The subject isn't known yet — only learn when the object Y is a
            # known roster name (e.g. "某某就是老王").
            y_entry = self._match_y(right, roster)
        if x_entry is None and y_entry is None:
            return False

        if x_entry is not None:
            store_name = str(x_entry.get('name', '')) or cleaned_left
            source = str(x_entry.get('source') or '')
        else:
            store_name = (subject or cleaned_left or left).strip(' @:：,，.。')
            source = str(y_entry.get('source') or '')
        if not store_name or not source:
            return False

        statement = s.strip().rstrip(' \u3000。.!！?？～~…，,；;')
        try:
            roster.learn_identity(source, store_name, statement)
        except RosterError as e:
            self._error('记录身份失败: ' + str(e))
            return False
        self._log(f'玩家资料库学习: {store_name} => {statement}')
        self._send_direct_reply(_ROSTER_LEARN_ACK)
        return True

    def _maybe_roster_reply(self, text: str) -> bool:
        """Answer ``xxx是谁`` straight from the imported spreadsheet.

        Runs only when a normal trigger reply would already fire. Returns True
        when the question was fully handled (either found or answered
        "不认识"), so the LLM isn't called twice.
        """
        roster = self._roster
        if roster is None or not getattr(roster, 'enabled', False):
            return False
        if not text or '是谁' not in text:
            return False
        idx = text.index('是谁')
        prefix = text[:idx].strip(' \u3000:：,，.。')
        cleaned = _clean_roster_prefix(prefix)
        subject = _roster_subject(cleaned)
        # The name/alias may be glued to chatter ("请问小明天") after a mention,
        # so also clean the trailing token on its own.
        raw_tail = _roster_subject(prefix)
        tail_clean = None
        if raw_tail:
            tail_clean = _strip_noise_prefix(raw_tail).strip(' :：@')

        entry = roster.match_at_end(prefix)

        candidates: List[str] = []
        for c in (subject, cleaned, tail_clean):
            if not c:
                continue
            c = c.strip(' :：@')
            if c and c not in candidates:
                candidates.append(c)
        # exact name / alias match for each candidate
        if entry is None:
            for c in candidates:
                entry = roster.find(c) or roster.find_alias(c)
                if entry is not None:
                    break
        # tail match of an alias that sits at the end of the prefix
        if entry is None:
            entry = roster.match_alias_at_end(prefix)

        if entry is not None:
            name = str(entry.get('name', ''))
            # Authoritative 玩家描述 wins; the fixed 描述1..N columns only
            # apply (at random) while it is still empty.
            main = str(entry.get('main') or '').strip()
            fixed = [d for d in (entry.get('fixed') or []) if str(d).strip()]
            if main:
                reply = main
            elif fixed:
                reply = str(random.choice(fixed))
            else:
                reply = f'我没有找到关于 {name} 的描述。'
            self._send_direct_reply(reply)
            self._log(f'玩家资料库命中: {name}')
            return True

        # Not in the table. Only answer "不认识" for a short bare question such
        # as "小明是谁呀" — longer sentences ("这坑是谁挖的") fall through to
        # the normal AI flow so we don't reply nonsense.
        if subject is None:
            return False
        tail = text[idx + 2:]
        if tail.strip(_SUFFIX_PUNCT):
            return False
        self._send_direct_reply(f'我不认识{subject}。')
        self._log(f'玩家资料库未命中，回复不认识: {subject}')
        return True

    def _send_direct_reply(self, reply: str) -> None:
        """Send a fixed answer, split into chat-sized chunks like AI replies."""
        cfg = self.config
        if self._stopped or not cfg.enabled:
            return
        chunks = self._split_reply(reply or '')
        if not chunks:
            return
        for i, chunk in enumerate(chunks):
            if self._stopped or not cfg.enabled:
                break
            if self._is_blocked(chunk):
                self._log('跳过被拦截的回复: ' + chunk)
                continue
            self.on_send_chat(chunk)
            self._log_activity('AI > ' + chunk)
            if i < len(chunks) - 1 and self._chunk_delay_ms > 0:
                time.sleep(self._chunk_delay_ms / 1000.0)
        if cfg.contextEnabled and cfg.contextLength > 0:
            self._add_to_context({'role': 'assistant', 'content': reply})
        self._log(f'已发送 {len(chunks)} 条直接回复。')

    # ------------------------------------------------------------------
    # Personality research ("AI阅读聊天记录研究性格")
    # ------------------------------------------------------------------
    def _capture_chat(self, player_name: Optional[str], text: str, now: int) -> None:
        """Remember a chat line per player so personality/alias research can read it."""
        if not player_name:
            return
        key = _norm(player_name).casefold()
        logs = self._chat_logs.setdefault(key, [])
        logs.append({'name': str(player_name), 'text': str(text or ''), 'ms': now})
        if len(logs) > _PERSONALITY_LOG_CAP:
            del logs[:len(logs) - _PERSONALITY_LOG_CAP]

    def _research_settings(self):
        roster = self._roster
        enabled = bool(roster and getattr(roster, 'personality_enabled', False))
        interval_ms = int(getattr(roster, 'research_interval_ms', 0) or
                          _PERSONALITY_DEFAULT_INTERVAL_MS)
        min_msgs = int(getattr(roster, 'research_min_messages', 0) or
                       _PERSONALITY_DEFAULT_MIN_MSGS)
        return enabled, interval_ms, min_msgs

    def _pick_auto_target(self, roster, since_map: Dict[str, int],
                          min_msgs: int) -> Optional[str]:
        """Pick the most-talkative player (known OR brand-new) to research.

        New players not yet in any imported sheet are chosen too, as long as a
        sheet is loaded to append their row into (the app then saves a new row
        for them automatically).
        """
        best = None
        best_cnt = 0
        can_append = bool(roster.file_paths())
        for key, logs in self._chat_logs.items():
            if not logs:
                continue
            known = roster.find(key) is not None
            if not known and not can_append:
                continue
            since = since_map.get(key, 0)
            cnt = sum(1 for m in logs if m.get('ms', 0) > since)
            if cnt < min_msgs or cnt <= best_cnt:
                continue
            display = None
            for m in reversed(logs):
                if m.get('name'):
                    display = str(m['name'])
                    break
            if display is None:
                e = roster.find(key)
                display = str(e.get('name') or key) if e is not None else key
            best, best_cnt = display, cnt
        return best

    def _maybe_auto_research(self) -> None:
        """Periodically research the most-talkative player (auto mode)."""
        roster = self._roster
        enabled, interval_ms, min_msgs = self._research_settings()
        if roster is None or not enabled or not self.config.enabled or self._stopped:
            return
        now = _now_ms()
        if now < self._next_auto_research_ms or self._research_running:
            return
        self._next_auto_research_ms = now + interval_ms

        best = self._pick_auto_target(roster, self._last_research_ms, min_msgs)
        if best is not None:
            self._start_research(best, manual=False)

    def _maybe_roster_research(self, text: str) -> bool:
        """Handle an explicit request like ``研究小明`` / ``研究一下小明``."""
        roster = self._roster
        enabled, _, _ = self._research_settings()
        if roster is None or not enabled:
            return False
        if not text or '研究' not in text or '是谁' in text:
            return False
        s = self._strip_bot_mention(text)
        if '研究' not in s:
            return False
        target = self._extract_research_target(s, roster)
        if not target:
            return False
        self._start_research(target, manual=True)
        return True

    def _extract_research_target(self, s: str, roster) -> Optional[str]:
        """Find which player "研究..." refers to (roster name or a new name)."""
        after = s[s.find('研究') + 2:]
        best: Optional[str] = None
        best_pos: Optional[int] = None
        for e in roster._entries:
            nm = e.get('name') or ''
            if not nm:
                continue
            p = after.find(nm)
            if p >= 0 and (best_pos is None or p < best_pos):
                best, best_pos = nm, p
        if best is not None:
            return best
        m = re.search(r'研究(?:一下|一个|看看|下|关于)?'
                      r'([^\s，。！？!?,;；:：的了吗呢啊呀吧和跟与、]+)', s)
        if m:
            cand = _norm(m.group(1))
            bot = str(self._bot_username or '').strip().lower()
            if cand and cand.lower() != bot:
                return cand
        return None

    def _start_research(self, target: str, manual: bool) -> None:
        if not target:
            return
        if self._research_running:
            if manual:
                self._send_direct_reply('上一个研究还在进行，请稍后再试。')
            return
        self._research_running = True
        threading.Thread(target=self._research_job,
                         args=(target, manual), daemon=True).start()

    def _research_job(self, target: str, manual: bool) -> None:
        try:
            res = self._run_research(target)
        except Exception as e:  # noqa: BLE001
            res = {'ok': False, 'msg': '研究出错: ' + str(e)}
        finally:
            self._research_running = False
        self._last_research_ms[_norm(target).casefold()] = _now_ms()

        if manual:
            if res.get('ok'):
                self._send_direct_reply(
                    f"研究{res.get('name') or target}：{res.get('note', '')}")
            else:
                self._send_direct_reply(res.get('msg') or '研究失败。')
        elif res.get('ok'):
            self._log(f"AI性格研究完成：{res.get('name')} -> {res.get('note', '')}")
        else:
            self._log('AI性格研究未完成：' + str(res.get('msg', '')))

    def _run_research(self, target: str) -> Dict[str, Any]:
        """Ask the LLM to read one player's recent chat and roll a personality
        note into AI记录1..5 of their row."""
        roster = self._roster
        if roster is None:
            return {'ok': False, 'msg': '资料库未配置。'}
        # Use a dedicated LLM client: research runs on its own thread while the
        # shared trigger/schedule clients are used from the worker thread.
        provider = None
        try:
            provider = llm.create_provider(self.config, None)
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'msg': f'AI API 未配置: {e}'}
        try:
            return self._run_research_with(provider, roster, target)
        finally:
            try:
                provider.close()
            except Exception:
                pass

    def _run_research_with(self, provider, roster, target: str) -> Dict[str, Any]:
        key = _norm(target).casefold()
        logs = self._chat_logs.get(key) or []
        entry = roster.find(target)
        if entry is not None:
            display = str(entry.get('name') or '') or target
            source = str(entry.get('source') or '')
        else:
            display = _norm(target)
            paths = roster.file_paths()
            source = paths[0] if paths else ''
        if not source:
            return {'ok': False, 'msg': f'{display} 没有对应的资料表，请先导入。'}
        if not logs:
            return {'ok': False, 'msg': f'暂时没有 {display} 的聊天记录。'}

        lines = [m.get('text', '') for m in logs if m.get('text')]
        transcript = '\n'.join('> ' + ln for ln in lines[-12:])
        try:
            out = provider.send(_PERSONALITY_SYSTEM,
                                [{'role': 'user', 'content': transcript}])
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'msg': f'AI 研究失败: {e}'}
        note = str(out or '').strip().strip('"\'“”`')
        note = re.sub(r'\s+', ' ', note)[:_PERSONALITY_NOTE_MAX].strip()
        if not note:
            return {'ok': False, 'msg': 'AI 未返回有效结论。'}
        try:
            roster.add_ai_record(source, display, note)
        except RosterError as e:
            return {'ok': False, 'msg': str(e)}
        return {'ok': True, 'name': display, 'note': note}

    # ------------------------------------------------------------------
    # Alias analysis ("AI阅读聊天分析玩家别名", independent of personality)
    # ------------------------------------------------------------------
    def _alias_settings(self):
        roster = self._roster
        enabled = bool(roster and getattr(roster, 'alias_enabled', False))
        interval_ms = int(getattr(roster, 'research_interval_ms', 0) or
                          _PERSONALITY_DEFAULT_INTERVAL_MS)
        min_msgs = int(getattr(roster, 'research_min_messages', 0) or
                       _PERSONALITY_DEFAULT_MIN_MSGS)
        return enabled, interval_ms, min_msgs

    def _maybe_auto_alias(self) -> None:
        """Periodically analyse aliases of the most-talkative player."""
        roster = self._roster
        enabled, interval_ms, min_msgs = self._alias_settings()
        if roster is None or not enabled or not self.config.enabled or self._stopped:
            return
        now = _now_ms()
        if now < self._next_auto_alias_ms or self._alias_running:
            return
        self._next_auto_alias_ms = now + interval_ms

        best = self._pick_auto_target(roster, self._last_alias_ms, min_msgs)
        if best is not None:
            self._start_alias(best, manual=False)

    def _maybe_alias_request(self, text: str) -> bool:
        """Handle requests mentioning 别名, e.g. ``小明有什么别名``."""
        roster = self._roster
        enabled, _, _ = self._alias_settings()
        if roster is None or not enabled:
            return False
        if not text or '别名' not in text:
            return False
        s = self._strip_bot_mention(text)
        target = self._extract_alias_target(s, roster)
        if not target:
            return False
        self._start_alias(target, manual=True)
        return True

    def _extract_alias_target(self, s: str, roster) -> Optional[str]:
        """Find which roster player's aliases are being discussed."""
        p = s.find('别名')
        if p < 0:
            return None
        before = s[:p]
        after = s[p + 2:]
        best = None
        best_pos = -1
        for e in roster._entries:
            nm = e.get('name') or ''
            if not nm:
                continue
            pos = before.rfind(nm)
            if pos > best_pos:
                best, best_pos = nm, pos
        if best is not None:
            return best
        # fall back: a roster name that appears right after "别名"
        best_pos = None
        for e in roster._entries:
            nm = e.get('name') or ''
            if not nm:
                continue
            pos = after.find(nm)
            if pos >= 0 and (best_pos is None or pos < best_pos):
                best, best_pos = nm, pos
        return best

    def _start_alias(self, target: str, manual: bool) -> None:
        if not target:
            return
        if self._alias_running:
            if manual:
                self._send_direct_reply('上一次别名分析还在进行，请稍后再试。')
            return
        self._alias_running = True
        threading.Thread(target=self._alias_job,
                         args=(target, manual), daemon=True).start()

    def _alias_job(self, target: str, manual: bool) -> None:
        try:
            res = self._run_alias(target)
        except Exception as e:  # noqa: BLE001
            res = {'ok': False, 'msg': '别名分析出错: ' + str(e)}
        finally:
            self._alias_running = False
        self._last_alias_ms[_norm(target).casefold()] = _now_ms()

        if manual:
            if res.get('ok'):
                name = res.get('name') or target
                aliases = [a for a in (res.get('aliases') or []) if a.strip()]
                if aliases:
                    self._send_direct_reply(f"{name} 的别名：{'、'.join(aliases)}")
                else:
                    self._send_direct_reply(f'暂未发现 {name} 的新别名。')
            else:
                self._send_direct_reply(res.get('msg') or '别名分析失败。')
        elif res.get('ok'):
            aliases = [a for a in (res.get('aliases') or []) if a.strip()]
            self._log(f'AI别名分析完成：{res.get("name")} -> {"、".join(aliases) or "无"}')
        else:
            self._log('AI别名分析未完成：' + str(res.get('msg', '')))

    def _run_alias(self, target: str) -> Dict[str, Any]:
        roster = self._roster
        if roster is None:
            return {'ok': False, 'msg': '资料库未配置。'}
        provider = None
        try:
            provider = llm.create_provider(self.config, None)
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'msg': f'AI API 未配置: {e}'}
        try:
            return self._run_alias_with(provider, roster, target)
        finally:
            try:
                provider.close()
            except Exception:
                pass

    def _run_alias_with(self, provider, roster, target: str) -> Dict[str, Any]:
        key = _norm(target).casefold()
        logs = self._chat_logs.get(key) or []
        entry = roster.find(target)
        if entry is not None:
            display = str(entry.get('name') or '') or target
            source = str(entry.get('source') or '')
        else:
            display = _norm(target)
            paths = roster.file_paths()
            source = paths[0] if paths else ''
        if not source:
            return {'ok': False, 'msg': f'{display} 没有对应的资料表，请先导入。'}
        if not logs:
            return {'ok': False, 'msg': f'暂时没有 {display} 的聊天记录。'}

        lines = [m.get('text', '') for m in logs if m.get('text')]
        transcript = '\n'.join('> ' + ln for ln in lines[-12:])
        system = _ALIAS_SYSTEM_TMPL.format(player=display)
        try:
            out = provider.send(system, [{'role': 'user', 'content': transcript}])
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'msg': f'AI 别名分析失败: {e}'}

        tokens = []
        for part in split_aliases(str(out or '')):
            t = _norm(part)
            if (not t or t.lower() == '无' or t.lower() == 'none'
                    or t.casefold() == _norm(display).casefold()):
                continue
            tokens.append(t)
        tokens = tokens[:_ALIAS_MAX_RESULTS]
        if not tokens:
            return {'ok': False, 'msg': f'暂未发现 {display} 的别名。'}

        try:
            cells = roster.add_aliases(source, display, tokens)
        except RosterError as e:
            return {'ok': False, 'msg': str(e)}
        return {'ok': True, 'name': display,
                'aliases': [c for c in cells if str(c).strip()]}

    def _build_context(self, current_user_message: str) -> List[Dict[str, str]]:
        cfg = self.config
        if cfg.contextEnabled and cfg.contextLength > 0:
            messages = list(self._context)
            if not messages:
                messages.append({'role': 'user', 'content': current_user_message})
            while len(messages) > cfg.contextLength:
                messages.pop(0)
            return messages
        return [{'role': 'user', 'content': current_user_message}]

    def _add_to_context(self, msg: Dict[str, str]) -> None:
        cfg = self.config
        if cfg.contextLength <= 0:
            return
        self._context.append(msg)
        while len(self._context) > cfg.contextLength:
            self._context.popleft()

    # ------------------------------------------------------------------
    # Matching / filtering helpers
    # ------------------------------------------------------------------
    def _matches_trigger(self, text: str) -> bool:
        if not self._trigger_patterns:
            return False
        for pattern in self._trigger_patterns:
            if pattern.search(text):
                return True
        return False

    def _matches_image_trigger(self, text: str) -> bool:
        if self._image_trigger_pattern is None:
            return False
        return bool(self._image_trigger_pattern.search(text))

    def _extract_image_prompt(self, text: str) -> Optional[str]:
        if self._image_trigger_pattern is None:
            return None
        m = self._image_trigger_pattern.search(text)
        if m:
            if m.groups():
                group = m.group(1)
                if group is not None and group.strip():
                    return group.strip()
            stripped = text[m.end():].strip()
            return stripped if stripped else None
        return None

    def _is_blocked(self, text: str) -> bool:
        if not self.config.restrictionEnabled:
            return False
        for pattern in self._blocked_patterns:
            if pattern.search(text):
                return True
        return False

    def set_bot_username(self, name: Optional[str]) -> None:
        self._bot_username = name

    def _is_self(self, player_name: Optional[str]) -> bool:
        if not player_name or not self._bot_username:
            return False
        return player_name.lower() == self._bot_username.lower()

    def _is_player_blocked(self, player_name: Optional[str]) -> bool:
        if not player_name or not self.config.blockedPlayers:
            return False
        for b in self.config.blockedPlayers:
            if b.get('enabled', True) and b.get('name') and \
                    str(b['name']).lower() == player_name.lower():
                return True
        return False

    def _extract_player_name(self, raw_text: str) -> Optional[str]:
        m = re.match(r'^\s*\|?\s*\[[^\]]+\]\s*([^\s»>]+)\s*[»>:]', raw_text)
        if m:
            return m.group(1).strip()
        m2 = re.match(r'^\s*[<\[\s]\s*([^\s\]><]+)\s*[>\]]\s*', raw_text)
        if m2:
            return m2.group(1).strip()
        return None

    def _strip_player_prefix(self, text: str, player_name: str) -> str:
        quoted = re.escape(player_name)
        prefix_pattern = re.compile(
            r'^<[^<>]*' + quoted + r'[^<>]*>\s*|^\[[^\[\]]*' + quoted + r'[^\[\]]*\]\s*')
        m = prefix_pattern.match(text)
        if m:
            stripped = text[m.end():].strip()
            if stripped:
                return stripped
        generic = re.compile(r'^\s*(?:[<\[]\s*[^\]<>]{1,32}\s*[>\]]|[|][^»>]{1,64}[»>])\s*:?\s*')
        m2 = generic.match(text)
        if m2:
            stripped = text[m2.end():].strip()
            if stripped:
                return stripped
        return text

    def _split_reply(self, reply: str) -> List[str]:
        result: List[str] = []
        if not reply:
            return result
        normalized = reply.replace('\r\n', '\n').strip()
        if not normalized:
            return result
        max_chars = max(1, self.config.maxCharsPerMessage)
        max_messages = max(1, self.config.maxReplyMessages)
        for paragraph in normalized.split('\n'):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            while len(paragraph) > max_chars and len(result) < max_messages:
                cut = max_chars
                space = paragraph.rfind(' ', 0, max_chars)
                if space > 0:
                    cut = space
                chunk = paragraph[:cut].strip()
                if not chunk:
                    break
                result.append(chunk)
                paragraph = paragraph[cut:].strip()
            if paragraph and len(result) < max_messages:
                result.append(paragraph)
            if len(result) >= max_messages:
                break
        return result

    def _cleanup_recent_messages(self) -> None:
        now = _now_ms()
        self._recent_ai_messages = {k: v for k, v in self._recent_ai_messages.items()
                                    if now - v <= 30000}
        self._recent_player_messages = {k: v for k, v in self._recent_player_messages.items()
                                        if now - v <= 5000}

    # ------------------------------------------------------------------
    # Image generation
    # ------------------------------------------------------------------
    def generate_image(self, prompt: str) -> None:
        if self._image_generating:
            self._log('文生图已在进行中。')
            return
        self._image_generating = True
        try:
            url = self._request_image_generation(prompt)
            if url:
                self.on_send_chat(url)
                self._log_activity('AI > [图] ' + url)
                self._log('图像生成完成，已发送 URL。')
            else:
                self._error('图像生成失败，请查看日志。')
        except Exception as e:  # noqa: BLE001
            self._error('文生图失败: ' + str(e))
        finally:
            self._image_generating = False

    def _request_image_generation(self, prompt: str) -> Optional[str]:
        cfg = self.config
        provider = self._image_provider
        if provider is None:
            provider = llm.create_image_provider(cfg)
            self._image_provider = provider
        if provider is None:
            self._error('文生图 API 未正确配置。')
            return None
        return provider.generate(prompt, cfg.imageModel, cfg.imageSize,
                                 getattr(cfg, 'imageRatio', '1:1') or '1:1')
