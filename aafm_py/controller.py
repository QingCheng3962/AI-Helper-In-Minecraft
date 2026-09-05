"""Controller tying the bot engine, AI player and UI together."""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from .ai_player import AiPlayer
from .bot_engine import BotEngine, BASE_DIR
from .config import (AiPlayerConfig, AppConfig, load_auth_profile)

# Phrases that indicate the LittleSkin / yggdrasil session became invalid and a
# fresh login is needed. Matched (case-insensitively) against engine errors and
# pre-login kicks.
AUTH_FAIL_HINTS = (
    'invalid access token',
    'invalid session',
    'access token',
    'token invalid',
    '令牌无效',
    'token 无效',
    '登录失效',
    '登录已过期',
    '请重新登录',
    '重新登录',
    'session expired',
    'unauthorized',
    'bad login',
    'login failed',
    'failed to verify',
    '身份验证失败',
)


def _is_auth_failure(text: str) -> bool:
    if not text:
        return False
    low = str(text).lower()
    for hint in AUTH_FAIL_HINTS:
        if hint in low:
            return True
    return False


class Controller:
    def __init__(self) -> None:
        self.config: AppConfig = AppConfig.load()
        self.ai_config: AiPlayerConfig = AiPlayerConfig.load()
        self.ui_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        self._bot_events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._engine = BotEngine(cwd=BASE_DIR, on_event=self._on_bot_event)
        self.ai_player: Optional[AiPlayer] = None

        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._connecting = False
        self._bot_username: Optional[str] = None
        self._relogin_triggered = False

    # ------------------------------------------------------------------
    # Connection control
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected and self._engine.running

    @property
    def connecting(self) -> bool:
        return self._connecting

    def connect(self) -> None:
        if self._connecting or self.connected:
            return
        self._relogin_triggered = False
        self.config.save()
        self.ai_config.save()
        self._connecting = True
        self._post_ui({'kind': 'status', 'text': '正在连接...'})

        runtime = self._build_runtime_config()
        try:
            self._engine.start(runtime)
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'status', 'text': f'启动失败: {e}'})
            self._connecting = False
            return

        self.ai_player = AiPlayer(
            self.ai_config,
            on_send_chat=self._on_ai_send_chat,
            log=lambda m: self._post_ui({'kind': 'log', 'level': 'ai', 'message': m}),
            error=lambda m: self._post_ui({'kind': 'log', 'level': 'error', 'message': m}),
        )
        if self._bot_username:
            self.ai_player.set_bot_username(self._bot_username)

        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def disconnect(self) -> None:
        self._running = False
        self._connecting = False
        self._connected = False
        if self.ai_player:
            self.ai_player.stop_now()
        self._engine.stop()
        self._post_ui({'kind': 'status', 'text': '已断开连接'})
        self._post_ui({'kind': 'disconnected', 'reason': 'user'})

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def send_chat(self, text: str) -> None:
        text = (text or '').strip()
        if not text:
            return
        if not self._engine.say(text):
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '无法发送（未连接）。'})

    def reload_ai_player(self) -> None:
        self.ai_config = AiPlayerConfig.load()
        if self.ai_player:
            self.ai_player.reload(self.ai_config)
        self._post_ui({'kind': 'log', 'level': 'info', 'message': 'AI 玩家配置已重载。'})

    def clear_ai_context(self) -> None:
        if self.ai_player:
            self.ai_player.clear_context()
        self._post_ui({'kind': 'log', 'level': 'info', 'message': '上下文已清空。'})

    def set_ai_enabled(self, enabled: bool) -> None:
        self.ai_config.enabled = bool(enabled)
        self.ai_config.save()
        if not enabled and self.ai_player:
            self.ai_player.stop_now()
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': 'AI 玩家已' + ('启用' if enabled else '禁用') + '。'})

    def add_blocked_player(self, name: str) -> None:
        name = (name or '').strip()
        if not name:
            return
        players = self.ai_config.blockedPlayers
        for b in players:
            if b.get('name') and str(b['name']).lower() == name.lower():
                self._post_ui({'kind': 'log', 'level': 'info',
                               'message': f'玩家已在黑名单: {name}'})
                return
        players.append({'name': name, 'enabled': True})
        self.ai_config.save()
        if self.ai_player:
            self.ai_player.config = self.ai_config
            self.ai_player._compile_patterns()
        self._post_ui({'kind': 'log', 'level': 'info', 'message': f'已添加黑名单: {name}'})

    def set_extra_enabled(self, which: str, enabled: bool) -> None:
        if which == 'autoeat':
            self.config.autoeat.enabled = bool(enabled)
            self.config.save()
            self._engine.set_autoeat(enabled)
        elif which == 'quiz':
            self.config.quiz.enabled = bool(enabled)
            self.config.save()
            self._engine.set_quiz(enabled)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    def _build_runtime_config(self) -> Dict[str, Any]:
        cfg = self.config
        auth = {'type': cfg.auth.type, 'username': cfg.auth.username,
                'uuid': cfg.auth.uuid, 'session': cfg.auth.session}
        if cfg.auth.type == 'littleskin':
            auth = {
                'type': 'littleskin',
                'site': cfg.auth.site_base(),
                'username': cfg.auth.username,
            }
            profile = load_auth_profile()
            if profile:
                auth['username'] = profile.get('username')
                auth['uuid'] = profile.get('uuid')
                auth['session'] = profile.get('session')
                if profile.get('site'):
                    auth['site'] = profile['site'].rstrip('/')
            else:
                # Fall back to a config-built offline session if no profile yet.
                auth = {'type': 'offline', 'username': cfg.auth.username or 'AI_Bot'}
                self._post_ui({'kind': 'log', 'level': 'warn',
                               'message': '未找到 LittleSkin 登录信息，将尝试以离线身份进入。'
                                          '请先在设置页点「LittleSkin 登录」。'})
        elif cfg.auth.type == 'microsoft':
            auth = {
                'type': 'microsoft',
                'username': cfg.auth.username or 'MSA_Account',
                'profilesFolder': (cfg.auth.profilesFolder or '').strip(),
            }
        llm = {
            'endpoint': (self.ai_config.baseUrl or cfg.llm.baseUrl),
            'model': self.ai_config.model or cfg.llm.model,
            'apiKey': self.ai_config.apiKey or cfg.llm.apiKey,
            'headers': {},
        }
        return {
            'server': {
                'host': cfg.server.host,
                'port': cfg.server.port,
                'version': cfg.server.version or '',
                'brand': cfg.server.brand or 'aafm-python',
                'viewDistance': cfg.server.viewDistance or 'tiny',
            },
            'auth': auth,
            'llm': llm,
            'autoeat': {'enabled': cfg.autoeat.enabled},
            'quiz': {'enabled': cfg.quiz.enabled, 'minDelay': cfg.quiz.minDelay,
                     'questionBank': cfg.quiz.questionBank},
        }

    # ------------------------------------------------------------------
    # Bot event handling
    # ------------------------------------------------------------------
    def _on_bot_event(self, ev: Dict[str, Any]) -> None:
        self._bot_events.put(ev)

    def _on_ai_send_chat(self, text: str) -> None:
        if not self._engine.say(text):
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': 'AI 回复发送失败（未连接）。'})

    def _worker_loop(self) -> None:
        last_tick = time.time()
        while self._running:
            try:
                ev = self._bot_events.get(timeout=0.3)
            except queue.Empty:
                ev = None
            if ev is not None:
                self._process_bot_event(ev)
            now = time.time()
            if now - last_tick >= 1.0:
                if self.ai_player:
                    try:
                        self.ai_player.tick()
                    except Exception as e:  # noqa: BLE001
                        self._post_ui({'kind': 'log', 'level': 'error',
                                       'message': 'AI 定时回复出错: ' + str(e)})
                last_tick = now

    def _process_bot_event(self, ev: Dict[str, Any]) -> None:
        event = ev.get('event')

        if event == 'log':
            self._post_ui({'kind': 'log', 'level': ev.get('level', 'info'),
                           'message': ev.get('message', '')})
        elif event == 'message':
            # raw system chat; ignore by default
            pass
        elif event == 'quizLog':
            self._post_ui({'kind': 'log', 'level': 'quiz',
                           'message': '[答题] ' + ev.get('message', '')})
        elif event == 'chat':
            player = ev.get('player', '?')
            content = ev.get('content', '')
            private = ev.get('isPrivate', False)
            raw = ev.get('raw') or ''
            self._post_ui({'kind': 'chat', 'player': player, 'content': content,
                           'private': bool(private)})
            if self.ai_player:
                try:
                    self.ai_player.on_player_message(player, content, raw=raw)
                except Exception as e:  # noqa: BLE001
                    self._post_ui({'kind': 'log', 'level': 'error',
                                   'message': 'AI 处理消息出错: ' + str(e)})
        elif event == 'actionBar':
            self._post_ui({'kind': 'actionBar', 'text': ev.get('text', '')})
        elif event == 'health':
            self._post_ui({'kind': 'health', 'health': ev.get('health'),
                           'food': ev.get('food')})
        elif event == 'spawn':
            self._connected = True
            self._connecting = False
            username = ev.get('username') or ''
            if username:
                self._bot_username = username
                if self.ai_player:
                    self.ai_player.set_bot_username(username)
            players = ev.get('players') or []
            self._post_ui({'kind': 'status', 'text': f'已连接（{username}）'})
            self._post_ui({'kind': 'spawn', 'username': username, 'players': players})
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'已进入服务器，在线玩家: {", ".join(players) or "无"}'})
        elif event == 'kicked':
            self._connected = False
            self._post_ui({'kind': 'status', 'text': '被踢出服务器'})
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '被踢出: ' + ev.get('reason', '')})
            self._maybe_request_relogin(ev.get('reason', ''))
        elif event == 'error':
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': 'Bot 错误: ' + ev.get('message', '')})
            self._maybe_request_relogin(ev.get('message', ''))
        elif event == 'end':
            self._connected = False
            self._connecting = False
            reason = ev.get('reason') or ''
            self._post_ui({'kind': 'status', 'text': '连接已结束'})
            self._post_ui({'kind': 'disconnected', 'reason': reason})
        elif event == 'chatSent':
            self._post_ui({'kind': 'log', 'level': 'sent',
                           'message': '» ' + ev.get('text', '')})
        elif event == 'pong':
            self._post_ui({'kind': 'log', 'level': 'info', 'message': 'pong'})
        elif event == 'msaCode':
            code = str(ev.get('user_code') or '')
            uri = str(ev.get('verification_uri') or 'https://www.microsoft.com/link')
            self._post_ui({'kind': 'log', 'level': 'msa',
                           'message': f'[正版登录] 请在浏览器打开 {uri} ，输入代码 {code} 完成微软账户授权。'})
        elif event == 'msa':
            self._post_ui({'kind': 'log', 'level': 'msa',
                           'message': '[正版登录] ' + str(ev.get('message') or '')})

    def _maybe_request_relogin(self, text: str) -> None:
        """When a LittleSkin session appears invalid, ask the GUI to re-login once."""
        if self._relogin_triggered:
            return
        if self.config.auth.type != 'littleskin':
            return
        if not _is_auth_failure(text):
            return
        self._relogin_triggered = True
        self._post_ui({'kind': 'reloginNeeded', 'message': str(text or '')})

    def _post_ui(self, ev: Dict[str, Any]) -> None:
        self.ui_queue.put(ev)

    def drain_ui(self, max_items: int = 500) -> List[Dict[str, Any]]:
        items = []
        try:
            for _ in range(max_items):
                items.append(self.ui_queue.get_nowait())
        except queue.Empty:
            pass
        return items
