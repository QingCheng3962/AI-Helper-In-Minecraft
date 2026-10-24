"""Controller tying the bot engine, AI player and UI together."""
from __future__ import annotations

import base64
import json
import os
import queue
import random
import re
import socket
import subprocess
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from .ai_player import AiPlayer, _BRACKET_LITERAL, _compile_trigger, _split_top_level_commas
from .affinity import AffinityStore
from .dailydb import DailyDB
from . import docbook
from .bot_engine import BotEngine, BASE_DIR, SendResult
from .config import (AiPlayerConfig, AppConfig, BOOK_PROGRESS_FILE,
                     DEFAULT_BASE_URLS, load_auth_profile)
from .log_saver import LogSaver
from .photo_server import PhotoServer, save_image_bytes
from .qq_engine import QqEngine
from .roster import Roster, RosterError

NAPCAT_DIR = os.path.join(BASE_DIR, 'NapCat')
NAPCAT_INNER_DIR = os.path.join(NAPCAT_DIR, 'napcat')
NAPCAT_LAUNCHER = os.path.join(NAPCAT_INNER_DIR, 'launcher-win10.bat')
NAPCAT_CACHE_DIR = os.path.join(NAPCAT_INNER_DIR, 'cache')
NAPCAT_CONFIG_DIR = os.path.join(NAPCAT_INNER_DIR, 'config')
QQ_ACCOUNTS_FILE = os.path.join(BASE_DIR, 'qq_accounts.json')

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


def _qq_mode_label(qq) -> str:
    ep = qq.active() if hasattr(qq, 'active') else qq
    if ep.mode == 'reverse':
        return f'反向监听 {ep.listenHost}:{ep.listenPort}'
    return f'正向连接 {ep.url}'


def _resolve_llm_endpoint(provider: str, base: str) -> str:
    """Full chat endpoint mirroring llm.py (so the quiz uses the global config)."""
    b = (base or '').strip().rstrip('/')
    if not b:
        return ''
    provider = (provider or 'openai').lower()
    if provider == 'anthropic':
        return b if b.endswith('/v1/messages') else b + '/v1/messages'
    if b.endswith('/chat/completions'):
        return b
    return b + '/chat/completions'


_MC_COLOR_RE = re.compile(r'(?:&|§)[0-9a-fk-orA-FK-OR]|(?:&|§)x(?:[0-9a-fA-F](?:&|§)?){6}')


def _strip_mc_codes(text: str) -> str:
    """Drop Minecraft legacy color/format codes (&a, §c, &x&f&f&0&0&0&0...)."""
    return _MC_COLOR_RE.sub('', text or '')


def _sanitize_text(value) -> str:
    """Replace transport-breaking characters (control chars, lone surrogates,
    Unicode noncharacters) with '?'. Normal text, emoji and '&' codes survive."""
    s = '' if value is None else str(value)
    out = []
    for ch in s:
        o = ord(ch)
        if o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F or o in (0xFFFE, 0xFFFF) \
                or 0xD800 <= o <= 0xDFFF:
            out.append('?')
        else:
            out.append(ch)
    return ''.join(out)


def _mc_len(text) -> int:
    """MC 聊天命令的 256 上限按 Java 字符串（UTF-16 码元）计。

    Python 的 ``len()`` 只数码点：一个 emoji 在 Java 里占 2 个码元、在 Python 里
    只算 1 个 → 含 emoji 的文案会被算少，最后超长被服务器踢（#19）。
    """
    s = '' if text is None else str(text)
    try:
        return len(s.encode('utf-16-le')) // 2
    except Exception:  # noqa: BLE001
        return len(s)


def _mc_clip(text, limit: int) -> str:
    """按 UTF-16 码元截断（不拆开代理对），用于 /msg 等有硬上限的发送。"""
    s = '' if text is None else str(text)
    if limit <= 0:
        return ''
    if _mc_len(s) <= limit:
        return s
    out = []
    used = 0
    for ch in s:
        w = 2 if ord(ch) > 0xFFFF else 1
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return ''.join(out)


def _is_auth_failure(text: str) -> bool:
    if not text:
        return False
    low = str(text).lower()
    for hint in AUTH_FAIL_HINTS:
        if hint in low:
            return True
    return False


# 机器人自己发到公屏的播放数据里一定有音频直链；带直链的消息一律不再当成新点歌（防自触发死循环）。
_AUDIO_URL_RE = re.compile(r'https?://\S*(?:music\.126\.net|\.mp3|\.m4a|\.flac)', re.I)

# 直链混淆：公屏的链接过滤会把 http(s):// 整条替换成「[链接]」，所以机器人把直链
# ①去掉 query ②去掉协议头（¤=http、¥=https）③把 . / : 换成安全字符，客户端 Mod 还原。
URL_MARK_HTTP = '¤'
URL_MARK_HTTPS = '¥'


class Controller:
    def __init__(self) -> None:
        self.config: AppConfig = AppConfig.load()
        self.ai_config: AiPlayerConfig = AiPlayerConfig.load()
        self.ui_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        self.roster = Roster(enabled=self.config.roster.enabled)
        self._load_roster_files()

        self._bot_events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._engine = BotEngine(cwd=BASE_DIR, on_event=self._on_bot_event)
        self.ai_player: Optional[AiPlayer] = None

        self._qq_events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._qq = QqEngine(cwd=BASE_DIR, on_event=self._on_qq_event)
        self._qq_connected = False
        self._qq_last_state: Optional[str] = None
        self._qq_pending_relays: List[str] = []
        self._qq_relay_warn_at = 0.0
        self._snowluma_uin = ''
        self._last_mc_to_qq_time = 0.0
        self._last_health_alert = 0.0
        self._song_reply_at: Dict[str, float] = {}
        self._song_posted_at: Dict[str, float] = {}
        # 视频推荐：**全服共用一个** 30 秒冷却（谁点都算，过了再点就换一批新的）
        self._video_last_at: float = 0.0
        # 在线玩家名单（引擎 spawn / players 事件更新；群发私信用）
        self._player_list: List[str] = []
        self._players_req_at: float = 0.0
        self._players_got_at: float = 0.0
        self._mass_running: bool = False
        # 最近发过的歌：key -> (时间, 队列条目快照)。同一首歌又被点时直接复用，**不再搜一遍**
        # （链接里带的 id: 还能让 Mod 自己兜底重取）。
        self._song_recent: Dict[str, Any] = {}
        # 点歌播放队列（机器人真队列：按歌长依次播放）
        self._song_queue: List[Dict[str, Any]] = []
        self._song_current: Optional[Dict[str, Any]] = None
        self._song_next_at = 0.0
        self._song_seq = 0
        self._qq_nicknames: Dict[str, str] = {}
        self._load_qq_accounts()
        self._photo = PhotoServer()
        self._public_base_cache = ''

        self.log_saver = LogSaver(BASE_DIR)
        # 好感度（[触发词]好感度 查询；聊天/点歌慢慢涨）
        self.affinity = AffinityStore(os.path.join(BASE_DIR, 'affinity.json'))
        # 每日数据（SQLite）：当日幸运值 + 当日签到。重启不会改写；
        # 跨天自动「备份 → 删除 → 重建」，旧库存到 db_backup/db_backup_<时间>.db
        self.daily = DailyDB(os.path.join(BASE_DIR, 'daily.db'),
                             os.path.join(BASE_DIR, 'db_backup'))

        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._connecting = False
        self._bot_username: Optional[str] = None
        self._relogin_triggered = False

        # Auto reconnect / kick handling.
        self._manual_disconnect = False
        self._reconnect_at: Optional[float] = None
        self._reconnect_attempts = 0
        self._keep_retry_deadline: Optional[float] = None
        self._kick_lobby_pending = False
        self._lobby_at: Optional[float] = None
        # 外置登录 / 网络服务异常时进入退避重试（避免刷屏 + 打风暴）。
        self._service_outage: Optional[str] = None
        self._service_error_count = 0
        self._last_service_error_log = 0.0
        self._proactive_at: Optional[float] = None
        self._auto_lobby_at: Optional[float] = None
        self._online_next_check: Optional[float] = None
        self._online_retry_at: Optional[float] = None

        self.check_updates_async()
        threading.Thread(target=self._napcat_config_loop, daemon=True).start()
        threading.Thread(target=self._log_save_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Log persistence loop (daily rollover)
    # ------------------------------------------------------------------
    def _log_save_loop(self) -> None:
        while True:
            try:
                self.tick_log_save()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(30)

    # ------------------------------------------------------------------
    # Auto-configure OneBot network for newly scanned QQ accounts
    # ------------------------------------------------------------------
    def _napcat_config_loop(self) -> None:
        # First pass shortly after startup, then poll for new accounts.
        while True:
            try:
                # Auto-writing the OneBot config only applies to NapCat; SnowLuma
                # is configured through its own WebUI.
                if self.config.qq.napcat.autoConfigNewAccounts and self.config.qq.framework == 'napcat':
                    self._ensure_napcat_configs()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5)

    def _desired_network(self):
        """Build the WS server/client entry from the NapCat settings."""
        cfg = self.config.qq.napcat
        if cfg.mode == 'reverse':
            host = cfg.listenHost or '0.0.0.0'
            if host in ('0.0.0.0', '::'):
                host = '127.0.0.1'
            client = {
                'name': 'mc-relay',
                'enable': True,
                'url': f'ws://{host}:{int(cfg.listenPort)}/onebot/v11/ws',
                'messagePostFormat': 'array',
                'reportSelfMessage': False,
                'reconnectInterval': 5000,
                'token': cfg.accessToken or '',
                'debug': False,
                'heartInterval': 30000,
                'verifyCertificate': True,
            }
            return None, client
        # forward (default): NapCat runs the WS server we connect to.
        parsed = urllib.parse.urlparse(cfg.url or 'ws://127.0.0.1:3001')
        host = parsed.hostname or '127.0.0.1'
        try:
            port = parsed.port or 3001
        except ValueError:
            port = 3001
        token = cfg.accessToken or ''
        if not token and parsed.query:
            for kv in parsed.query.split('&'):
                if kv.startswith('access_token='):
                    token = urllib.parse.unquote(kv.split('=', 1)[1])
        server = {
            'name': 'mc-relay',
            'enable': True,
            'host': host,
            'port': int(port),
            'messagePostFormat': 'array',
            'reportSelfMessage': False,
            'token': token,
            'enableForcePushEvent': True,
            'debug': False,
            'heartInterval': 30000,
        }
        return server, None

    def _ensure_napcat_configs(self) -> None:
        d = self._napcat_config_dir()
        if not os.path.isdir(d):
            return
        uins = set()
        for name in os.listdir(d):
            m = re.match(r'(?:onebot11|napcat|napcat_protocol)_(\d+)\.json$', name)
            if m:
                uins.add(m.group(1))
        for uin in uins:
            self._ensure_account_config(d, uin)

    def _ensure_account_config(self, d: str, uin: str) -> None:
        # Core + protocol configs (NapCat normally creates these itself).
        core_path = os.path.join(d, f'napcat_{uin}.json')
        if not os.path.exists(core_path):
            self._write_json(core_path, {
                'fileLog': False, 'consoleLog': True, 'fileLogLevel': 'debug',
                'consoleLogLevel': 'info', 'packetBackend': 'auto', 'packetServer': '',
                'o3HookMode': 1, 'bypass': {
                    'hook': False, 'window': False, 'module': False,
                    'process': False, 'container': False, 'js': False},
                'autoTimeSync': True,
            })
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'[NapCat] 已为账号 {uin} 生成 napcat_{uin}.json'})
        proto_path = os.path.join(d, f'napcat_protocol_{uin}.json')
        if not os.path.exists(proto_path):
            self._write_json(proto_path, {
                'enable': False,
                'network': {'httpServers': [], 'websocketServers': [], 'websocketClients': []},
            })

        # OneBot network config: make sure our relay is present and enabled.
        path = os.path.join(d, f'onebot11_{uin}.json')
        data = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:  # noqa: BLE001
                return  # never clobber a file we can't parse
        if not isinstance(data, dict):
            data = {}
        data.setdefault('musicSignUrl', '')
        data.setdefault('enableLocalFile2Url', False)
        data.setdefault('parseMultMsg', False)
        data.setdefault('imageDownloadProxy', '')
        data.setdefault('timeout', {
            'baseTimeout': 10000, 'uploadSpeedKBps': 256,
            'downloadSpeedKBps': 256, 'maxTimeout': 1800000})
        net = data.get('network')
        if not isinstance(net, dict):
            net = {}
            data['network'] = net
        for key in ('httpServers', 'httpSseServers', 'httpClients', 'websocketClients', 'plugins'):
            if not isinstance(net.get(key), list):
                net[key] = []
        servers = net.get('websocketServers')
        if not isinstance(servers, list):
            servers = []
            net['websocketServers'] = servers

        server, client = self._desired_network()
        changed = False
        if server is not None:
            exists = any(isinstance(s, dict) and s.get('name') == 'mc-relay'
                         and int(s.get('port') or 0) == server['port']
                         for s in servers)
            if not exists:
                servers.append(server)
                changed = True
        if client is not None:
            clients = net['websocketClients']
            exists = any(isinstance(c, dict) and c.get('name') == 'mc-relay'
                         and c.get('url') == client['url']
                         for c in clients)
            if not exists:
                clients.append(client)
                changed = True

        if changed or not os.path.exists(path):
            self._write_json(path, data)
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'[NapCat] 已为账号 {uin} 写入 OneBot 转述配置。'})

    @staticmethod
    def _write_json(path: str, data: Dict[str, Any]) -> None:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # Online update check
    # ------------------------------------------------------------------
    def check_updates_async(self) -> None:
        """Check NapCat / this program for updates in the background."""
        def worker():
            try:
                from . import updater
                updater.check_updates(
                    lambda level, msg: self._post_ui({'kind': 'log', 'level': level, 'message': msg}))
            except Exception as e:  # noqa: BLE001
                self._post_ui({'kind': 'log', 'level': 'info',
                               'message': '[更新] 检查失败（可忽略）: ' + str(e)})
        threading.Thread(target=worker, daemon=True).start()

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
        self._manual_disconnect = False
        self._reconnect_at = None
        self._keep_retry_deadline = None
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
            roster=self.roster,
            on_reply=self._on_ai_replied,
        )
        if self._bot_username:
            self.ai_player.set_bot_username(self._bot_username)

        if self.config.qq.enabled and not self._qq.running:
            self._start_qq()

        self._running = True
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        """Start the event loop if it is not already running."""
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker.start()

    def disconnect(self) -> None:
        self._manual_disconnect = True
        self._reconnect_at = None
        self._lobby_at = None
        self._kick_lobby_pending = False
        self._running = False
        self._connecting = False
        self._connected = False
        if self.ai_player:
            self.ai_player.stop_now()
            self.ai_player.set_online(False)
        self._proactive_at = None
        self._auto_lobby_at = None
        self._online_next_check = None
        self._online_retry_at = None
        self._engine.stop()
        self._post_ui({'kind': 'status', 'text': '已断开连接'})
        self._post_ui({'kind': 'disconnected', 'reason': 'user'})

    def shutdown(self) -> None:
        """Full stop used on app close: MC connection plus the QQ relay."""
        self.disconnect()
        self._stop_qq()
        try:
            self.log_saver.flush()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Auto reconnect / kick handling
    # ------------------------------------------------------------------
    def _schedule_reconnect(self, kicked: bool) -> None:
        """Queue a reconnect after a non-user disconnect (or server kick)."""
        cfg = self.config.reconnect
        if not cfg.enabled or self._manual_disconnect:
            return
        if self._reconnect_at is not None:
            return
        # Start the keep-retrying window on the first schedule.
        if (getattr(cfg, 'keepRetrying', False) and self._keep_retry_deadline is None
                and int(cfg.keepRetryMaxMinutes or 0) > 0):
            self._keep_retry_deadline = time.time() + int(cfg.keepRetryMaxMinutes) * 60

        max_attempts = max(1, cfg.maxAttempts)
        keep = self._can_keep_retrying(cfg)
        if self._reconnect_attempts >= max_attempts and not keep:
            self._post_ui({'kind': 'reconnect', 'state': 'giveup',
                           'attempts': self._reconnect_attempts})
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'已连续重连 {self._reconnect_attempts} 次仍未成功，停止重连。'})
            return
        self._reconnect_attempts += 1
        if self._service_outage is not None:
            # 外置登录 / 网络服务异常：指数退避（最高 15 分钟），避免反复打风暴。
            base = max(1, int(cfg.delaySeconds or 0) or 1)
            delay = min(900, base * (2 ** min(self._reconnect_attempts - 1, 7)))
        else:
            delay = max(0, cfg.delaySeconds)
        self._reconnect_at = time.time() + delay
        if kicked and cfg.kickLobbyEnabled:
            self._kick_lobby_pending = True
        # Drop the dead engine process now so no stale events race the retry.
        self._engine.stop()
        self._post_ui({'kind': 'reconnect', 'state': 'scheduled',
                       'attempt': self._reconnect_attempts, 'max': max_attempts,
                       'delay': delay})
        if self._service_outage is not None:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'外置登录/网络服务异常，{delay}s 后重试'
                                      f'（第 {self._reconnect_attempts} 次，退避中）…'})
        else:
            suffix = '，持续重连中…' if keep and self._reconnect_attempts >= max_attempts else '…'
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'检测到{"被踢出" if kicked else "非主动断线"}，'
                                      f'{delay}s 后自动重连（第 {self._reconnect_attempts} 次）{suffix}'})

    def _can_keep_retrying(self, cfg) -> bool:
        if not getattr(cfg, 'keepRetrying', False):
            return False
        total = int(cfg.keepRetryMaxMinutes or 0)
        if total <= 0:
            return True  # unlimited
        if self._keep_retry_deadline is None:
            return True
        return time.time() < self._keep_retry_deadline

    def _run_reconnect(self) -> None:
        self._reconnect_at = None
        if self._manual_disconnect or not self.config.reconnect.enabled:
            return
        self._post_ui({'kind': 'reconnect', 'state': 'connecting',
                       'attempt': self._reconnect_attempts})
        self._connecting = False
        self.connect()

    def _schedule_kick_lobby(self) -> None:
        if not self._kick_lobby_pending:
            return
        delay = max(0, self.config.reconnect.kickLobbyDelaySeconds)
        self._lobby_at = time.time() + delay
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': f'重连成功，将在 {delay}s 后自动发送 /lobby。'})

    def _run_kick_lobby(self) -> None:
        self._lobby_at = None
        if not self._kick_lobby_pending:
            return
        self._kick_lobby_pending = False
        if self._engine.say('/lobby'):
            self._post_ui({'kind': 'log', 'level': 'sent', 'message': '» /lobby （自动）'})
        else:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '自动发送 /lobby 失败（未连接）。'})

    def _check_reconnect_triggers(self, text) -> bool:
        """Force a reconnect when a server line matches a configured trigger."""
        cfg = self.config.reconnect
        if not cfg.enabled or self._manual_disconnect:
            return False
        patterns = cfg.customTriggers or []
        s = str(text or '')
        if not patterns or not s:
            return False
        for p in patterns:
            try:
                matched = re.search(p, s, re.IGNORECASE) is not None
            except re.error:
                matched = p.lower() in s.lower()
            if not matched:
                continue
            if self._reconnect_at is None:
                self._post_ui({'kind': 'log', 'level': 'warn',
                               'message': f'命中重连触发词「{p}」，将自动重连…'})
                self._schedule_reconnect(kicked=True)
            return True
        return False

    def _maybe_proactive_reconnect(self) -> None:
        """While online, drop + reconnect every configured interval."""
        cfg = self.config.reconnect
        if (not cfg.enabled or not cfg.proactiveEnabled or self._manual_disconnect
                or not self.connected or self._reconnect_at is not None or self._connecting):
            return
        now = time.time()
        interval = max(1, int(cfg.proactiveIntervalSeconds or 0))
        if self._proactive_at is None:
            self._proactive_at = now + interval
            return
        if now >= self._proactive_at:
            self._proactive_at = None
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'主动重连：每 {interval}s 刷新一次连接…'})
            self._schedule_reconnect(kicked=False)

    def _maybe_auto_lobby(self) -> None:
        """Send /lobby every configured interval while online."""
        cfg = self.config.reconnect
        if not cfg.autoLobbyEnabled or self._manual_disconnect or not self.connected:
            return
        now = time.time()
        interval = max(1, int(cfg.autoLobbyIntervalSeconds or 0))
        if self._auto_lobby_at is None:
            self._auto_lobby_at = now + interval
            return
        if now >= self._auto_lobby_at:
            if self._engine.say('/lobby'):
                self._post_ui({'kind': 'log', 'level': 'sent',
                               'message': '» /lobby （定时）'})
            else:
                self._post_ui({'kind': 'log', 'level': 'warn',
                               'message': '定时 /lobby 发送失败（未连接）。'})
            self._auto_lobby_at = now + interval

    def _maybe_online_check(self) -> None:
        """Check online every interval; if offline, retry connecting until back."""
        cfg = self.config.reconnect
        if not cfg.onlineCheckEnabled or self._manual_disconnect:
            return
        now = time.time()
        interval = max(1, int(cfg.onlineCheckIntervalSeconds or 0))
        retry = max(1, int(cfg.onlineReconnectIntervalSeconds or 0))
        if self.connected:
            self._online_retry_at = None
            self._online_next_check = now + interval
            return
        # Offline: start the retry loop at the next check tick, then retry.
        if self._online_retry_at is None:
            if self._online_next_check is None:
                self._online_next_check = now + interval
                return
            if now < self._online_next_check:
                return
            self._online_next_check = now + interval
            if self._connecting or self._engine.running or self._reconnect_at is not None:
                return
            self._online_retry_at = now + retry
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'主动连接：检测到离线，每 {retry}s 重试（直到连上）。'})
            return
        if self._connecting or self._engine.running or self._reconnect_at is not None:
            return
        if now >= self._online_retry_at:
            self._online_retry_at = now + retry
            self._post_ui({'kind': 'log', 'level': 'warn', 'message': '主动连接：尝试重连…'})
            self._connecting = False
            self.connect()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def send_chat(self, text: str) -> None:
        # 公屏单包上限 256（UTF-16 码元）：留 6 个余量，emoji 也算对（#19）
        text = _mc_clip((text or '').strip(), 250).strip()
        if not text:
            return
        self._remember_sent(text)
        if not self._engine.say(text):
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '无法发送（未连接）。'})

    # ------------------------------------------------------------------
    # 自触发防线：记住「机器人自己发出去的话」，服务器把它广播回来时别再当成新消息
    # ------------------------------------------------------------------
    @staticmethod
    def _norm_line(text: str) -> str:
        """归一化一行聊天（去掉颜色码/空白，转小写），用于比对「这是不是我自己发过的」。"""
        t = re.sub(r'§.', '', str(text or ''))
        t = re.sub(r'&[0-9a-fk-or]', '', t, flags=re.IGNORECASE)
        t = re.sub(r'\s+', '', t)
        return t.strip().lower()

    def _remember_sent(self, text: str) -> None:
        n = self._norm_line(text)
        if not n:
            return
        if not hasattr(self, '_sent_lines'):
            self._sent_lines: Dict[str, float] = {}
        self._sent_lines[n] = time.time()
        if len(self._sent_lines) > 400:
            for k in sorted(self._sent_lines, key=lambda x: self._sent_lines[x])[:150]:
                self._sent_lines.pop(k, None)

    def _is_own_echo(self, text: str) -> bool:
        """这条聊天是不是我自己刚发出去的（15 分钟内、完全一致或明显包含）。"""
        n = self._norm_line(text)
        if len(n) < 4:
            return False
        now = time.time()
        for k, ts in getattr(self, '_sent_lines', {}).items():
            if now - ts > 900:
                continue
            if k == n:
                return True
            if len(k) >= 6 and len(n) >= 6 and (k in n or n in k):
                return True
        return False

    # ------------------------------------------------------------------
    # Document -> book and quill (with progress + stop/pause/continue)
    # ------------------------------------------------------------------
    def _settings_from_config(self) -> Dict[str, Any]:
        db = self.config.docBook
        return {
            # Pagination is line-driven; the char count is only the game's hard
            # limit (1024), so a small leftover value can never shorten pages.
            'pageChars': int(docbook.MAX_PAGE_CHARS),
            'pageLines': int(db.pageLines),
            'pagesPerBook': int(db.pagesPerBook),
            'signBook': bool(db.signBook),
            'titleTemplate': str(db.titleTemplate or ''),
            'startText': str(db.startText or ''),
            'endText': str(db.endText or ''),
            'endDrop': bool(db.endDrop),
            'autoGetBook': bool(db.autoGetBook),
        }

    def load_book_progress(self) -> Dict[str, Any]:
        try:
            with open(BOOK_PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def save_book_progress(self, **fields) -> None:
        data = self.load_book_progress()
        data.update(fields)
        try:
            with open(BOOK_PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass

    def clear_book_progress(self) -> None:
        try:
            os.remove(BOOK_PROGRESS_FILE)
        except OSError:
            pass

    def _send_book_write(self, pages, settings, doc_file, start_page, book_start) -> bool:
        pages = [str(p) for p in (pages or [])]
        if not pages:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[书与笔] 没有可写入的内容。'})
            return False
        if not self._engine.running:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[书与笔] 未连接服务器，无法写入。'})
            return False
        self.save_book_progress(
            file=doc_file, settings=settings, pagesPerBook=int(settings.get('pagesPerBook', 100)),
            nextPage=int(start_page), nextBook=int(book_start), totalPages=len(pages),
            updated=time.strftime('%Y-%m-%d %H:%M:%S'))
        ok = self._engine.write_book(
            pages,
            start_text=settings.get('startText', ''),
            end_text=settings.get('endText', ''),
            end_drop=bool(settings.get('endDrop', False)),
            auto_get_book=bool(settings.get('autoGetBook', True)),
            pages_per_book=int(settings.get('pagesPerBook', 100)),
            start_page=int(start_page), book_start=int(book_start),
            sign_book=bool(settings.get('signBook', False)),
            title_template=settings.get('titleTemplate', ''))
        if not ok:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[书与笔] 指令发送失败（引擎未就绪）。'})
        return ok

    def write_book_from_file(self, doc_file: str, pages, settings, start_page: int = 0,
                             book_start: int = 1) -> bool:
        """Start writing a document (used for both 写入/重新写入)."""
        return self._send_book_write(pages, settings, doc_file, start_page, book_start)

    def _pages_from_progress(self, prog: Dict[str, Any]):
        path = str(prog.get('file') or '')
        if not path or not os.path.exists(path):
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[书与笔] 找不到上次的文档，无法继续。'})
            return None, None, None
        settings = prog.get('settings') or self._settings_from_config()
        try:
            pages = docbook.load_pages(
                path,
                max_chars=docbook.MAX_PAGE_CHARS,
                max_lines=int(settings.get('pageLines', 14)))
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[书与笔] 重新读取文档失败：' + str(e)})
            return None, None, None
        return path, pages, settings

    def continue_book(self) -> bool:
        """继续上次未写完的文档（从未写入的地方开始）。"""
        prog = self.load_book_progress()
        if not prog:
            self._post_ui({'kind': 'log', 'level': 'warn', 'message': '[书与笔] 没有可继续的进度。'})
            return False
        path, pages, settings = self._pages_from_progress(prog)
        if pages is None:
            return False
        start_page = max(0, int(prog.get('nextPage', 0)))
        book_start = max(1, int(prog.get('nextBook', 1)))
        if start_page >= len(pages):
            self._post_ui({'kind': 'log', 'level': 'info', 'message': '[书与笔] 上次已全部写完。'})
            return False
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': f'[书与笔] 从第 {start_page + 1} 页继续写入（第 {book_start} 本起）。'})
        return self._send_book_write(pages, settings, path, start_page, book_start)

    def rewrite_book(self) -> bool:
        """从第一页重新写入。"""
        prog = self.load_book_progress()
        if not prog:
            self._post_ui({'kind': 'log', 'level': 'warn', 'message': '[书与笔] 没有可重新写入的进度。'})
            return False
        path, pages, settings = self._pages_from_progress(prog)
        if pages is None:
            return False
        self._post_ui({'kind': 'log', 'level': 'info', 'message': '[书与笔] 从第一页重新写入。'})
        return self._send_book_write(pages, settings, path, 0, 1)

    def pause_book(self, action: str = 'pause') -> bool:
        if not self._engine.running:
            self._post_ui({'kind': 'log', 'level': 'error', 'message': '[书与笔] 未连接服务器。'})
            return False
        ok = self._engine.book_control(action)
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': '[书与笔] 已请求' + ('暂停' if action == 'pause' else '停止')
                                  + '，将在当前这本写完后停下。'})
        return ok

    def write_book(self, pages, start_text: str = '', end_text: str = '',
                   end_drop: bool = False, auto_get_book: bool = True,
                   pages_per_book: int = 100, start_page: int = 0,
                   book_start: int = 1, sign_book: bool = False,
                   title_template: str = '', doc_file: str = '') -> bool:
        settings = self._settings_from_config()
        settings.update({
            'startText': start_text or settings['startText'],
            'endText': end_text or settings['endText'],
            'endDrop': bool(end_drop),
            'autoGetBook': bool(auto_get_book),
            'pagesPerBook': int(pages_per_book or 100),
            'signBook': bool(sign_book),
            'titleTemplate': str(title_template or ''),
        })
        return self._send_book_write(pages, settings, doc_file, start_page, book_start)

    def clear_book(self) -> bool:
        if not self._engine.running:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[书与笔] 未连接服务器，无法清空。'})
            return False
        ok = self._engine.clear_book()
        if not ok:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[书与笔] 清空指令发送失败（引擎未就绪）。'})
        return ok

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
    # QQ group-chat relay (OneBot 11 gateway)
    # ------------------------------------------------------------------
    @property
    def qq_connected(self) -> bool:
        return self._qq_connected and self._qq.running

    @property
    def qq_running(self) -> bool:
        return self._qq.running

    def _detect_running_snowluma_dir(self) -> str:
        """Find the directory of the currently running SnowLuma process.

        SnowLuma's full Windows package ships its own ``node.exe`` next to
        ``index.mjs``, so we look for that; on Linux we read ``/proc``. This makes
        the connection work on any machine regardless of the configured path.
        Cached for a few seconds.
        """
        now = time.time()
        cached = getattr(self, '_snowluma_detected', None)
        if cached is not None and now - getattr(self, '_snowluma_detect_at', 0) < 5:
            return cached
        found = ''
        try:
            for _attempt in range(2):
                paths = []
                if os.name == 'nt':
                    kwargs = {}
                    if hasattr(subprocess, 'CREATE_NO_WINDOW'):
                        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                    out = subprocess.run(
                        ['powershell', '-NoProfile', '-Command',
                         "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                         "Select-Object -ExpandProperty ExecutablePath"],
                        capture_output=True, text=True, timeout=8, **kwargs)
                    paths = [ln.strip() for ln in (out.stdout or '').splitlines() if ln.strip()]
                else:
                    for pid in os.listdir('/proc'):
                        if not pid.isdigit():
                            continue
                        try:
                            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                                cmd = f.read().decode('utf-8', 'ignore')
                        except OSError:
                            continue
                        if 'index.mjs' in cmd:
                            try:
                                paths.append(os.path.join(os.readlink(f'/proc/{pid}/cwd'), 'node'))
                            except OSError:
                                pass
                for p in paths:
                    d = os.path.dirname(p)
                    if os.path.isfile(os.path.join(d, 'index.mjs')):
                        found = d
                        break
                if found:
                    break
        except Exception:  # noqa: BLE001
            found = ''
        if found:
            self._snowluma_detected = found
            self._snowluma_detect_at = now
        return found

    def _snowluma_config_dir(self) -> str:
        bases = []
        p = (self.config.qq.snowluma.launcherPath or '').strip()
        if p:
            bases.append(p)
        detected = self._detect_running_snowluma_dir()
        if detected:
            bases.append(detected)
        bases.append(os.path.join(BASE_DIR, 'SnowLuma'))
        for base in bases:
            cfg = os.path.join(base, 'config')
            if os.path.isdir(cfg):
                return cfg
        return os.path.join(bases[0], 'config')

    def _snowluma_candidates(self):
        """All (uin, host, port, token) from SnowLuma's onebot_*.json, best first.

        SnowLuma can leave a stale token in the file after a restart, so callers
        should probe the candidates and use the one that actually handshakes.
        """
        cfg_dir = self._snowluma_config_dir()
        if not os.path.isdir(cfg_dir):
            return []
        try:
            names = [n for n in os.listdir(cfg_dir)
                     if n.startswith('onebot_') and n.endswith('.json')]
        except OSError:
            return []
        last = (self.config.qq.lastLoginUin or '').strip()

        def _mtime(name):
            try:
                return os.path.getmtime(os.path.join(cfg_dir, name))
            except OSError:
                return 0.0

        def _rank(name):
            uin = name[len('onebot_'):-len('.json')]
            return (0 if (last and uin == last) else 1, -_mtime(name))

        out = []
        for name in sorted(names, key=_rank):
            try:
                with open(os.path.join(cfg_dir, name), 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            servers = ((data.get('networks') or {}).get('wsServers') or [])
            if not servers:
                continue
            s = servers[0]
            try:
                port = int(s.get('port') or 3001)
            except (TypeError, ValueError):
                port = 3001
            out.append({
                'uin': name[len('onebot_'):-len('.json')],
                'host': str(s.get('host') or '127.0.0.1'),
                'port': port,
                'token': str(s.get('accessToken') or ''),
            })
        return out

    def _probe_ws_token(self, host: str, port: int, token: str,
                        timeout: float = 1.5):
        """Return True if a WebSocket handshake to host:port with token succeeds.

        SnowLuma answers ``401`` for a wrong token without upgrading; a correct
        token gets ``101 Switching Protocols``. Pure stdlib (raw socket).
        """
        query = ('?access_token=' + urllib.parse.quote(token, safe='')) if token else ''
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        req = (f'GET /{query} HTTP/1.1\r\n'
               f'Host: {host}:{port}\r\n'
               'Upgrade: websocket\r\nConnection: Upgrade\r\n'
               f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
        try:
            sock = socket.create_connection((host, int(port)), timeout=timeout)
        except OSError:
            return None
        try:
            sock.sendall(req.encode('ascii'))
            data = sock.recv(64)
        except OSError:
            return None
        finally:
            try:
                sock.close()
            except OSError:
                pass
        return data.startswith(b'HTTP/1.1 101') or data.startswith(b'HTTP/1.0 101')

    def snowluma_ws_auth(self):
        """(host, port, accessToken) that actually works with the running SnowLuma.

        Reads ``<launcherPath>/config/onebot_*.json`` candidates (preferring the
        last-logged-in UIN, then the most recently modified file), then **probes**
        each token with a real WebSocket handshake so a stale token left behind by
        a SnowLuma restart never causes a 401. The chosen UIN goes to
        ``self._snowluma_uin``.
        """
        self._snowluma_uin = ''
        cands = self._snowluma_candidates()
        if not cands:
            return None
        for c in cands:
            if self._probe_ws_token(c['host'], c['port'], c['token']):
                self._snowluma_uin = c['uin']
                return (c['host'], c['port'], c['token'])
        # Server not reachable / no token handshook: return the best guess.
        c = cands[0]
        self._snowluma_uin = c['uin']
        return (c['host'], c['port'], c['token'])

    def _build_qq_runtime_config(self) -> Dict[str, Any]:
        qq = self.config.qq
        ep = qq.active()          # per-framework endpoint config
        url = (ep.url or '').strip()
        token = ep.accessToken or ''
        # For SnowLuma, auto-read the port/token from its own config so the user
        # doesn't have to copy the access token by hand (a wrong/empty token is
        # what makes the WS server answer 401).
        if qq.framework == 'snowluma':
            det = self.snowluma_ws_auth()
            if det:
                host, port, det_token = det
                if not url:
                    url = f'ws://{host}:{port}'
                # Prefer the live token from SnowLuma's config: after switching
                # accounts a previously saved token would make the WS answer 401.
                if det_token:
                    token = det_token
                uin = getattr(self, '_snowluma_uin', '')
                if uin:
                    self._post_ui({'kind': 'log', 'level': 'info',
                                   'message': f'[QQ] SnowLuma 账号 UIN={uin}：'
                                              f'已从 config/onebot_{uin}.json 读取端口/token。'})
        return {
            'mode': ep.mode,
            'url': url or 'ws://127.0.0.1:3001',
            'accessToken': token,
            'listenHost': ep.listenHost or '0.0.0.0',
            'listenPort': ep.listenPort,
            'groups': list(qq.groups or []),
            'imageAsLink': bool(qq.imageAsLink),
            'autoReconnect': bool(qq.autoReconnect),
            'healthCheckSeconds': int(qq.healthCheckSeconds),
        }

    def start_qq(self) -> None:
        if self._qq.running:
            # Already connected: push updated settings (e.g. the group filter)
            # without a full reconnect, so editing 「群号」 + 连接 takes effect.
            try:
                self._qq.set_groups(self.config.qq.groups or [])
            except Exception:  # noqa: BLE001
                pass
            return
        self._start_qq()

    def stop_qq(self) -> None:
        self._stop_qq()

    def _load_qq_accounts(self) -> None:
        try:
            with open(QQ_ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._qq_nicknames = {str(k): str(v) for k, v in data.items()}
        except Exception:  # noqa: BLE001
            self._qq_nicknames = {}

    def _save_qq_accounts(self) -> None:
        try:
            with open(QQ_ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._qq_nicknames, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass

    def list_qq_accounts(self) -> List[str]:
        """QQ numbers known locally (NapCat config files + remembered ones)."""
        uins = set(self._qq_nicknames.keys())
        try:
            for name in os.listdir(self._napcat_config_dir()):
                m = re.match(r'(?:napcat|onebot11|napcat_protocol)_(\d+)\.json$', name)
                if m:
                    uins.add(m.group(1))
        except OSError:
            pass
        last = (self.config.qq.lastLoginUin or '').strip()
        if last:
            uins.add(last)
        return sorted(uins)

    def qq_account_choices(self):
        """List of (uin, display_label) for the quick-login picker."""
        choices = []
        for uin in self.list_qq_accounts():
            nick = self._qq_nicknames.get(uin, '')
            label = f'{uin}  {nick}'.rstrip()
            if uin == (self.config.qq.lastLoginUin or '').strip():
                label = '★ ' + label
            choices.append((uin, label))
        return choices

    def start_qq_framework(self, uin: str = '') -> None:
        """Launch the selected OneBot framework (SnowLuma or NapCat)."""
        if self.config.qq.framework == 'napcat':
            self._start_napcat(uin)
        else:
            self._start_snowluma(uin)

    def _resolve_launcher(self, path: str):
        """Return (argv_prefix, cwd) for a folder / .bat / .exe launcher path."""
        if os.path.isdir(path):
            names = ('launcher.bat', 'launcher.cmd', 'launcher.exe',
                     'start.bat', 'start.cmd', 'run.bat', 'launcher.sh')
            target = None
            for name in names:
                cand = os.path.join(path, name)
                if os.path.isfile(cand):
                    target = cand
                    break
            if target is None:
                entries = sorted(os.listdir(path))
                # prefer a launcher/start script
                for name in entries:
                    low = name.lower()
                    if ('launcher' in low or 'start' in low) and low.endswith(
                            ('.bat', '.cmd', '.exe')):
                        target = os.path.join(path, name)
                        break
            if target is None:
                # fall back to the first runnable-looking .exe / .bat
                for name in sorted(os.listdir(path)):
                    low = name.lower()
                    if low.endswith('.exe') and not any(
                            w in low for w in ('uninstall', 'unins', 'update', 'remove')):
                        target = os.path.join(path, name)
                        break
            if target is None:
                for name in sorted(os.listdir(path)):
                    low = name.lower()
                    if low.endswith(('.bat', '.cmd')):
                        target = os.path.join(path, name)
                        break
            if target is None:
                return None, path
        else:
            target = path
        cwd = os.path.dirname(target) or None
        if target.lower().endswith(('.bat', '.cmd')):
            return ['cmd', '/c', target], cwd
        return [target], cwd

    def _start_snowluma(self, uin: str = '') -> None:
        """Launch SnowLuma from the configured path (folder or executable)."""
        path = (self.config.qq.snowluma.launcherPath or '').strip()
        if not path or not os.path.exists(path):
            for cand in (os.path.join(BASE_DIR, 'SnowLuma'),
                         self._detect_running_snowluma_dir()):
                if cand and os.path.exists(cand):
                    path = cand
                    break
        if not path:
            path = os.path.join(BASE_DIR, 'SnowLuma')
        if not os.path.exists(path):
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': f'未找到 SnowLuma 启动路径：{path}'
                                      '（请在 QQ 转述页「启动路径」里填写 SnowLuma 目录或 launcher 文件）。'})
            return
        argv, cwd = self._resolve_launcher(path)
        if not argv:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': f'在 {path} 里找不到 launcher.bat / launcher.exe / start.bat，'
                                      '请直接填写可执行文件路径。'})
            return
        try:
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(argv, cwd=cwd, **kwargs)
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': '已启动 SnowLuma（首次请在它的 WebUI 里完成 QQ 登录与 OneBot 连接配置；'
                                      'OneBot 正向地址默认 ws://127.0.0.1:3001）。'})
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '启动 SnowLuma 失败: ' + str(e)})
            return
        if cwd:
            try:
                if os.name == 'nt':
                    os.startfile(cwd)
            except Exception:  # noqa: BLE001
                pass

    def _napcat_base_dir(self) -> str:
        p = (self.config.qq.napcat.launcherPath or '').strip()
        return p if p else NAPCAT_DIR

    def _napcat_inner_dir(self) -> str:
        base = self._napcat_base_dir()
        inner = os.path.join(base, 'napcat')
        return inner if os.path.isdir(inner) else base

    def _napcat_config_dir(self) -> str:
        for d in (os.path.join(self._napcat_inner_dir(), 'config'),
                  os.path.join(self._napcat_base_dir(), 'config')):
            if os.path.isdir(d):
                return d
        return os.path.join(self._napcat_inner_dir(), 'config')

    def _napcat_cache_dir(self) -> str:
        return os.path.join(self._napcat_inner_dir(), 'cache')

    def _start_napcat(self, uin: str = '') -> None:
        """Launch NapCat from the configured folder (or the bundled copy)."""
        custom = (self.config.qq.napcat.launcherPath or '').strip()
        argv, cwd = None, None
        if custom and os.path.exists(custom):
            argv, cwd = self._resolve_launcher(custom)
        if not argv and os.path.isfile(NAPCAT_LAUNCHER):
            argv, cwd = ['cmd', '/c', NAPCAT_LAUNCHER], NAPCAT_INNER_DIR
        if not argv:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '未找到 NapCat 启动器。请在「QQ 转述 → 网关设置」的「启动路径」'
                                      '填写 NapCat 目录（含 launcher-win10.bat / launcher.bat），'
                                      '或留空使用随项目携带的 NapCat。'})
            return
        uin = str(uin or '').strip()
        if uin:
            argv = list(argv) + ['-q', uin]
            self.config.qq.lastLoginUin = uin
            self.config.save()
        try:
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(argv, cwd=cwd, **kwargs)
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': ('已启动 NapCat 并请求快速登录 ' + uin) if uin
                                      else '已启动 NapCat（会弹窗/UAC 提权，首次请扫码登录）。'})
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '启动 NapCat 失败: ' + str(e)})
            return
        self._open_napcat_cache()

    # Backwards-compatible alias.
    def start_napcat(self, uin: str = '') -> None:
        self.start_qq_framework(uin)

    def _open_napcat_cache(self) -> None:
        try:
            cache = self._napcat_cache_dir()
            os.makedirs(cache, exist_ok=True)
            if os.name == 'nt':
                os.startfile(cache)
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': '已打开 NapCat cache 目录（扫码二维码 qrcode.png 会出现在这里）。'})
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '无法打开 cache 目录: ' + str(e)})

    def _start_qq(self) -> None:
        self._qq_connected = False
        cfg = self.config.qq
        if cfg.imageAsLink:
            try:
                self._public_base_cache = ''
                self._photo.start(cfg.imageHttpHost, cfg.imageHttpPort)
                # Detect the public base in the background: it does several HTTP
                # requests and must never freeze the UI thread (which would make
                # the 「启用」 checkbox un-clickable).
                self._post_ui({'kind': 'log', 'level': 'info',
                               'message': f'图片服务已启动（端口 {cfg.imageHttpPort}），公网地址探测中…'})
                threading.Thread(target=self._log_public_base, daemon=True).start()
            except Exception as e:  # noqa: BLE001
                self._post_ui({'kind': 'log', 'level': 'error',
                               'message': '启动图片服务失败: ' + str(e)})
        try:
            self._qq.start(self._build_qq_runtime_config())
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '启动 QQ 转述失败: ' + str(e)})
            return
        self._post_ui({'kind': 'qqStatus', 'state': 'connecting'})
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': 'QQ 转述已启动（' + _qq_mode_label(self.config.qq) + '）。'})
        # QQ can run without the MC connection, so make sure the event loop
        # (which processes QQ engine events) is alive.
        self._ensure_worker()

    def _log_public_base(self) -> None:
        try:
            base = self._image_base_url()
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'图片链接公网地址：{base}/pic_http/'})
        except Exception:  # noqa: BLE001
            pass

    def _stop_qq(self) -> None:
        was_running = self._qq.running
        try:
            self._qq.stop()
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '停止 QQ 引擎时出错: ' + str(e)})
        try:
            self._photo.stop()
        except Exception:  # noqa: BLE001
            pass
        self._qq_connected = False
        self._qq_last_state = None
        self._post_ui({'kind': 'qqStatus', 'state': 'disconnected'})
        if was_running:
            self._post_ui({'kind': 'log', 'level': 'info', 'message': 'QQ 转述已停止。'})

    def restart_qq(self) -> None:
        """(Re)start the QQ relay with the current settings."""
        self._stop_qq()
        self._start_qq()

    def set_qq_enabled(self, enabled: bool) -> None:
        self.config.qq.enabled = bool(enabled)
        self.config.save()
        try:
            if enabled:
                if not self._qq.running:
                    self._start_qq()
            else:
                self._stop_qq()
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '切换 QQ 转述出错: ' + str(e)})
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': 'QQ 转述已' + ('启用' if enabled else '禁用') + '。'})

    def _on_qq_event(self, ev: Dict[str, Any]) -> None:
        self._qq_events.put(ev)

    def _process_qq_event(self, ev: Dict[str, Any]) -> None:
        event = ev.get('event')
        if event == 'log':
            self._post_ui({'kind': 'log', 'level': ev.get('level', 'info'),
                           'message': '[QQ] ' + ev.get('message', '')})
        elif event == 'error':
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[QQ] ' + ev.get('message', '')})
        elif event == 'status':
            state = ev.get('state')
            self._qq_connected = (state == 'connected')
            self._post_ui({'kind': 'qqStatus', 'state': state,
                           'selfId': ev.get('selfId', '')})
            if state != self._qq_last_state:
                self._qq_last_state = state
                self._post_ui({'kind': 'log', 'level': 'info',
                               'message': '[QQ] ' + ('已连接网关' if self._qq_connected else '已断开')})
        elif event == 'restricted':
            self._qq_connected = False
            self._post_ui({'kind': 'qqStatus', 'state': 'restricted'})
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[QQ] 账号可能受限/离线：' + str(ev.get('message') or '')
                                      + '（已停止自动重连，请登录 NapCat 检查；修复后点「启动/重连 QQ」）'})
        elif event == 'loginInfo':
            uin = str(ev.get('uin') or '').strip()
            nick = str(ev.get('nickname') or '').strip()
            if uin:
                if self._qq_nicknames.get(uin) != nick:
                    self._qq_nicknames[uin] = nick
                    self._save_qq_accounts()
                if nick:
                    self._post_ui({'kind': 'log', 'level': 'info',
                                   'message': f'[QQ] 已登录 {nick}({uin})'})
        elif event == 'health':
            if ev.get('online'):
                self._post_ui({'kind': 'log', 'level': 'info',
                               'message': '[QQ] 在线检测恢复：'
                                          + str(ev.get('nickname') or ev.get('selfId') or '')})
        elif event == 'groups':
            names = ev.get('groups') or []
            if names:
                self._post_ui({'kind': 'log', 'level': 'info',
                               'message': '[QQ] 群列表: ' + ', '.join(
                                   f"{g.get('name')}({g.get('id')})" for g in names)})
        elif event == 'qqMessage':
            self._relay_qq_message(ev)
        elif event == 'end':
            self._qq_connected = False
            self._post_ui({'kind': 'qqStatus', 'state': 'disconnected'})

    def _relay_qq_message(self, ev: Dict[str, Any]) -> None:
        # Direction switch: turning QQ->MC off only stops forwarding; the QQ /
        # OneBot connection (and MC->QQ) keeps running.
        if not self.config.qq.qqToMcEnabled:
            return
        if not self._engine.running:
            # Surface *why* nothing was forwarded instead of silently dropping.
            now = time.time()
            if now - self._qq_relay_warn_at > 30:
                self._qq_relay_warn_at = now
                self._post_ui({'kind': 'log', 'level': 'warn',
                               'message': '[QQ] 收到群消息，但 MC 机器人未连接，无法转发到游戏；'
                                          '请先点顶部「连接」进入服务器。'})
            return
        content = ev.get('content') or ''
        if self.config.qq.imageAsLink and ev.get('images'):
            try:
                content = self._attach_image_links(ev, content)
            except Exception as e:  # noqa: BLE001
                self._post_ui({'kind': 'log', 'level': 'error',
                               'message': '[QQ] 图片处理失败: ' + str(e)})
        text = self._format_qq_message(ev, content=content)
        if not text:
            return
        if not self._engine.say(text):
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[QQ] 转述失败（MC 未连接）。'})
            return
        # Log the relay once: the engine's chatSent echo below is re-tagged as
        # 'qq' when it matches this pending entry (avoids a duplicate line).
        self._qq_pending_relays.append(text)
        if len(self._qq_pending_relays) > 50:
            del self._qq_pending_relays[:len(self._qq_pending_relays) - 50]

    def _format_qq_message(self, ev: Dict[str, Any], content: Optional[str] = None) -> str:
        cfg = self.config.qq
        template = cfg.format or '&7&o[{group}][{name}]：{message}'
        group = _sanitize_text(ev.get('groupName') or ev.get('groupId') or '')
        name = _sanitize_text(ev.get('sender') or '')
        message = _sanitize_text(content if content is not None else (ev.get('content') or ''))
        try:
            text = template.format(group=group, name=name, message=message)
        except (KeyError, IndexError, ValueError):
            text = group + name + message
        text = _sanitize_text(text.replace('\n', ' ').strip())
        limit = max(20, int(cfg.maxLength or 220))
        if len(text) > limit:
            text = text[:limit - 1] + '…'
        return text

    # ------------------------------------------------------------------
    # QQ image -> local HTTP links
    # ------------------------------------------------------------------
    def _image_base_url(self) -> str:
        cfg = self.config.qq
        if cfg.imagePublicBase:
            return cfg.imagePublicBase.rstrip('/')
        if self._public_base_cache:
            return self._public_base_cache
        self._public_base_cache = self._detect_public_base(cfg.imageHttpPort)
        return self._public_base_cache

    def _detect_public_base(self, port: int) -> str:
        fallback = f'http://127.0.0.1:{int(port)}'
        try:
            import httpx
        except Exception:  # noqa: BLE001
            return fallback
        for url in ('https://api.ipify.org', 'https://ifconfig.me/ip',
                    'https://ip.3322.net', 'https://api.ip.sb/ip'):
            try:
                r = httpx.get(url, timeout=4.0, headers={'User-Agent': 'curl/8'})
                ip = (r.text or '').strip().split()[0]
                # basic IPv4/IPv6 sanity
                if ip and len(ip) <= 45 and all(
                        c.isdigit() or c in '.:abcdefABCDEF' for c in ip):
                    return f'http://{ip}:{int(port)}'
            except Exception:  # noqa: BLE001
                continue
        return fallback

    def _attach_image_links(self, ev: Dict[str, Any], content: str) -> str:
        images = ev.get('images') or []
        if not images:
            return content
        base = self._image_base_url()
        out = content
        for candidates in images:
            data = None
            for cand in (candidates or []):
                data = self._fetch_image_bytes(cand)
                if data:
                    break
            if not data:
                continue
            hint = ''
            for cand in (candidates or []):
                hint = str(cand.get('value') or '')
                if hint:
                    break
            name = save_image_bytes(data, hint)
            link = base + '/pic_http/' + name
            out = out.replace('[图片]', '[图片] ' + link, 1)
        return out

    def _fetch_image_bytes(self, cand) -> Optional[bytes]:
        if not isinstance(cand, dict):
            return None
        kind = cand.get('type')
        value = str(cand.get('value') or '')
        if not value:
            return None

        path = None
        if kind == 'path':
            path = value
        elif kind == 'file':
            if re.match(r'^https?://', value, re.IGNORECASE):
                kind = 'url'
            else:
                path = value
        if path:
            p = path
            if p.lower().startswith('file://'):
                p = p[7:]
            p = urllib.parse.unquote(p)
            if re.match(r'^/[A-Za-z]:', p):
                p = p[1:]
            try:
                if os.path.isfile(p):
                    with open(p, 'rb') as f:
                        return f.read()
            except OSError:
                pass
            return None

        if kind == 'url':
            try:
                import httpx
                r = httpx.get(value, timeout=8.0, follow_redirects=True,
                              headers={'User-Agent': 'Mozilla/5.0',
                                       'Referer': 'https://qun.qq.com/'})
                if r.status_code == 200 and r.content:
                    return r.content
            except Exception:  # noqa: BLE001
                return None
        return None

    # ------------------------------------------------------------------
    # MC -> QQ relay ("[sentQ] ..." in public chat)
    # ------------------------------------------------------------------
    def _is_self_name(self, name: str) -> bool:
        """宽松判断某个聊天显示名是不是机器人自己（去掉称号/括号再比）。"""
        if not name or not self._bot_username:
            return False
        bot = str(self._bot_username).strip().lower()
        raw = str(name).strip().lower()
        if raw == bot:
            return True
        cleaned = re.sub(r'[\[【（(][^\]】）)]*[\]】）)]', '', raw).strip()
        return bool(bot) and (cleaned == bot or cleaned.endswith(bot) or raw.endswith(bot))

    def _looks_like_play_text(self, text: str) -> bool:
        """payload 是否像机器人自己发的播放文案（按 playText 的固定文字判断）。"""
        t = (text or '').strip().lower()
        if not t:
            return False
        template = (self.config.song.playText or '{player} 点了一首 {music}').lower()
        for chunk in re.split(r'\{[a-z]+\}', template):
            chunk = chunk.strip()
            if len(chunk) >= 3 and chunk in t:
                return True
        return False

    @staticmethod
    def _song_key(text: str) -> str:
        """歌曲去重用的归一化 key（去掉直链、括号备注、by 尾巴）。"""
        t = re.sub(r'\s*\|\s*.*$', '', str(text or '')).strip().lower()
        t = re.sub(r'[（(][^）)]*[）)]', '', t)
        t = re.sub(r'\bby\b.*$', '', t)
        return t.strip(' -—·\t')

    @staticmethod
    def _trigger_aliases(trigger: str) -> List[str]:
        """触发词的全角写法（``【点歌】`` / ``〔点歌〕`` / ``［点歌］``）。

        帮助说明里用的是这种写法 → 玩家照着打也能点歌（机器人两种都认），
        而客户端 Mod 只认半角 ``[点歌]``，所以**帮助消息不会被 Mod 当成一次点歌**
        （否则每个看帮助的人都会让 Mod 干等 30 秒，再报一句「没找到」）。
        """
        t = str(trigger or '').strip()
        if len(t) < 3 or t[0] != '[' or t[-1] != ']':
            return []
        body = t[1:-1]
        return [lb + body + rb for lb, rb in (('【', '】'), ('〔', '〕'), ('［', '］'))]

    @staticmethod
    def _msg_name(name: str) -> str:
        """玩家名能不能拿来发 ``/msg``（不能就返回空串）。

        顺手挡掉带空格 / 换行 / ``/`` / ``§`` 的名字，避免命令注入。
        """
        n = str(name or '').strip()
        if not n or len(n) > 32:
            return ''
        if any(ch.isspace() or ch in '/\\§' for ch in n):
            return ''
        return n

    def _song_private(self) -> bool:
        """是否「谁点谁听」：播放数据私聊发给点歌的人。"""
        return bool(getattr(self.config.song, 'privatePlay', True))

    def _send_song_line(self, who: str, line: str, is_play_data: bool = False) -> bool:
        """把一条点歌相关消息发出去：谁都听（公屏）或只放给点歌的人（私聊）。

        返回 True 表示是私聊发出去的。**长度按「整条命令」算**：私聊时 ``/msg <名字> ``
        也算进 MC 的 256 上限，超了服务器会直接踢机器人。
        """
        target = self._msg_name(who) if self._song_private() else ''
        if target:
            cmd = f'/msg {target} '
            budget = max(40, 250 - _mc_len(cmd))
            if _mc_len(line) > budget:
                line = _mc_clip(line, budget)   # 兜底截断（正常不会走到这：上面已按 limit 拆好）
            self._remember_sent(cmd + line)
            try:
                if self._engine.say(cmd + line):
                    return True
            except Exception:  # noqa: BLE001
                pass
            # 私聊发不出去（机器人没连上？）→ 退回公屏，至少点歌的人能听到
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'[点歌] 私聊 {target} 失败，改发公屏'})
        self.send_chat(line)
        return False

    # ------------------------------------------------------------------
    # 通用指令：[触发词]帮助 / [触发词]好感度
    # （[触发词] = 机器人 AI 的触发词或点歌触发词，例如 [DiaoHelper]帮助 / [点歌]好感度）
    # ------------------------------------------------------------------
    def _bot_command_words(self) -> List[str]:
        """**提示词**（玩家配置页「触发正则」里配的那些，例如 ``[AI]`` / ``@DiaoHelper``）。

        和点歌的 ``[点歌]`` 不是一回事：``[点歌]xxx`` 是 Mod 的点歌指令，这里只认 AI 提示词。
        只在 AI 一个提示词都没配的时候，才拿点歌触发词兜底（免得一个都用不了）。
        """
        tokens: List[str] = []
        for src in (getattr(self, 'ai_config', None), getattr(self, 'ai_player', None)):
            cfg = getattr(src, 'config', src)
            try:
                raw = str(getattr(cfg, 'triggerRegex', '') or '')
            except Exception:  # noqa: BLE001
                raw = ''
            for part in _split_top_level_commas(raw):
                if part and part not in tokens:
                    tokens.append(part)
        if not tokens:
            t = (self.config.song.trigger or '').strip()
            if t:
                tokens.append(t)
        return tokens

    def _bot_command_patterns(self) -> List[Any]:
        """提示词编译成正则（复用 AI 自己那套规则：``[xxx]`` 当字面量，其余当正则）。"""
        pats: List[Any] = []
        for tok in self._bot_command_words():
            try:
                pats.append(_compile_trigger(tok))
            except Exception:  # noqa: BLE001
                continue
        return pats

    def _bot_trigger_hint(self) -> str:
        """帮助文案里展示的提示词（挑一个像 ``[AI]`` / ``@xxx`` 的）。"""
        tokens = self._bot_command_words()
        for tok in tokens:
            if _BRACKET_LITERAL.fullmatch(tok) or tok.startswith('@'):
                return tok
        return tokens[0] if tokens else '[AI]'

    def _maybe_bot_command(self, player: str, content: str, raw: str = '') -> bool:
        """处理 ``[提示词]帮助`` / ``[提示词]好感度`` / ``[提示词]视频推荐`` / ``[提示词]今日幸运值``。

        返回 True 表示已处理（AI 别再插嘴）。
        """
        text = (raw or content or '').strip()
        if not text:
            return False
        for pat in self._bot_command_patterns():
            try:
                m = pat.search(text)
            except Exception:  # noqa: BLE001
                continue
            if not m:
                continue
            after = text[m.end():].strip().lstrip('：:，,、 ').strip()
            if not after:
                continue
            head = after.split()[0] if after.split() else after
            head_low = head.lower()
            if head_low in ('帮助', 'help', '?', '？', '用法', '说明书', '玩法'):
                self._send_bot_help(player)
                self._post_ui({'kind': 'log', 'level': 'info',
                               'message': f'[帮助] {player} 查看了机器人用法（已私聊）'})
                return True
            if head_low in ('好感度', '好感', '亲密度', 'affinity'):
                target = after[len(head):].strip() or player
                self._reply_affinity(player, target)
                return True
            if head_low in ('视频推荐', '推荐视频', '视频', 'b站', 'bilibili', '看看视频', '追番'):
                self._handle_video_request(player)
                return True
            if head_low in ('今日幸运值', '幸运值', '今日幸运', '幸运', 'lucky'):
                self._reply_lucky(player)
                return True
            if head_low in ('签到', '打卡', 'checkin', 'check-in', '每日签到'):
                self._do_checkin(player)
                return True
        return False

    # ------------------------------------------------------------------
    # 视频推荐（公屏；全服共用冷却，冷却过后再点会**换一批新的**）
    # ------------------------------------------------------------------
    def _misc(self):
        """杂项配置（老 config.json 里没有这段时给个默认的，别炸）。"""
        cfg = getattr(self.config, 'misc', None)
        if cfg is None:
            from .config import MiscConfig
            cfg = MiscConfig()
            self.config.misc = cfg
        return cfg

    def _handle_video_request(self, player: str) -> None:
        cfg = self._misc()
        if not cfg.videoEnabled:
            self.send_chat('【视频推荐】这个功能没开（服主可在「杂项」页打开）')
            return
        if (cfg.videoSource or '') == 'fav' and not (cfg.biliCookie or '').strip():
            self.send_chat('【视频推荐】现在是「我的收藏夹」来源，但还没配 B 站 Cookie，'
                           '服主在「杂项」页填一下 SESSDATA 就能用~')
            return
        now = time.time()
        cool = float(cfg.videoCooldownSeconds)
        last = self._video_last_at
        if last and now - last < cool:
            wait = int(cool - (now - last)) + 1
            self.send_chat(cfg.videoCoolText.replace('{wait}', str(wait))
                           .replace('{player}', player))
            return
        self._video_last_at = now
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': f'[视频推荐] {player} 要推荐（{cfg.source_name()}，正在抓一批新的）'})
        threading.Thread(target=self._video_worker, args=(player,), daemon=True).start()

    def _video_worker(self, player: str) -> None:
        from . import bilibili
        cfg = self._misc()
        source = cfg.videoSource or 'anime'
        # 换收藏夹就当换了个来源，别把上一个夹子的「推过」记录带过来
        seen_key = source if source != 'fav' else 'fav:%d' % int(cfg.biliFavMediaId or 0)
        count = int(cfg.videoCount or 5)
        # 最近推过的 id：换一批用（每个来源各记一份，保留最近 60 条）
        seen: Dict[str, List[str]] = getattr(self, '_video_seen', None) or {}
        self._video_seen = seen
        used = list(seen.get(seen_key) or [])
        items = []
        try:
            items, why = bilibili.recommend_ex(source, count, exclude=used,
                                               cookie=cfg.biliCookie,
                                               media_id=cfg.biliFavMediaId)
        except Exception as e:  # noqa: BLE001
            why = f'{type(e).__name__}: {e}'
            items = []
        if not items:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'[视频推荐] 抓取失败（{cfg.source_name()}）：{why}'})
            self.send_chat(cfg.videoFailText.replace('{player}', player))
            return
        seen[seen_key] = (used + [str(x.get('id')) for x in items])[-60:]
        head = (cfg.videoTitle.replace('{player}', player)
                .replace('{count}', str(len(items)))
                .replace('{source}', cfg.source_name()))
        self.send_chat(head)
        for i, it in enumerate(items, 1):
            line = (cfg.videoItemText
                    .replace('{index}', str(i))
                    .replace('{title}', it.get('title') or '')
                    .replace('{owner}', it.get('owner') or '')
                    .replace('{link}', it.get('link') or ''))
            line = ' '.join(line.split())
            if len(line) > 200:
                line = line[:196] + '… ' + (it.get('link') or '')
            self.send_chat(line)
            time.sleep(0.4)
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': f'[视频推荐] 已在公屏推 {len(items)} 条（{cfg.source_name()}，'
                                  f'{player} 点的）'})

    # ------------------------------------------------------------------
    # 今日幸运值（当天第一次查询时随机定下来，存进 daily.db → 重启不变）
    # ------------------------------------------------------------------
    def _lucky_value(self, player: str, date_str: Optional[str] = None) -> int:
        """取玩家今天的幸运值；库里没有就随机定一个存起来（当天不再变）。"""
        cfg = self._misc()
        mx = max(2, int(cfg.luckMax or 100))
        try:
            return int(self.daily.get_lucky(player, mx, date_str))
        except Exception:  # noqa: BLE001
            import hashlib
            day = date_str or time.strftime('%Y-%m-%d', time.localtime())
            raw = (day + '|' + str(player or '').strip().lower() + '|' + str(mx)).encode('utf-8')
            return int(hashlib.md5(raw).hexdigest(), 16) % mx + 1

    def _reply_lucky(self, player: str) -> None:
        cfg = self._misc()
        if not cfg.luckEnabled:
            self.send_chat('【今日幸运值】这个功能没开（服主可在「杂项」页打开）')
            return
        mx = max(2, int(cfg.luckMax or 100))
        val = self._lucky_value(player)
        pct = val / mx
        if pct >= 0.9:
            tail = '欧皇附体，今天适合开箱/抽卡！'
        elif pct >= 0.7:
            tail = '手气不错，适合挑战一下。'
        elif pct >= 0.4:
            tail = '普普通通的一天，稳扎稳打。'
        elif pct >= 0.2:
            tail = '有点背，别乱冒险。'
        else:
            tail = '今天宜挂机、宜听歌、忌作死。'
        text = (cfg.luckText
                .replace('{player}', player)
                .replace('{value}', str(val))
                .replace('{max}', str(mx))
                .replace('{comment}', tail)
                .replace('{date}', time.strftime('%Y-%m-%d', time.localtime())))
        self.send_chat(text)

    # ------------------------------------------------------------------
    # 每日签到（[提示词]签到）：奖励金钱 / 物品，用配好的两条命令发出去
    # ------------------------------------------------------------------
    def _do_checkin(self, player: str) -> None:
        cfg = self._misc()
        if not cfg.checkinEnabled:
            self.send_chat(cfg.checkinOffText.replace('{player}', player))
            return
        meta = {}

        def reward_plan(streak: int, total: int):
            lo, hi = int(cfg.checkinMoneyMin or 0), int(cfg.checkinMoneyMax or 0)
            money = random.randint(min(lo, hi), max(lo, hi))
            money += int(cfg.checkinStreakBonus or 0) * max(0, streak - 1)
            item, count = (cfg.checkinItem or '').strip(), int(cfg.checkinItemCount or 0)
            if not item or count <= 0:
                item, count = '', 0
            meta.update(money=money, item=item, count=count, streak=streak, total=total)
            plans = []
            for kind, enabled, template in (
                    ('money', money > 0, cfg.checkinMoneyCmd),
                    ('item', bool(item), cfg.checkinItemCmd)):
                if enabled:
                    plans.append(dict(meta, kind=kind, command=self._reward_command(
                        template, player, money, item, count)))
            return plans

        info = self.daily.sign(player, reward_factory=reward_plan)
        if info.get('reason') in ('storage_error', 'invalid_player'):
            self.send_chat(f'【簽到】{player} 本次簽到未能儲存，未發送獎勵，請稍後重試。')
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': f'[簽到] {player} 簽到儲存失敗，已阻止發獎。'})
            return
        if info.get('reason') == 'invalid_reward':
            self.send_chat(f'【簽到】{player} 獎勵命令設定無效，本次未簽到、未發獎，請管理者修正後重試。')
            return
        attempted = False
        try:
            for reward in self.daily.rewards(player, info['day']):
                if reward['state'] != 'pending' or not self.daily.claim_reward(reward):
                    continue
                attempted = True
                command = reward['snapshot']['command']
                try:
                    self._remember_sent(command)
                    result = self._engine.say_result(command)
                    if result.status not in ('submitted', 'not_sent', 'unknown'):
                        result = SendResult('unknown', 'invalid_send_result')
                except Exception as exc:  # noqa: BLE001
                    result = SendResult('unknown', type(exc).__name__)
                self.daily.finish_reward(reward, result.status, result.error)
                self._post_ui({'kind': 'log', 'level': 'info' if result.status == 'submitted' else 'warn',
                               'message': f"[簽到獎勵] {player} {reward['day']} {reward['kind']}："
                                          f"{result.status} {result.error}"})
                if result.status == 'unknown':
                    break
            rewards = self.daily.rewards(player, info['day'])
        except Exception:  # noqa: BLE001
            self.send_chat(f'【簽到】{player} 獎勵紀錄讀寫失敗，已停止發送；請管理者核對，勿盲目補發。')
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': f'[簽到] {player} 獎勵紀錄讀寫失敗，已停止本輪發送。'})
            return
        pending = sum(r['state'] == 'pending' for r in rewards)
        unknown = sum(r['state'] in ('sending', 'unknown') for r in rewards)
        submitted = sum(r['state'] == 'submitted' for r in rewards)
        status = '獎勵命令已提交，實際到帳尚未確認。' if rewards else '本次未設定獎勵。'
        if pending or unknown:
            self.send_chat(f'【簽到】{player} 簽到已記錄；待補送 {pending} 項、已提交 {submitted} 項、'
                           f'結果未確認 {unknown} 項。再次簽到只補送確定未送出的項目；未知結果請管理者核對。')
            return
        if not info.get('ok'):
            if attempted:
                self.send_chat(f'【簽到】{player} 補領處理完成；{status}')
                return
            text = (cfg.checkinRepeatText
                    .replace('{player}', player)
                    .replace('{streak}', str(info.get('streak') or 0))
                    .replace('{total}', str(info.get('total') or 0)))
            self.send_chat(text + (' ' + status if rewards else ''))
            return
        money, item, count = meta['money'], meta['item'], meta['count']
        items_txt = f'{item} x{count}' if item else ''
        text = (cfg.checkinText
                .replace('{player}', player)
                .replace('{money}', str(money))
                .replace('{value}', str(money))
                .replace('{item}', item)
                .replace('{count}', str(count))
                .replace('{items}', f' + {items_txt}' if items_txt else '')
                .replace('{streak}', str(info['streak']))
                .replace('{total}', str(info['total'])))
        self.send_chat(text.replace('{reward_status}', status) if '{reward_status}' in text else text + ' ' + status)

    @staticmethod
    def _reward_command(template: str, player: str, value: int,
                        item: str, count: int) -> str:
        """凍結前驗證命令；不截斷命令後假裝成功。"""
        cmd = (str(template or '')
               .replace('{player}', str(player))
               .replace('{value}', str(value))
               .replace('{money}', str(value))
               .replace('{item}', item or '')
               .replace('{count}', str(count)))
        if '\n' in cmd or '\r' in cmd or re.search(r'\{[A-Za-z_][A-Za-z_0-9]*\}', cmd):
            raise ValueError('Invalid reward command')
        cmd = ' '.join(cmd.split())
        if not cmd or cmd == '/':
            raise ValueError('Empty reward command')
        if not cmd.startswith('/'):
            cmd = '/' + cmd
        if len(cmd.encode('utf-16-le')) // 2 > 250:
            raise ValueError('Reward command too long')
        return cmd

    # ------------------------------------------------------------------
    # 群发私聊：把一句话 /msg 给所有在线玩家（GUI「杂项」页的输入框 + 按钮）
    # ------------------------------------------------------------------
    def refresh_player_list(self, wait: float = 1.2) -> List[str]:
        """请引擎报一份在线名单（连不上/超时就用缓存里上次的）。"""
        try:
            eng = self._engine
            if eng is not None and eng.running:
                self._players_req_at = time.time()
                eng.send({'type': 'listPlayers'})
                deadline = time.time() + max(0.0, float(wait))
                while time.time() < deadline:
                    if self._players_got_at > self._players_req_at:
                        break
                    time.sleep(0.1)
        except Exception:  # noqa: BLE001
            pass
        return list(self._player_list)

    def mass_message(self, text: str, gap: Optional[float] = None,
                     background: bool = True):
        """把所有在线玩家私聊一遍（``/msg <玩家> <内容>``）。

        ``gap`` 是**两个玩家之间的间隔秒数**（默认取杂项页的配置），防止刷太快被踢。
        ``background=True`` 时丢到后台线程，立刻返回 ``-1``（表示已开始排队）。
        """
        text = (text or '').strip()
        if not text:
            return 0
        if self._mass_running:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '[群发] 上一次还没发完，稍等一下再点。'})
            return 0
        if background:
            threading.Thread(target=self._mass_worker, args=(text, gap), daemon=True).start()
            return -1
        return self._mass_worker(text, gap)

    def _mass_worker(self, text: str, gap: Optional[float] = None) -> int:
        cfg = self._misc()
        g = cfg.massGapSeconds if gap is None else gap
        try:
            g = max(0.0, min(10.0, float(g)))
        except (TypeError, ValueError):
            g = 0.5
        self._mass_running = True
        try:
            players = self.refresh_player_list()
            me = (self._bot_username or '').strip().lower()
            names: List[str] = []
            for p in players:
                n = self._msg_name(p)
                if not n:
                    continue
                if cfg.massSkipSelf and me and n.lower() == me:
                    continue
                if n not in names:
                    names.append(n)
            if not names:
                self._post_ui({'kind': 'log', 'level': 'warn',
                               'message': '[群发] 没拿到在线玩家名单（机器人没连上？）'})
                return 0
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'[群发] 开始私聊 {len(names)} 个玩家，间隔 {g:g} 秒：{text}'})
            ok = 0
            for i, name in enumerate(names, 1):
                cmd = f'/msg {name} '
                room = max(20, 250 - _mc_len(cmd))
                body = _mc_clip(text, room)
                self._remember_sent(cmd + body)     # 自触发防线：记下自己发过的话
                try:
                    if self._engine.say(cmd + body):
                        ok += 1
                except Exception:  # noqa: BLE001
                    pass
                if i < len(names) and g > 0:
                    time.sleep(g)
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'[群发] 完成：{ok}/{len(names)} 个玩家'})
            return ok
        finally:
            self._mass_running = False
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': f'[幸运值] {player} 查询：{val}/100'})

    def _send_bot_help(self, player: str) -> None:
        """私聊玩家：机器人用法（游戏内帮助）。

        注意：内容里**不写半角 [点歌]**（用【点歌】代替），否则装了 Mod 的玩家会把这条
        帮助也当成一次点歌（Mod 只认半角写法）。
        """
        name = self._msg_name(player)
        if not name:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'[帮助] 「{player}」不像游戏 ID，帮助没发出去'})
            return
        words_all = self._bot_command_words()
        song_trigger = (self.config.song.trigger or '').strip()
        # ⚠ 帮助文本里绝不能出现点歌触发词（Mod 会把它当成一次点歌），只留 AI 提示词
        words = [w for w in words_all if w and w != song_trigger]
        t = self._bot_trigger_hint()
        if words and t == song_trigger:
            t = '【提示词】'
        trigger_hint = ' / '.join(words[:3]) if words else t
        stop = (self.config.song.stopWord or '停止').strip() or '停止'
        misc = self._misc()
        vcount = int(misc.videoCount or 5)
        vcool = int(misc.videoCooldownSeconds or 30)
        if misc.checkinEnabled:
            reward = '金币'
            if (misc.checkinItem or '').strip() and int(misc.checkinItemCount or 0) > 0:
                reward += f' 和 {misc.checkinItem} x{misc.checkinItemCount}'
            if int(misc.checkinStreakBonus or 0) > 0:
                reward += '（连签更多）'
            checkin_line = f'· 每日签到：{t}签到 —— 每天一次，可领 {reward}'
        else:
            checkin_line = '· 每日签到：暂时关闭中'
        lines = [
            f'你好 {name}！我是服里的 AI 搭子，可以聊天、点歌、查好感度。',
            f'· 找我聊天：想说的话前面带上提示词（{trigger_hint}）',
            '· 点歌：【点歌】歌名 —— 机器人私聊把播放数据发给你（谁点谁听，不吵别人）',
            f'· 停止：【点歌】{stop}；点歌台：【点歌】台（装了 Mod 按 K 也能开）',
            f'· 好感度：{t}好感度 看自己的；{t}好感度 玩家名 看别人的',
            f'· 视频推荐 / 今日幸运值：{t}视频推荐（{misc.source_name()}前 {vcount} 条，'
            f'全服 {vcool} 秒冷却）、{t}今日幸运值（每天固定一个）',
            checkin_line,
            '· 其它：/tpa 玩家、/home、/sethome、/sit、/lay、/spin 直接跟我说就行',
            '· 完整说明见 HELP.md（或问服主）',
        ]
        for line in lines:
            try:
                if not self._engine.say(f'/msg {name} {line}'):
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(0.35)

    def _reply_affinity(self, asker: str, target: str) -> None:
        """回复好感度（公屏，带等级）。"""
        cfg = getattr(self.config, 'affinity', None)
        if cfg is not None and not cfg.enabled:
            self.send_chat('【好感度】这个功能没开（服主可在 config.json 的 affinity 段打开）')
            return
        info = self.affinity.info(target)
        tail = '已经是最高档啦' if info['next'] is None else f"距离下一档还差 {info['toNext']} 点"
        self.send_chat(f"【好感度】{info['name']}：{info['score']} 分"
                       f"（{info['level']}，{info['blurb']}）— {tail}")
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': f"[好感度] {asker} 查询了 {info['name']}："
                                  f"{info['score']}（{info['level']}）"})

    def _on_ai_replied(self, player: Optional[str]) -> None:
        """AI 回了话 → 给这个玩家加好感度（有冷却，防刷屏刷分）。"""
        cfg = getattr(self.config, 'affinity', None)
        if cfg is None or not cfg.enabled or not player:
            return
        before = self.affinity.score(player)
        after = self.affinity.add(str(player), cfg.chatGain, 'chat',
                                  cooldown=cfg.chatCooldownSeconds,
                                  daily_bonus=cfg.dailyGain)
        if after is not None and after != before:
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'[好感度] {player} +{after - before} → {after}'})

    def _send_song_help(self, who: str) -> None:
        """私聊（``/msg``）玩家：点歌触发词的全部用法。跑在后台线程里，一条一条慢慢发。"""
        name = str(who or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_]{1,16}', name):
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'[点歌] 「{who}」不像游戏 ID，帮助私聊没发出去'})
            return
        trigger = (self.config.song.trigger or '[点歌]').strip() or '[点歌]'
        aliases = self._trigger_aliases(trigger)
        t = aliases[0] if aliases else trigger
        stop_word = (self.config.song.stopWord or '停止').strip() or '停止'
        lines = [
            f'点歌用法：聊天里发 {t} 后面跟下面这些词（半角中括号写法也一样）',
            f'{t}歌名 → 点歌，例：{t}起风了（装了 Mod 的人全都能听到）',
            f'{t}{stop_word} → 停止播放并清空播放队列',
            f'{t}台 → 打开点歌台界面（Mod 里按 K 也能开）',
            f'{t}help → 看这条帮助；{t}登录/{t}登出 → 提示去哪登录网易云；'
            f'也可以直接发网易云链接或数字 id，例：{t}1901371647',
            '没装 Mod 听不到：在聊天里发一次点歌，机器人会回你 Mod 下载链接',
        ]
        ok = 0
        for line in lines:
            try:
                if not self._engine.say(f'/msg {name} {line}'):
                    break
            except Exception:  # noqa: BLE001
                break
            ok += 1
            time.sleep(0.4)      # 别刷太快被服务器当刷屏
        self._post_ui({'kind': 'log', 'level': 'song' if ok else 'warn',
                       'message': (f'[点歌] 已私聊 {name} 点歌用法（{ok}/{len(lines)} 条）'
                                   if ok else f'[点歌] 私聊 {name} 失败（机器人没连上？）')})

    @staticmethod
    def _encode_url(url: str) -> str:
        """把直链编码成「公屏链接过滤完全认不出来」的形式（消息里连 http 都不出现）。

        ① 去掉查询串（`?vuutv=…`，实测去掉照样能播，也短一截、不容易超 256 字符上限）；
        ② 去掉协议头，用 {@code ¤}=http、{@code ¥}=https 代替；
        ③ 把 `.` `/` `:` 换成 URL 里绝不会出现的安全字符。
        客户端 Mod 还原（见 SongModClient.extractObfuscatedUrl）。
        """
        u = str(url or '').strip().split('?', 1)[0]
        mark = URL_MARK_HTTP
        if u.startswith('https://'):
            mark, u = URL_MARK_HTTPS, u[len('https://'):]
        elif u.startswith('http://'):
            mark, u = URL_MARK_HTTP, u[len('http://'):]
        return mark + u.replace('.', '·').replace('/', '¦').replace(':', '∶')

    def _maybe_song_reply(self, player: str, content: str, raw: str = '') -> bool:
        """看到聊天里的「点歌」触发词：
        ① 日志页记一条歌曲日志；
        ②（可选）用**机器人的会员 Cookie** 解析出可播放直链，发到公屏（客户端 Mod 播放）；
        ③（可选）回一条 Mod 下载链接（提醒没装的人）。
        返回 True 表示这条消息已被处理。"""
        song = self.config.song
        if self._is_self_name(player):
            return False
        trigger = (song.trigger or '').strip()
        if not trigger:
            return False
        text = raw or content or ''
        # 全角写法也认（【点歌】歌名 / 〔点歌〕歌名 / ［点歌］歌名）——帮助里用的就是这个写法，
        # 玩家直接抄也能点；而客户端 Mod 只认半角 [点歌]，所以帮助消息不会被 Mod 当成点歌。
        if trigger not in text:
            for alias in self._trigger_aliases(trigger):
                if alias and alias in text:
                    text = text.replace(alias, trigger)
                    break
        low = text.lower()
        pos = low.find(trigger.lower())
        if pos < 0:
            return False
        after = text[pos + len(trigger):].strip()
        stop_word = (song.stopWord or '').strip()
        who = player or '?'

        # ⓞ 自触发防线①：这句话是不是我自己刚发出去的（服务器把我自己的话广播回来）
        if self._is_own_echo(text) or self._is_own_echo(after):
            return False
        # ⓞ② 自触发防线②：熔断 —— 同一句话几秒内被反复「点」，就是循环的特征，停一会儿
        loop_key = self._song_key(after)
        now_l = time.time()
        if (loop_key and loop_key == getattr(self, '_song_loop_key', '')
                and now_l - getattr(self, '_song_loop_at', 0.0) < 8):
            self._song_loop_n = getattr(self, '_song_loop_n', 0) + 1
        else:
            self._song_loop_key, self._song_loop_n = loop_key, 1
        self._song_loop_at = now_l
        if getattr(self, '_song_loop_n', 0) >= 3:
            self._song_loop_n = 0
            self._song_loop_until = now_l + 60
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '[点歌] 检测到自触发循环（同一句话连点多次）→ 暂停点歌 60 秒'})
            return False
        if now_l < getattr(self, '_song_loop_until', 0.0):
            return False

        # 停止指令：[点歌]停止 —— **只清这个玩家自己的**（谁点谁听，别把别人的歌关了）
        if stop_word and after.lower() == stop_word.lower():
            self.clear_song_queue(who)
            self._post_ui({'kind': 'log', 'level': 'song',
                           'message': f'[点歌] {who} 停止了点歌'})
            return False
        # 登录/登出等指令：不算点歌，也不回链接
        if after in ('登录', '登出'):
            self._post_ui({'kind': 'log', 'level': 'song',
                           'message': f'[点歌] {who} 执行了「{after}」'})
            return False
        if not after:
            return False
        # 帮助：[点歌]help → /msg 私聊这个玩家，列出所有提示词用法（不进队列、不回下载链接）
        if after.lower() in ('help', 'h', '?', '？', '帮助', '用法', '指令'):
            self._post_ui({'kind': 'log', 'level': 'song',
                           'message': f'[点歌] {who} 看了点歌用法（已私聊 {who}）'})
            threading.Thread(target=self._send_song_help, args=(who,), daemon=True).start()
            return True
        # 「点歌台」是 Mod 的游戏内界面（Mod 自己会开），机器人不当成点歌请求
        if after.lower() in ('台', '界面', 'gui', '菜单', '点歌台'):
            self._post_ui({'kind': 'log', 'level': 'song',
                           'message': f'[点歌] {who} 打开了点歌台界面'})
            return True
        # ① 机器人自己发的播放数据：带音频直链、或带混淆直链标记的，一律不再解析（防自触发死循环）
        if (' | ' in after or URL_MARK_HTTP in after or URL_MARK_HTTPS in after
                or _AUDIO_URL_RE.search(after)):
            return False
        # ② 文案里带播放模板的固定文字（默认「点了一首」）→ 也是机器人自己发的
        if self._looks_like_play_text(after):
            return False
        # ③ 这首歌刚刚发过（多半是同一首歌又被点了一次）：**不再搜一遍**，直接把缓存好的直链
        #    换成新的点歌人重发 —— 快速响应、不等待；链接里还有 `id:` 供 Mod 自己兜底重取。
        posted_key = self._song_key(after)
        recent = self._song_recent.get(posted_key) if posted_key else None
        if recent and (time.time() - recent[0]) < 480:
            # 防死循环（万一服务器把我们的格式全洗掉、自己的消息又被当成点歌）：
            # 同一首歌 20 秒内被「再点」超过 4 次 → 当成回声，直接忽略
            now1 = time.time()
            if now1 - getattr(self, '_replay_window_at', 0.0) > 20:
                self._replay_window_at = now1
                self._replay_window_n = 0
            self._replay_window_n = getattr(self, '_replay_window_n', 0) + 1
            if self._replay_window_n > 4:
                return False
            replay = dict(recent[1])
            replay['who'] = who
            self._post_ui({'kind': 'log', 'level': 'song',
                           'message': f'[点歌] {who} 又点了《{replay.get("title")}》→ '
                                      f'直接复用刚才的直链重播（不再搜索）'})
            if song.postPlayLink:
                self._song_queue.insert(0, replay)   # 插到队首：下一秒就发（配合「立刻插播」）
            # 回下载链接的逻辑照走（让没装 Mod 的人也能看到）
            if not song.replyEnabled:
                return True
            now = time.time()
            if now - self._song_reply_at.get(who, 0.0) < max(0, int(song.replyCooldownSeconds)):
                return True
            self._song_reply_at[who] = now
            if song.modUrl:
                self._send_song_line(who, song.build_reply())
            return True
        # ④ 突发限流：20 秒内最多处理 12 次点歌（防止任何形式的刷屏/循环）
        now0 = time.time()
        if now0 - getattr(self, '_song_burst_at', 0.0) > 20:
            self._song_burst_at = now0
            self._song_burst_n = 0
        self._song_burst_n = getattr(self, '_song_burst_n', 0) + 1
        if self._song_burst_n > 12:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '[点歌] 短时间点歌太多，已忽略这条（防刷屏）'})
            return False

        # 每次点歌记一条日志
        self._post_ui({'kind': 'log', 'level': 'song',
                       'message': f'[点歌] {who} 点了歌：{after}'})

        # ② 解析后加入播放队列（网络操作放后台线程）
        if song.postPlayLink:
            self.enqueue_song(after, who, 'player')

        # ③ 回一条 Mod 下载链接
        if not song.replyEnabled:
            return True
        now = time.time()
        if now - self._song_reply_at.get(who, 0.0) < max(0, int(song.replyCooldownSeconds)):
            return True
        self._song_reply_at[who] = now
        if not song.modUrl:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '[点歌] 「点歌」页还没填 Mod 下载链接，无法回复给需要的人。'})
            return True
        private = self._send_song_line(who, song.build_reply())
        self._post_ui({'kind': 'log', 'level': 'song',
                       'message': f"[点歌] 已{'私聊 ' + who if private else '公屏回复'} Mod 下载链接"})
        return True

    # ------------------------------------------------------------------
    # 点歌播放队列（机器人真队列）
    # ------------------------------------------------------------------
    def song_state(self) -> Dict[str, Any]:
        """给 GUI 用：当前播放 + 队列快照。"""
        return {
            'current': dict(self._song_current) if self._song_current else None,
            'queue': [dict(e) for e in self._song_queue],
        }

    def enqueue_song(self, query: str, who: str = '我', source: str = 'gui') -> None:
        """把一次点歌解析后加入播放队列（解析在后台线程做）。"""
        query = (query or '').strip()
        if not query:
            return
        self._post_ui({'kind': 'log', 'level': 'song',
                       'message': f'[点歌] {who} 点了歌：{query}（加入队列）'})
        threading.Thread(target=self._resolve_into_queue,
                         args=(query, who, source), daemon=True).start()

    def _resolve_into_queue(self, query: str, who: str, source: str) -> None:
        cookie = self.config.song.neteaseCookie
        try:
            from . import netease
            info = netease.resolve(query, cookie)
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'[点歌] 解析「{query}」失败：{e}'})
            return
        if not info:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'[点歌] 没找到「{query}」'})
            return
        title = info.get('title') or query
        artists = info.get('artists') or []
        artist = '/'.join([str(a) for a in artists]) if artists else '未知歌手'
        if not info.get('url'):
            note = info.get('note') or '无法获取播放地址'
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'[点歌] {title} - {artist}：{note}'})
            return
        self._song_seq += 1
        entry = {
            'seq': self._song_seq,
            'who': who,
            'source': source,
            'query': query,
            'title': title,
            'artist': artist,
            'url': info['url'],
            'id': str(info.get('id') or ''),
            'duration': int(info.get('duration') or 0),
            'trial': int(info.get('trial') or 0),
            'added_at': time.time(),
        }
        self._song_queue.append(entry)
        self._post_ui({'kind': 'log', 'level': 'song',
                       'message': f'[点歌] 已加入队列：{title} - {artist}'
                                  f'（队列 {len(self._song_queue)} 首）'})
        # 好感度：点歌成功 +N（只有真实玩家名才算，GUI 里的「我」不算）
        try:
            cfg = getattr(self.config, 'affinity', None)
            if cfg is not None and cfg.enabled and cfg.songGain > 0 and self._msg_name(who):
                after = self.affinity.add(str(who), cfg.songGain, 'song',
                                          cooldown=60, daily_bonus=cfg.dailyGain)
                if after is not None:
                    self._post_ui({'kind': 'log', 'level': 'info',
                                   'message': f'[好感度] {who} 点歌 +{cfg.songGain} → {after}'})
        except Exception:  # noqa: BLE001
            pass

    def enqueue_existing(self, entry: Dict[str, Any], who: str = '我', source: str = 'gui') -> None:
        """把队列里已有的某首（别人的）歌，按原样再加进队列（不再解析）。"""
        url = entry.get('url')
        if not url:
            return
        self._song_seq += 1
        item = {
            'seq': self._song_seq,
            'who': who,
            'source': source,
            'query': entry.get('query') or entry.get('title') or '',
            'title': entry.get('title') or '',
            'artist': entry.get('artist') or '',
            'url': url,
            'id': str(entry.get('id') or ''),
            'duration': int(entry.get('duration') or 0),
            'trial': int(entry.get('trial') or 0),
            'added_at': time.time(),
        }
        self._song_queue.append(item)
        self._post_ui({'kind': 'log', 'level': 'song',
                       'message': f"[点歌] {who} 也要听：{item['title']} - {item['artist']}"
                                  f"（队列 {len(self._song_queue)} 首）"})

    def clear_song_queue(self, who: str = '') -> None:
        """停止/清空播放队列。

        带 ``who`` 时**只清这个玩家自己的** —— 「谁点谁听」下这是必须的：
        A 发 ``[点歌]停止`` 不该把 B 正在听的歌也关了。不带 ``who`` 就是全清（GUI 的「清空队列」用）。
        """
        target = str(who or '').strip().lower()
        if target:
            before = len(self._song_queue)
            self._song_queue[:] = [
                e for e in self._song_queue
                if str(e.get('who') or '').strip().lower() != target
            ]
            removed = before - len(self._song_queue)
            mine_current = (self._song_current is not None
                            and str(self._song_current.get('who') or '').strip().lower() == target)
            if mine_current:
                self._song_current = None
                self._song_next_at = 0.0
            self._post_ui({'kind': 'log', 'level': 'song',
                           'message': f'[点歌] {who} 停止：只清了他/她自己的 {removed} 首'
                                      f'{"（含正在放的那首）" if mine_current else ""}，'
                                      f'别人的 {len(self._song_queue)} 首不动'})
            return
        n = len(self._song_queue)
        self._song_queue.clear()
        self._song_current = None
        self._song_next_at = 0.0
        self._post_ui({'kind': 'log', 'level': 'song',
                       'message': f'[点歌] 已清空播放队列（{n} 首）'})

    def _maybe_daily_rotate(self) -> None:
        """跨天检查：daily.db 备份 → 删除 → 重建（幸运值 / 签到按天重置）。"""
        try:
            info = self.daily.ensure_day()
        except Exception:  # noqa: BLE001
            return
        if info.get('rotated'):
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': '新的一天：每日数据已重置，旧库备份到 '
                                      + str(info.get('backup') or 'db_backup/')})

    def _song_tick(self) -> None:
        """每秒调用：当前放完就播下一首；开了「立刻插播」就新歌一到马上放（打断当前这首）。"""
        now = time.time()
        if self._song_current is not None:
            if now < self._song_next_at:
                # 当前这首还没放完。没排队的就继续等；有排队的且开了「立刻插播」就马上换歌。
                instant = bool(getattr(self.config.song, 'instantPlay', True))
                if not self._song_queue or not instant:
                    return
                self._post_ui({'kind': 'log', 'level': 'song',
                               'message': f"[点歌] 新点歌立刻插播，打断："
                                          f"{self._song_current.get('title')}"})
            else:
                self._post_ui({'kind': 'log', 'level': 'song',
                               'message': f"[点歌] 播完：{self._song_current.get('title')}"})
            self._song_current = None
        if not self._song_queue:
            return
        entry = self._song_queue.pop(0)
        self._post_song_entry(entry, now)

    def _post_song_entry(self, entry: Dict[str, Any], now: Optional[float] = None) -> None:
        """把一条点歌发出去（客户端 Mod 播放），并记账（防自触发 + 供重复点歌复用）。

        「谁点谁听」（``song.privatePlay`` 默认开）时播放数据用 ``/msg`` **私聊**发给点歌的人，
        公屏上不出现 —— 其他玩家的 Mod 不会跟着响。
        """
        now = time.time() if now is None else now
        trigger = (self.config.song.trigger or '[点歌]').strip() or '[点歌]'
        music = f"{entry['title']} - {entry['artist']}"
        trial = int(entry.get('trial') or 0)
        if trial:
            music += (f'（试听{trial}s，机器人未登录会员）' if trial > 0 else '（试听片段）')
        template = (self.config.song.playText or '{player} 点了一首 {music}')
        text = (template.replace('{player}', str(entry.get('who') or '?'))
                .replace('{music}', music)
                .replace('{title}', str(entry.get('title')))
                .replace('{artist}', str(entry.get('artist'))))
        # 公屏会把 http(s):// 链接整条替换成「[链接]」，所以发**混淆后的直链**（Mod 侧还原）。
        enc = self._encode_url(entry['url'])
        title = str(entry.get('title') or '')
        # 再附一个歌曲 id（第二道保险）：万一服务器还动了这条消息，Mod 可以自己用 id 取链。
        # ⚠ 不能用 `#`（会被服务器当十六进制颜色码吃掉，`#3399839173` → `9173`）、也不能用 `&`（MC 颜色符）。
        sid = str(entry.get('id') or '')
        idtag = f' id:{sid}' if sid else ''
        who = str(entry.get('who') or '')
        # 私聊时整条要发的是命令 `/msg <名字> <内容>` —— MC 单包上限 256，命令名+名字也算长度！
        # （之前没算：文案长一点就直接超限，服务器反手把机器人踢了）
        target = self._msg_name(who) if self._song_private() else ''
        limit = 250 - (_mc_len(f'/msg {target} ') if target else 0)
        limit = max(80, limit)

        def build(title_text: str, with_enc: bool, with_id: bool) -> str:
            parts = [f'{trigger}{title_text} | ']
            if with_enc:
                parts.append(enc)
            if with_id and idtag:
                parts.append(idtag)
            return ''.join(parts).strip()

        line = build(text, True, True)
        if _mc_len(line) > limit:
            # 太长：文案单独发一条（**不带触发词**，Mod 忽略、玩家看得见）
            try:
                self._send_song_line(who, _mc_clip(text, limit))
            except Exception:  # noqa: BLE001
                pass
            line = build(title, True, True)
        if _mc_len(line) > limit:
            line = build(title, True, False)          # 丢掉 id 标记
        if _mc_len(line) > limit:
            keep = max(0, limit - _mc_len(trigger) - _mc_len(enc) - 3)
            line = build(_mc_clip(title, keep), True, False)   # 缩歌名
        if _mc_len(line) > limit:
            # 直链实在太长（罕见）：只发 id，Mod 用自己的 Cookie 取链（没填 Cookie 就是试听）
            line = build(title, False, True)
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '[点歌] 直链太长发不出去，已改成只发歌曲 id（Mod 自己取链）'})
        private = self._send_song_line(who, line, is_play_data=True)
        # 记下刚发过的这首歌：① 防止自己的消息被再次当成点歌（循环）；② 重复点时直接复用
        try:
            snapshot = dict(entry)
            # 同一首歌的多种写法都登记（歌名 / 歌名-歌手 / 渲染后的文案 / 玩家当初打的关键词），
            # 这样「又点同一首」时无论怎么写都能命中缓存 → 不再重新搜索
            alias_keys = [
                self._song_key(music),
                self._song_key(text),
                self._song_key(title),
                self._song_key(f"{title} {entry.get('artist') or ''}"),
                self._song_key(entry.get('query') or ''),
            ]
            for key in alias_keys:
                if key:
                    self._song_posted_at[key] = now
                    self._song_recent[key] = (now, snapshot)
        except Exception:  # noqa: BLE001
            pass
        self._song_current = entry
        duration = int(entry.get('duration') or 0)
        if duration <= 0:
            duration = 180000
        self._song_next_at = now + duration / 1000.0 + 2.0
        self._post_ui({'kind': 'log', 'level': 'song',
                       'message': f"[点歌] {'已私聊 ' + who if private else '已发公屏'}："
                                  f"{entry['title']} - {entry['artist']}"
                                  f"（{int(duration / 1000)}s，队列剩 {len(self._song_queue)} 首）"})

    def _maybe_relay_mc_to_qq(self, player: str, content: str) -> bool:
        """If an in-game message starts with the trigger, forward it to a QQ
        group. Returns True when the message was consumed (AI should skip it)."""
        cfg = self.config.qq
        if not cfg.mcToQqEnabled:
            return False
        text = (content or '').strip()
        if not text:
            return False
        matched = self._match_mc_trigger(text, cfg.mcTrigger or '[sentQ]')
        if matched is None:
            return False
        target, body = matched
        body = body.strip()
        if not body:
            self._send_mc_feedback(player, False)
            return True
        if not self._qq.running:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '[MC→QQ] QQ 未连接，忽略：' + text})
            self._send_mc_feedback(player, False)
            return True
        group_id = target or cfg.mcTargetGroup or (cfg.groups[0] if cfg.groups else '')
        if not group_id:
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '[MC→QQ] 未配置目标群，忽略：' + text})
            self._send_mc_feedback(player, False)
            return True
        now = time.time()
        cooldown = int(cfg.mcCooldownSeconds or 0)
        if cooldown > 0 and now - self._last_mc_to_qq_time < cooldown:
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': '[MC→QQ] 冷却中，忽略。'})
            self._send_mc_feedback(player, False)
            return True
        out = self._format_mc_to_qq(player, body)
        if not out:
            self._send_mc_feedback(player, False)
            return True
        if self._qq.send_group(group_id, out):
            self._last_mc_to_qq_time = now
            self._post_ui({'kind': 'log', 'level': 'qq',
                           'message': f'[MC→QQ] {group_id}: {out}'})
            self._send_mc_feedback(player, True)
        else:
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '[MC→QQ] 发送失败。'})
            self._send_mc_feedback(player, False)
        return True

    def _send_mc_feedback(self, player: str, ok: bool) -> None:
        """Say a configurable success/failure line in MC public chat."""
        cfg = self.config.qq
        tmpl = cfg.mcSuccessMsg if ok else cfg.mcFailureMsg
        if not tmpl or not self._engine.running:
            return
        who = _sanitize_text(player or '')
        try:
            text = tmpl.format(player=who)
        except (KeyError, IndexError, ValueError):
            text = tmpl.replace('{player}', who)
        text = _sanitize_text(text.replace('\n', ' ').strip())
        if text:
            self._engine.say(text)

    def _match_mc_trigger(self, text: str, triggers: str):
        """Return (target_group_or_None, body) or None when not a trigger.

        ``triggers`` may hold several comma-separated words (``[sentQ],[MC]``);
        the message is relayed when it starts with any of them. Each trigger
        accepts ``[sentQ] 内容`` and ``[sentQ:群号] 内容`` (bracketed form) as
        well as a plain prefix without brackets.
        """
        parts = [p.strip() for p in re.split(r'[,，]', str(triggers or '')) if p.strip()]
        if not parts:
            parts = ['[sentQ]']
        for trig in parts:
            matched = self._match_one_trigger(text, trig)
            if matched is not None:
                return matched
        return None

    def _match_one_trigger(self, text: str, trig: str):
        if text.startswith('['):
            end = text.find(']')
            if end > 0:
                token = text[1:end].strip()
                base, grp = token, None
                if ':' in token or '：' in token:
                    sep = ':' if ':' in token else '：'
                    base, grp = token.split(sep, 1)
                    base, grp = base.strip(), grp.strip()
                trig_base = (trig or '').strip()
                if trig_base.startswith('[') and trig_base.endswith(']'):
                    trig_base = trig_base[1:-1].strip()
                if base and base.lower() == trig_base.lower():
                    return (grp or None, text[end + 1:].strip())
        if trig and text.lower().startswith(trig.lower()):
            return (None, text[len(trig):].strip(' :：'))
        return None

    def _format_mc_to_qq(self, player: str, message: str) -> str:
        cfg = self.config.qq
        who = _sanitize_text(_strip_mc_codes(player or ''))
        body = _sanitize_text(_strip_mc_codes(message or ''))
        template = cfg.mcFormat or '[MC] {player}：{message}'
        try:
            out = template.format(player=who, message=body)
        except (KeyError, IndexError, ValueError):
            out = f'[MC] {who}：{body}'
        out = _sanitize_text(_strip_mc_codes(out.replace('\n', ' ').strip()))
        if len(out) > 500:
            out = out[:499] + '…'
        return out

    # ------------------------------------------------------------------
    # Low-health alert
    # ------------------------------------------------------------------
    def _maybe_health_alert(self, health) -> None:
        cfg = self.config.healthAlert
        if not cfg.enabled or health is None:
            return
        try:
            hp = float(health)
        except (TypeError, ValueError):
            return
        if hp > int(cfg.threshold):
            return
        text = (cfg.message or '').strip()
        if not text:
            return
        now = time.time()
        cooldown = int(cfg.cooldownSeconds or 0)
        if cooldown > 0 and now - self._last_health_alert < cooldown:
            return
        if self._engine.say(text):
            self._last_health_alert = now
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'[血量] {hp:.0f} ≤ {int(cfg.threshold)}，已发送提醒。'})

    # ------------------------------------------------------------------
    # Roster (local "who is this player" table)
    # ------------------------------------------------------------------
    def _load_roster_files(self) -> None:
        paths = list(self.config.roster.files or [])
        self.roster.clear()
        for err in self.roster.load_files(paths):
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': '[玩家资料库] ' + err})
        # Drop entries whose file no longer exists so the GUI list stays clean.
        kept = [p for p in paths if os.path.exists(p)]
        if len(kept) != len(paths):
            self.config.roster.files = kept
            self.config.save()
        self.roster.enabled = self.config.roster.enabled
        self.roster.personality_enabled = self.config.roster.personalityEnabled
        self.roster.alias_enabled = self.config.roster.aliasEnabled
        self.roster.research_interval_ms = max(
            30, self.config.roster.researchIntervalSeconds) * 1000
        self.roster.research_min_messages = max(
            1, self.config.roster.researchMinMessages)
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': f'玩家资料库: {self.roster.total} 条记录。'})

    def roster_set_personality(self, enabled: bool, interval_seconds: int) -> None:
        self.config.roster.personalityEnabled = bool(enabled)
        self.config.roster.researchIntervalSeconds = max(30, int(interval_seconds or 180))
        self.config.save()
        self.roster.personality_enabled = self.config.roster.personalityEnabled
        self.roster.research_interval_ms = self.config.roster.researchIntervalSeconds * 1000
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': 'AI 性格研究已' + ('启用' if enabled else '禁用') + '。'})

    def sync_roster_runtime(self) -> None:
        """Push current config.roster values into the live Roster object."""
        self.roster.enabled = self.config.roster.enabled
        self.roster.personality_enabled = self.config.roster.personalityEnabled
        self.roster.alias_enabled = self.config.roster.aliasEnabled
        self.roster.research_interval_ms = max(
            30, self.config.roster.researchIntervalSeconds) * 1000
        self.roster.research_min_messages = max(
            1, self.config.roster.researchMinMessages)

    def roster_set_alias(self, enabled: bool) -> None:
        self.config.roster.aliasEnabled = bool(enabled)
        self.config.save()
        self.roster.alias_enabled = self.config.roster.aliasEnabled
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': 'AI 别名分析已' + ('启用' if enabled else '禁用') + '。'})

    def export_roster_template(self, path: str):
        from .roster import make_template
        try:
            make_template(path)
            return True, '模板已导出。'
        except RosterError as e:
            return False, str(e)

    def roster_set_enabled(self, enabled: bool) -> None:
        self.config.roster.enabled = bool(enabled)
        self.config.save()
        self.roster.enabled = self.config.roster.enabled
        self._post_ui({'kind': 'log', 'level': 'info',
                       'message': '玩家身份问答已' + ('启用' if enabled else '禁用') + '。'})

    def roster_add_file(self, path: str):
        path = os.path.abspath(path)
        if not os.path.exists(path):
            return False, '文件不存在。'
        files = self.config.roster.files
        if any(os.path.normcase(os.path.abspath(f)) == os.path.normcase(path)
               for f in files):
            return False, '该文件已在资料库中。'
        try:
            count = self.roster.add_file(path)
        except RosterError as e:
            return False, str(e)
        files.append(path)
        self.config.save()
        return True, f'已导入 {count} 条记录。'

    def roster_remove_file(self, path: str) -> bool:
        files = self.config.roster.files
        match = next((f for f in files
                      if os.path.normcase(os.path.abspath(f)) == os.path.normcase(
                          os.path.abspath(path))), None)
        if match is None:
            return False
        files.remove(match)
        self.roster.remove_source(match)
        self.config.save()
        return True

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
        # Resolve the same OpenAI-compatible endpoint the AI player uses, so the
        # quiz answers with the global AI config (base URL auto-appended path).
        provider = (self.ai_config.provider or cfg.llm.provider or 'openai').lower()
        base = (self.ai_config.baseUrl or cfg.llm.baseUrl or '').strip()
        if not base:
            base = DEFAULT_BASE_URLS.get(provider, '')
        llm = {
            'endpoint': _resolve_llm_endpoint(provider, base),
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
            'verify': {'enabled': cfg.verify.enabled,
                       'titleKeyword': cfg.verify.titleKeyword,
                       'nameKeyword': cfg.verify.nameKeyword,
                       'minMajority': cfg.verify.minMajority,
                       'maxTargets': cfg.verify.maxTargets,
                       'clickDelayMs': cfg.verify.clickDelayMs,
                       'startDelayMs': cfg.verify.startDelayMs,
                       'debug': cfg.verify.debug},
        }

    # ------------------------------------------------------------------
    # Bot event handling
    # ------------------------------------------------------------------
    def _on_bot_event(self, ev: Dict[str, Any]) -> None:
        self._bot_events.put(ev)

    def _on_ai_send_chat(self, text: str) -> None:
        blocked = self._blocked_command(text)
        if blocked:
            cfg = self.config.commandBlock
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'已屏蔽危险命令：/{blocked}（防注入）'})
            note = (cfg.message or '').strip()
            if note:
                who = getattr(self.ai_player, '_current_player', None) or '未知'
                try:
                    note = note.format(player=who, command=blocked, cmd=blocked)
                except (KeyError, IndexError, ValueError):
                    note = (note.replace('{player}', who)
                            .replace('{command}', blocked).replace('{cmd}', blocked))
                self._engine.say(note)
            return
        if not self._engine.say(text):
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': 'AI 回复发送失败（未连接）。'})

    def _blocked_command(self, text: str) -> str:
        """Return the blocked command entry if ``text`` starts with one.

        Matches the command *path*, so multi-word entries work too:
        entry ``land`` blocks ``/land create ...``; entry ``land create``
        blocks ``/land create ...`` but not ``/land claim``.
        """
        cfg = self.config.commandBlock
        if not cfg.enabled:
            return ''
        s = (text or '').strip()
        if not s.startswith('/'):
            return ''
        rest = s[1:].strip().lower()
        if not rest:
            return ''
        for entry in (cfg.commands or []):
            e = str(entry).strip().lower().lstrip('/').strip()
            if not e:
                continue
            if rest == e or rest.startswith(e + ' '):
                return e
        return ''

    def _worker_loop(self) -> None:
        last_tick = time.time()
        while self._running or self._qq.running:
            try:
                ev = self._bot_events.get(timeout=0.3)
            except queue.Empty:
                ev = None
            if ev is not None:
                try:
                    self._process_bot_event(ev)
                except Exception as e:  # noqa: BLE001
                    self._post_ui({'kind': 'log', 'level': 'error',
                                   'message': '处理游戏事件出错: ' + str(e)})
            while True:
                try:
                    qev = self._qq_events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._process_qq_event(qev)
                except Exception as e:  # noqa: BLE001
                    self._post_ui({'kind': 'log', 'level': 'error',
                                   'message': '处理 QQ 事件出错: ' + str(e)})
            now = time.time()
            if self._reconnect_at is not None and now >= self._reconnect_at:
                self._run_reconnect()
            if self._lobby_at is not None and now >= self._lobby_at:
                self._run_kick_lobby()
            if now - last_tick >= 1.0:
                self._maybe_proactive_reconnect()
                self._maybe_auto_lobby()
                self._maybe_online_check()
                self._maybe_daily_rotate()
                try:
                    self._song_tick()
                except Exception:  # noqa: BLE001
                    pass
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
            # raw system chat; the parsed forms are handled below
            pass
        elif event == 'serverMessage':
            raw = ev.get('raw') or ''
            if raw.strip():
                self._post_ui({'kind': 'log', 'level': 'server', 'message': raw})
                if self.ai_player:
                    try:
                        self.ai_player.on_server_message(raw)
                    except Exception:  # noqa: BLE001
                        pass
                self._check_reconnect_triggers(raw)
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
            if not private and self._check_reconnect_triggers(raw or content):
                return
            if not private and self._maybe_relay_mc_to_qq(player, content):
                return
            if self._maybe_bot_command(player, content, raw):
                return
            if not private and self._maybe_song_reply(player, content, raw):
                return
            if self.ai_player:
                try:
                    self.ai_player.on_player_message(player, content, raw=raw)
                except Exception as e:  # noqa: BLE001
                    self._post_ui({'kind': 'log', 'level': 'error',
                                   'message': 'AI 处理消息出错: ' + str(e)})
        elif event == 'actionBar':
            self._post_ui({'kind': 'actionBar', 'text': ev.get('text', '')})
            self._check_reconnect_triggers(ev.get('text', ''))
        elif event == 'health':
            self._post_ui({'kind': 'health', 'health': ev.get('health'),
                           'food': ev.get('food')})
            self._maybe_health_alert(ev.get('health'))
        elif event == 'spawn':
            self._connected = True
            self._connecting = False
            self._reconnect_attempts = 0
            self._reconnect_at = None
            self._keep_retry_deadline = None
            self._service_outage = None
            self._service_error_count = 0
            username = ev.get('username') or ''
            if username:
                self._bot_username = username
                if self.ai_player:
                    self.ai_player.set_bot_username(username)
                    self.ai_player.set_online(True)
            players = ev.get('players') or []
            self._player_list = [str(p) for p in players if str(p or '').strip()]
            self._players_got_at = time.time()
            self._post_ui({'kind': 'status', 'text': f'已连接（{username}）'})
            self._post_ui({'kind': 'spawn', 'username': username, 'players': players})
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'已进入服务器，在线玩家: {", ".join(players) or "无"}'})
            self._schedule_kick_lobby()
            if self.config.reconnect.proactiveEnabled:
                self._proactive_at = (time.time()
                                      + max(1, self.config.reconnect.proactiveIntervalSeconds))
            else:
                self._proactive_at = None
            self._auto_lobby_at = None
            self._online_next_check = None
            self._online_retry_at = None
        elif event == 'players':
            # 引擎回的在线名单（群发私信用；也顺手给 UI 更新一下）
            players = ev.get('players') or []
            self._player_list = [str(p) for p in players if str(p or '').strip()]
            self._players_got_at = time.time()
            self._post_ui({'kind': 'players', 'players': list(self._player_list)})
        elif event == 'kicked':
            was_connected = self._connected
            self._connected = False
            self._connecting = False
            if self.ai_player:
                self.ai_player.set_online(False)
            self._post_ui({'kind': 'status', 'text': '被踢出服务器'})
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '被踢出: ' + ev.get('reason', '')})
            relogin = self._maybe_request_relogin(ev.get('reason', ''))
            if not relogin:
                self._schedule_reconnect(kicked=was_connected)
        elif event == 'serviceError':
            kind = ev.get('kind') or 'unknown'
            detail = ev.get('detail') or ''
            friendly = ev.get('message') or '连接服务出错'
            self._service_outage = kind
            self._service_error_count += 1
            now = time.time()
            # 首次、或每 60 秒提示一次，避免刷屏。
            if self._service_error_count == 1 or now - self._last_service_error_log >= 60:
                self._last_service_error_log = now
                extra = f'（{detail}）' if detail else ''
                self._post_ui({'kind': 'log', 'level': 'warn',
                               'message': f'{friendly}{extra}。已进入退避重试，不会一直刷屏；'
                                          f'等对方恢复后会自动连上。'})
        elif event == 'error':
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': 'Bot 错误: ' + ev.get('message', '')})
            self._maybe_request_relogin(ev.get('message', ''))
        elif event == 'end':
            self._connected = False
            self._connecting = False
            if self.ai_player:
                self.ai_player.set_online(False)
            reason = ev.get('reason') or ''
            self._post_ui({'kind': 'status', 'text': '连接已结束'})
            self._post_ui({'kind': 'disconnected', 'reason': reason})
            if not self._manual_disconnect and reason != 'quit':
                self._schedule_reconnect(kicked=False)
        elif event == 'chatSent':
            text = ev.get('text', '')
            self._remember_sent(text)      # 自触发防线：记下自己发过的话
            if text in self._qq_pending_relays:
                self._qq_pending_relays.remove(text)
                self._post_ui({'kind': 'log', 'level': 'qq', 'message': '» ' + text})
            else:
                self._post_ui({'kind': 'log', 'level': 'sent', 'message': '» ' + text})
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
        elif event == 'bookProgress':
            self.save_book_progress(
                nextPage=int(ev.get('nextPage', 0)),
                nextBook=int(ev.get('nextBook', 1)),
                updated=time.strftime('%Y-%m-%d %H:%M:%S'))
            prog = self.load_book_progress()
            self._post_ui({'kind': 'bookProgress',
                           'nextPage': int(ev.get('nextPage', 0)),
                           'nextBook': int(ev.get('nextBook', 1)),
                           'done': int(ev.get('done', 0)),
                           'total': int(ev.get('total', 0)),
                           'totalPages': int(prog.get('totalPages', 0) or 0)})
        elif event == 'bookResult':
            ok = bool(ev.get('ok'))
            self._post_ui({'kind': 'log', 'level': 'sent' if ok else 'error',
                           'message': '[书与笔] ' + str(ev.get('message') or '')})

    def _maybe_request_relogin(self, text: str) -> bool:
        """When a LittleSkin session appears invalid, ask the GUI to re-login once.

        Returns True when a re-login was requested (the GUI drives the reconnect),
        so the caller can skip the generic auto-reconnect.
        """
        if self._relogin_triggered:
            return False
        if self.config.auth.type != 'littleskin':
            return False
        if not _is_auth_failure(text):
            return False
        self._relogin_triggered = True
        self._post_ui({'kind': 'reloginNeeded', 'message': str(text or '')})
        return True

    def _post_ui(self, ev: Dict[str, Any]) -> None:
        if isinstance(ev, dict) and ev.get('kind') == 'log':
            # 机器人自己写的、带触发词的日志 → 也记成「自己说过的话」：
            # 万一它被转到游戏里（日志转发/AI 复述），服务器广播回来时就不会再当成新点歌。
            try:
                msg = str(ev.get('message') or '')
                trigger = (self.config.song.trigger or '').strip()
                if trigger and trigger in msg:
                    self._remember_sent(msg)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._maybe_forward_log_to_mc(ev.get('level'), ev.get('message'))
            except Exception:  # noqa: BLE001
                pass
        try:
            self._record_ui_event(ev)
        except Exception:  # noqa: BLE001
            pass
        self.ui_queue.put(ev)

    # ------------------------------------------------------------------
    # Log persistence (low level)
    # ------------------------------------------------------------------
    def record_log(self, text) -> None:
        """Record a UI-originated log line into the persistent log buffer."""
        self.log_saver.add(text)

    def _record_ui_event(self, ev: Dict[str, Any]) -> None:
        """Mirror backend UI events into the persistent log buffer.

        Server chat / announcements / command output (``kind == 'log'`` with
        level ``server``) and private messages all funnel through here, so every
        server message is saved even if it is never read from the GUI.
        """
        if not isinstance(ev, dict):
            return
        kind = ev.get('kind')
        if kind == 'log':
            self.log_saver.add(ev.get('message', ''))
        elif kind == 'chat':
            prefix = '[私聊] ' if ev.get('private') else ''
            self.log_saver.add(
                f'{prefix}{ev.get("player", "?")}: {ev.get("content", "")}')
        elif kind == 'actionBar':
            self.log_saver.add(f'[ActionBar] {ev.get("text", "")}')

    def tick_log_save(self) -> None:
        """Flush the log buffer when the calendar day rolls over."""
        saved = self.log_saver.maybe_roll()
        if saved:
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': f'日志已保存：{os.path.basename(saved)}'})

    def _maybe_forward_log_to_mc(self, level: Optional[str], message) -> None:
        """Forward selected log lines (default: errors) to the MC public chat."""
        cfg = self.config.logForward
        if not cfg.enabled or not self._engine.running:
            return
        if (level or 'info') not in (cfg.levels or []):
            return
        text = re.sub(r'\s+', ' ', str(message or '')).strip()
        if not text:
            return
        # ⚠ 千万别把「带点歌触发词的日志」转到游戏里：那等于让机器人自己喊一句 [点歌]…，
        #    服务器广播回来又被当成新的点歌 → 自触发死循环（实测能把机器人刷到被踢）。
        trigger = (self.config.song.trigger or '').strip()
        for word in ([trigger] + self._trigger_aliases(trigger)) if trigger else []:
            if word and word in text:
                return
        tmpl = cfg.format or '&7Log -> &c{log}'
        try:
            out = tmpl.format(log=text)
        except (KeyError, IndexError, ValueError):
            out = '&7Log -> &c' + text
        self._engine.say(out)

    def drain_ui(self, max_items: int = 500) -> List[Dict[str, Any]]:
        items = []
        try:
            for _ in range(max_items):
                items.append(self.ui_queue.get_nowait())
        except queue.Empty:
            pass
        return items
