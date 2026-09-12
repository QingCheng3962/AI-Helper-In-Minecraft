"""Configuration models and persistence for the Python AI-player app.

Ported from the aafm (AI Agent for Minecraft) Fabric mod:
- AI player settings mirror ``mcai_chat.json`` (com.mcai.chat.ChatConfig)
- Server / auth / LLM / quiz / autoeat settings live in ``config.json``
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APP_CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
AI_PLAYER_CONFIG_FILE = os.path.join(BASE_DIR, 'mcai_chat.json')
AUTH_PROFILE_FILE = os.path.join(BASE_DIR, 'auth_profile.json')
PROMPT_TEMPLATES_FILE = os.path.join(BASE_DIR, 'mcai_prompt_templates.json')

DEFAULT_BASE_URLS = {
    'openai': 'https://api.openai.com/v1',
    'deepseek': 'https://api.deepseek.com/v1',
    'moonshot': 'https://api.moonshot.cn/v1',
    'anthropic': 'https://api.anthropic.com',
    'ollama': 'http://localhost:11434/v1',
}

DEFAULT_LITTLESKIN_SITE = 'https://littleskin.cn'
DEFAULT_LITTLESKIN_CLIENT_ID = '1153'
DEFAULT_LITTLESKIN_CLIENT_SECRET = '3C1p27R9qxnH4lBrJjM9NsWVjYG2UuAluag95ydS'
DEFAULT_LITTLESKIN_REDIRECT_PORT = 7322

TEMPLATE_ASSISTANT = (
    '你是 Minecraft 聊天中的 AI 助手。回答要简洁，不要使用命令，不要发送以 / 开头的内容。'
)

TEMPLATE_BUDDY = (
    '你是一位 Minecraft 聊天搭子（玩家的好朋友），像真人玩家一样活跃在服务器聊天里。'
    '行为准则：每次回复只发一句玩家风格的短话，简短自然口语化，不要长篇大论；'
    '如果需要多条消息，每条之间间隔约 1 秒逐条发出；'
    '可以解答玩家的问题、回应大家的聊天、调侃玩笑玩梗，让氛围轻松愉快，不要过度严肃；'
    '使用 & 符号给文字着色（如 &a绿 &c红 &6金 &b蓝 &d粉 &e黄），像服务器常见彩色聊天一样；'
    '可以纯净输出服务器命令：/pay <玩家> <金额>、/tpa <玩家>、/tpahere、/home、/sethome、/afk、/msg <玩家> <内容> 等，需要时直接发出；'
    '快速监听玩家消息，快速回复，追求极高的响应速度与效率，不要犹豫拖沓；'
    '保持友好，不要恶意冒犯、辱骂或刷屏。'
)

DEFAULT_TRIGGER_REGEX = r'(?i)^(ai|@ai)[，, ]'
DEFAULT_IMAGE_TRIGGER_REGEX = r'(?i)^(?:画|生成图片|img)[：: ]?(.*)$'


# ---------------------------------------------------------------------------
# AI player config (mirrors ChatConfig)
# ---------------------------------------------------------------------------
@dataclass
class BlockedPlayer:
    name: str = ''
    enabled: bool = True


@dataclass
class AiPlayerConfig:
    enabled: bool = False
    templateName: str = '聊天搭子'
    systemPrompt: str = TEMPLATE_BUDDY

    provider: str = 'openai'
    baseUrl: str = ''
    apiKey: str = ''
    model: str = 'gpt-4o-mini'
    temperature: float = 0.7
    maxTokens: int = 1024

    maxReplyMessages: int = 3
    maxCharsPerMessage: int = 100

    contextEnabled: bool = True
    contextLength: int = 20

    triggerEnabled: bool = True
    triggerRegex: str = DEFAULT_TRIGGER_REGEX
    triggerCooldownSeconds: int = 10

    autoReplyEnabled: bool = False
    scheduleEnabled: bool = False
    scheduleIntervalSeconds: int = 30

    restrictionEnabled: bool = True
    blockedRegexPatterns: List[str] = field(default_factory=list)
    blockedPlayers: List[Dict[str, Any]] = field(default_factory=list)

    timeoutSeconds: int = 30
    retryCount: int = 1
    debugLog: bool = True

    imageGenerationEnabled: bool = False
    imageModel: str = 'gpt-image-1'
    imageTriggerRegex: str = DEFAULT_IMAGE_TRIGGER_REGEX
    imageSize: str = '1024x1024'
    imageRatio: str = '1:1'
    imageTimeoutSeconds: int = 120
    imageRetryCount: int = 1
    imageCooldownSeconds: int = 30

    def validate(self) -> None:
        if not self.templateName:
            self.templateName = '聊天搭子'
        if not self.provider:
            self.provider = 'openai'
        self.provider = self.provider.lower()
        if self.apiKey is None:
            self.apiKey = ''
        if not self.model:
            self.model = 'gpt-4o-mini'
        if not self.systemPrompt:
            self.systemPrompt = TEMPLATE_BUDDY
        if not self.triggerRegex:
            self.triggerRegex = DEFAULT_TRIGGER_REGEX
        if not self.imageTriggerRegex:
            self.imageTriggerRegex = DEFAULT_IMAGE_TRIGGER_REGEX
        if self.blockedRegexPatterns is None:
            self.blockedRegexPatterns = []
        if self.blockedPlayers is None:
            self.blockedPlayers = []
        self.blockedPlayers = [b for b in self.blockedPlayers
                               if isinstance(b, dict) and b.get('name')]
        self.temperature = _clamp_float(self.temperature, 0.0, 2.0)
        self.maxTokens = _clamp_int(self.maxTokens, 1, 4096)
        self.maxReplyMessages = _clamp_int(self.maxReplyMessages, 1, 10)
        self.maxCharsPerMessage = _clamp_int(self.maxCharsPerMessage, 1, 256)
        self.contextLength = _clamp_int(self.contextLength, 0, 100)
        self.scheduleIntervalSeconds = _clamp_int(self.scheduleIntervalSeconds, 5, 3600)
        self.triggerCooldownSeconds = _clamp_int(self.triggerCooldownSeconds, 1, 3600)
        self.timeoutSeconds = _clamp_int(self.timeoutSeconds, 5, 120)
        self.retryCount = _clamp_int(self.retryCount, 0, 5)
        if not self.imageModel:
            self.imageModel = 'gpt-image-1'
        if not self.imageSize:
            self.imageSize = '1024x1024'
        self.imageTimeoutSeconds = _clamp_int(self.imageTimeoutSeconds, 30, 3600)
        self.imageRetryCount = _clamp_int(self.imageRetryCount, 0, 3)
        self.imageCooldownSeconds = _clamp_int(self.imageCooldownSeconds, 5, 3600)
        if self.autoReplyEnabled:
            self.triggerEnabled = False
            self.scheduleEnabled = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, path: Optional[str] = None) -> 'AiPlayerConfig':
        path = path or AI_PLAYER_CONFIG_FILE
        cfg = cls()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if hasattr(cfg, k):
                            setattr(cfg, k, v)
            except Exception as e:
                print(f'[mcai] Failed to read {path}, using defaults: {e}')
        cfg.validate()
        return cfg

    def save(self, path: Optional[str] = None) -> None:
        path = path or AI_PLAYER_CONFIG_FILE
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[mcai] Failed to save {path}: {e}')


# ---------------------------------------------------------------------------
# App config (server / auth / llm / quiz / autoeat)
# ---------------------------------------------------------------------------
@dataclass
class ServerConfig:
    host: str = 'frp-day.com'
    port: int = 33726
    version: str = '1.20'
    brand: str = 'aafm-python'
    viewDistance: str = 'tiny'


@dataclass
class AuthConfig:
    """Login settings. ``type`` is one of ``offline`` / ``littleskin`` / ``microsoft``.

    - offline   : offline-mode, joins with ``username`` only.
    - littleskin: yggdrasil auth against ``site`` (default littleskin.cn). The
      OAuth app (clientId/clientSecret/redirectPort) is used by the LittleSkin
      login helper script.
    - microsoft : official Microsoft/Minecraft account (device-code auth). Token
      cache lives in ``profilesFolder`` (empty => minecraft default nmp-cache).
    """
    type: str = 'offline'
    username: str = 'AI_Bot'
    uuid: str = ''
    session: Dict[str, Any] = field(default_factory=dict)
    site: str = DEFAULT_LITTLESKIN_SITE
    clientId: str = DEFAULT_LITTLESKIN_CLIENT_ID
    clientSecret: str = DEFAULT_LITTLESKIN_CLIENT_SECRET
    redirectPort: int = DEFAULT_LITTLESKIN_REDIRECT_PORT
    profilesFolder: str = ''

    def site_base(self) -> str:
        """Normalized LittleSkin site root without trailing slash."""
        s = (self.site or '').strip().rstrip('/')
        return s or DEFAULT_LITTLESKIN_SITE

    def api_base(self) -> str:
        """Normalized LittleSkin yggdrasil base (``<site>/api/yggdrasil``)."""
        return self.site_base() + '/api/yggdrasil'

    def validate(self) -> None:
        if self.type not in ('offline', 'littleskin', 'microsoft'):
            self.type = 'offline'
        if not self.username:
            self.username = 'AI_Bot'
        if not self.clientId:
            self.clientId = DEFAULT_LITTLESKIN_CLIENT_ID
        self.clientId = str(self.clientId).strip()
        if not self.site_base():
            self.site = DEFAULT_LITTLESKIN_SITE
        if self.clientSecret is None:
            self.clientSecret = ''
        try:
            self.redirectPort = int(self.redirectPort)
        except (TypeError, ValueError):
            self.redirectPort = DEFAULT_LITTLESKIN_REDIRECT_PORT
        if self.session is None:
            self.session = {}
        if not isinstance(self.session, dict):
            self.session = {}


@dataclass
class LLMConfig:
    provider: str = 'openai'
    baseUrl: str = ''
    apiKey: str = ''
    model: str = 'gpt-4o-mini'
    temperature: float = 0.7
    maxTokens: int = 1024
    timeoutSeconds: int = 30
    retryCount: int = 1


@dataclass
class QuizConfig:
    enabled: bool = True
    minDelay: int = 3000
    questionBank: str = 'questionBank.json'


@dataclass
class AutoEatConfig:
    enabled: bool = True


@dataclass
class QqConfig:
    """QQ group-chat relay via an OneBot 11 gateway (NapCat / Lagrange / go-cqhttp).

    - ``mode`` ``forward`` connects out to the gateway's WebSocket server;
      ``reverse`` listens and lets the gateway connect in.
    - ``groups`` is the list of group ids to relay; empty means every group.
    - ``format`` is the MC chat template with ``{group}`` / ``{name}`` /
      ``{message}`` placeholders. The default uses the ``&7&o`` color codes
      (dark gray, italic) requested for the relayed lines.
    """
    enabled: bool = False
    mode: str = 'forward'
    url: str = 'ws://127.0.0.1:3001'
    accessToken: str = ''
    listenHost: str = '0.0.0.0'
    listenPort: int = 3002
    groups: List[str] = field(default_factory=list)
    format: str = '&7&o[{group}][{name}]：{message}'
    maxLength: int = 220

    # MC -> QQ relay: when an in-game chat message starts with ``mcTrigger``,
    # send the rest to a QQ group. ``mcTargetGroup`` empty => first ``groups``
    # entry; ``[sentQ:群号] 内容`` overrides the target for one message.
    mcToQqEnabled: bool = False
    mcTrigger: str = '[sentQ]'
    mcTargetGroup: str = ''
    mcFormat: str = '[MC] {player}：{message}'
    mcCooldownSeconds: int = 3

    def validate(self) -> None:
        if self.mode not in ('forward', 'reverse'):
            self.mode = 'forward'
        if not self.url:
            self.url = 'ws://127.0.0.1:3001'
        if not self.listenHost:
            self.listenHost = '0.0.0.0'
        try:
            self.listenPort = int(self.listenPort)
        except (TypeError, ValueError):
            self.listenPort = 3002
        self.listenPort = _clamp_int(self.listenPort, 1, 65535)
        if self.accessToken is None:
            self.accessToken = ''
        if not self.format:
            self.format = '&7&o[{group}][{name}]：{message}'
        self.maxLength = _clamp_int(self.maxLength, 20, 256)
        if self.groups is None:
            self.groups = []
        norm = []
        for g in self.groups:
            s = str(g).strip()
            if s and s not in norm:
                norm.append(s)
        self.groups = norm
        if not self.mcTrigger:
            self.mcTrigger = '[sentQ]'
        self.mcTargetGroup = str(self.mcTargetGroup or '').strip()
        if not self.mcFormat:
            self.mcFormat = '[MC] {player}：{message}'
        self.mcCooldownSeconds = _clamp_int(self.mcCooldownSeconds, 0, 3600)


@dataclass
class RosterConfig:
    """Local "who is this player" fact table loaded from .xlsx / .csv files.

    Columns: 玩家名 | 玩家描述 (authoritative, may be empty) | 描述1/描述2/....
    | AI记录1..AI记录5 (personality notes the LLM writes after reading chat).
    - "xxx是谁" answers the authoritative 玩家描述 when set, otherwise picks a
      描述1..N at random.
    - A triggered in-game statement "X是Y / X就是Y" (X or Y in the table) is
      written back into that player's 玩家描述 cell of the source workbook.
    - When ``personalityEnabled`` the AI periodically (and on an explicit
      "研究X" request) reads recent chat of a player and rolls one concise
      personality note into AI记录1..5 of that player's row.
    """
    enabled: bool = True
    files: List[str] = field(default_factory=list)

    personalityEnabled: bool = True
    researchIntervalSeconds: int = 180
    researchMinMessages: int = 6

    # Independent alias analysis (reads chat, writes 别名1..3).
    aliasEnabled: bool = True


@dataclass
class ReconnectConfig:
    """Automatic recovery after a non-user disconnect / server kick.

    - ``enabled``               : reconnect when the bot drops on its own.
    - ``delaySeconds``          : wait before each reconnect attempt.
    - ``maxAttempts``           : give up after this many tries (reset on spawn).
    - ``kickLobbyEnabled``      : after being kicked and reconnecting, send /lobby.
    - ``kickLobbyDelaySeconds`` : wait this long after spawn before sending /lobby.
    """
    enabled: bool = True
    delaySeconds: int = 5
    maxAttempts: int = 5
    kickLobbyEnabled: bool = True
    kickLobbyDelaySeconds: int = 2

    def validate(self) -> None:
        self.delaySeconds = _clamp_int(self.delaySeconds, 0, 600)
        self.maxAttempts = _clamp_int(self.maxAttempts, 1, 100)
        self.kickLobbyDelaySeconds = _clamp_int(self.kickLobbyDelaySeconds, 0, 600)


@dataclass
class AppearanceConfig:
    """GUI look & feel: optional window background image + light/dark theme.

    - ``backgroundImage``    : path to a PNG/JPG shown as the window background
                               (panels stay a solid theme colour on top).
    - ``backgroundDarkness`` : 0-100, dims the background image.
    - ``textTheme``          : ``auto`` (pick from the image brightness) /
                               ``light`` (light background -> black text) /
                               ``dark`` (dark background -> white text).
    """
    backgroundImage: str = ''
    backgroundDarkness: int = 40
    textTheme: str = 'auto'

    def validate(self) -> None:
        self.backgroundImage = str(self.backgroundImage or '')
        self.backgroundDarkness = _clamp_int(self.backgroundDarkness, 0, 100)
        if self.textTheme not in ('auto', 'light', 'dark'):
            self.textTheme = 'auto'


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    quiz: QuizConfig = field(default_factory=QuizConfig)
    autoeat: AutoEatConfig = field(default_factory=AutoEatConfig)
    qq: QqConfig = field(default_factory=QqConfig)
    roster: RosterConfig = field(default_factory=RosterConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)
    appearance: AppearanceConfig = field(default_factory=AppearanceConfig)

    def validate(self) -> None:
        try:
            self.server.port = int(self.server.port)
        except (TypeError, ValueError):
            self.server.port = 25565
        if not self.server.host:
            self.server.host = 'localhost'
        if not self.llm.provider:
            self.llm.provider = 'openai'
        self.llm.provider = self.llm.provider.lower()
        if not self.llm.model:
            self.llm.model = 'gpt-4o-mini'
        if not self.auth.username:
            self.auth.username = 'AI_Bot'
        self.auth.validate()
        self.qq.validate()
        self.reconnect.validate()
        self.appearance.validate()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, path: Optional[str] = None) -> 'AppConfig':
        path = path or APP_CONFIG_FILE
        cfg = cls()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for section in ('server', 'auth', 'llm', 'quiz', 'autoeat', 'qq', 'roster',
                                    'reconnect', 'appearance'):
                        sub = data.get(section)
                        if isinstance(sub, dict):
                            cur = getattr(cfg, section)
                            for k, v in sub.items():
                                if hasattr(cur, k):
                                    setattr(cur, k, v)
            except Exception as e:
                print(f'[mcai] Failed to read {path}, using defaults: {e}')
        cfg.validate()
        return cfg

    def save(self, path: Optional[str] = None) -> None:
        path = path or APP_CONFIG_FILE
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[mcai] Failed to save {path}: {e}')


# ---------------------------------------------------------------------------
# Prompt template manager
# ---------------------------------------------------------------------------
class PromptTemplateManager:
    """Manage named prompt templates, persisted as JSON."""

    @staticmethod
    def _load() -> List[Dict[str, str]]:
        if not os.path.exists(PROMPT_TEMPLATES_FILE):
            return []
        try:
            with open(PROMPT_TEMPLATES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f'[mcai] Failed to load prompt templates: {e}')
            return []

    @staticmethod
    def _save_list(templates: List[Dict[str, str]]) -> None:
        try:
            with open(PROMPT_TEMPLATES_FILE, 'w', encoding='utf-8') as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[mcai] Failed to save prompt templates: {e}')

    @classmethod
    def get_all(cls) -> List[Dict[str, str]]:
        return cls._load()

    @classmethod
    def save(cls, name: str, prompt: str) -> None:
        templates = cls._load()
        for t in templates:
            if t.get('name') == name:
                t['prompt'] = prompt
                cls._save_list(templates)
                return
        templates.append({'name': name, 'prompt': prompt})
        cls._save_list(templates)

    @classmethod
    def delete(cls, name: str) -> None:
        templates = cls._load()
        templates = [t for t in templates if t.get('name') != name]
        cls._save_list(templates)


def load_auth_profile() -> Optional[Dict[str, Any]]:
    """Load auth_profile.json produced by 3rdparty_auth.js (LittleSkin login)."""
    try:
        if os.path.exists(AUTH_PROFILE_FILE):
            with open(AUTH_PROFILE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get('session') and data.get('username'):
                return data
    except Exception as e:
        print(f'[mcai] Failed to read auth profile: {e}')
    return None


def _clamp_int(v: Any, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return lo


def _clamp_float(v: Any, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo
