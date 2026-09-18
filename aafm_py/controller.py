"""Controller tying the bot engine, AI player and UI together."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from .ai_player import AiPlayer
from .bot_engine import BotEngine, BASE_DIR
from .config import (AiPlayerConfig, AppConfig, load_auth_profile)
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
    if qq.mode == 'reverse':
        return f'反向监听 {qq.listenHost}:{qq.listenPort}'
    return f'正向连接 {qq.url}'


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
        self._last_mc_to_qq_time = 0.0
        self._last_health_alert = 0.0
        self._qq_nicknames: Dict[str, str] = {}
        self._load_qq_accounts()
        self._photo = PhotoServer()
        self._public_base_cache = ''

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
        self._kick_lobby_pending = False
        self._lobby_at: Optional[float] = None

        self.check_updates_async()
        threading.Thread(target=self._napcat_config_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Auto-configure OneBot network for newly scanned QQ accounts
    # ------------------------------------------------------------------
    def _napcat_config_loop(self) -> None:
        # First pass shortly after startup, then poll for new accounts.
        while True:
            try:
                if self.config.qq.autoConfigNewAccounts:
                    self._ensure_napcat_configs()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5)

    def _desired_network(self):
        """Build the WS server/client entry from the current QQ settings."""
        cfg = self.config.qq
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
        d = NAPCAT_CONFIG_DIR
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
        self._engine.stop()
        self._post_ui({'kind': 'status', 'text': '已断开连接'})
        self._post_ui({'kind': 'disconnected', 'reason': 'user'})

    def shutdown(self) -> None:
        """Full stop used on app close: MC connection plus the QQ relay."""
        self.disconnect()
        self._stop_qq()

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
        max_attempts = max(1, cfg.maxAttempts)
        if self._reconnect_attempts >= max_attempts:
            self._post_ui({'kind': 'reconnect', 'state': 'giveup',
                           'attempts': self._reconnect_attempts})
            self._post_ui({'kind': 'log', 'level': 'warn',
                           'message': f'已连续重连 {self._reconnect_attempts} 次仍未成功，停止重连。'})
            return
        self._reconnect_attempts += 1
        delay = max(0, cfg.delaySeconds)
        self._reconnect_at = time.time() + delay
        if kicked and cfg.kickLobbyEnabled:
            self._kick_lobby_pending = True
        # Drop the dead engine process now so no stale events race the retry.
        self._engine.stop()
        self._post_ui({'kind': 'reconnect', 'state': 'scheduled',
                       'attempt': self._reconnect_attempts, 'max': max_attempts,
                       'delay': delay})
        self._post_ui({'kind': 'log', 'level': 'warn',
                       'message': f'检测到{"被踢出" if kicked else "非主动断线"}，'
                                  f'{delay}s 后自动重连（第 {self._reconnect_attempts}/{max_attempts} 次）…'})

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
    # QQ group-chat relay (OneBot 11 gateway)
    # ------------------------------------------------------------------
    @property
    def qq_connected(self) -> bool:
        return self._qq_connected and self._qq.running

    @property
    def qq_running(self) -> bool:
        return self._qq.running

    def _build_qq_runtime_config(self) -> Dict[str, Any]:
        cfg = self.config.qq
        return {
            'mode': cfg.mode,
            'url': cfg.url,
            'accessToken': cfg.accessToken or '',
            'listenHost': cfg.listenHost or '0.0.0.0',
            'listenPort': cfg.listenPort,
            'groups': list(cfg.groups or []),
            'imageAsLink': bool(cfg.imageAsLink),
            'autoReconnect': bool(cfg.autoReconnect),
            'healthCheckSeconds': int(cfg.healthCheckSeconds),
        }

    def start_qq(self) -> None:
        if self._qq.running:
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
            for name in os.listdir(NAPCAT_CONFIG_DIR):
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

    def start_napcat(self, uin: str = '') -> None:
        """Launch the bundled NapCat launcher (optionally quick-login a QQ)."""
        if not os.path.isfile(NAPCAT_LAUNCHER):
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '未找到 NapCat 启动脚本：' + NAPCAT_LAUNCHER})
            return
        uin = str(uin or '').strip()
        args = ['cmd', '/c', NAPCAT_LAUNCHER]
        if uin:
            args += ['-q', uin]
            self.config.qq.lastLoginUin = uin
            self.config.save()
        try:
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(args, cwd=NAPCAT_INNER_DIR, **kwargs)
            self._post_ui({'kind': 'log', 'level': 'info',
                           'message': ('已启动 NapCat 并请求快速登录 ' + uin) if uin
                                      else '已启动 NapCat（会弹窗/UAC 提权，首次请扫码登录）。'})
        except Exception as e:  # noqa: BLE001
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '启动 NapCat 失败: ' + str(e)})
            return
        self._open_napcat_cache()

    def _open_napcat_cache(self) -> None:
        try:
            os.makedirs(NAPCAT_CACHE_DIR, exist_ok=True)
            if os.name == 'nt':
                os.startfile(NAPCAT_CACHE_DIR)
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
                base = self._image_base_url()
                self._post_ui({'kind': 'log', 'level': 'info',
                               'message': f'图片服务已启动：{base}/pic_http/（端口 {cfg.imageHttpPort}）'})
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

    def _stop_qq(self) -> None:
        was_running = self._qq.running
        self._qq.stop()
        self._photo.stop()
        self._qq_connected = False
        self._qq_last_state = None
        if was_running:
            self._post_ui({'kind': 'qqStatus', 'state': 'disconnected'})

    def restart_qq(self) -> None:
        """(Re)start the QQ relay with the current settings."""
        self._stop_qq()
        self._start_qq()

    def set_qq_enabled(self, enabled: bool) -> None:
        self.config.qq.enabled = bool(enabled)
        self.config.save()
        if enabled:
            if not self._qq.running:
                self._start_qq()
        else:
            self._stop_qq()
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
        if not self._engine.running:
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
        while self._running or self._qq.running:
            try:
                ev = self._bot_events.get(timeout=0.3)
            except queue.Empty:
                ev = None
            if ev is not None:
                self._process_bot_event(ev)
            while True:
                try:
                    qev = self._qq_events.get_nowait()
                except queue.Empty:
                    break
                self._process_qq_event(qev)
            now = time.time()
            if self._reconnect_at is not None and now >= self._reconnect_at:
                self._run_reconnect()
            if self._lobby_at is not None and now >= self._lobby_at:
                self._run_kick_lobby()
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
            if not private and self._maybe_relay_mc_to_qq(player, content):
                return
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
            self._maybe_health_alert(ev.get('health'))
        elif event == 'spawn':
            self._connected = True
            self._connecting = False
            self._reconnect_attempts = 0
            self._reconnect_at = None
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
            self._schedule_kick_lobby()
        elif event == 'kicked':
            was_connected = self._connected
            self._connected = False
            self._connecting = False
            self._post_ui({'kind': 'status', 'text': '被踢出服务器'})
            self._post_ui({'kind': 'log', 'level': 'error',
                           'message': '被踢出: ' + ev.get('reason', '')})
            relogin = self._maybe_request_relogin(ev.get('reason', ''))
            if not relogin:
                self._schedule_reconnect(kicked=was_connected)
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
            if not self._manual_disconnect and reason != 'quit':
                self._schedule_reconnect(kicked=False)
        elif event == 'chatSent':
            text = ev.get('text', '')
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
        self.ui_queue.put(ev)

    def drain_ui(self, max_items: int = 500) -> List[Dict[str, Any]]:
        items = []
        try:
            for _ in range(max_items):
                items.append(self.ui_queue.get_nowait())
        except queue.Empty:
            pass
        return items
