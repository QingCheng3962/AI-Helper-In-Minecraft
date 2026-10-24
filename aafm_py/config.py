"""Configuration models and persistence for the Python AI-player app.

Ported from the aafm (AI Agent for Minecraft) Fabric mod:
- AI player settings mirror ``mcai_chat.json`` (com.mcai.chat.ChatConfig)
- Server / auth / LLM / quiz / autoeat settings live in ``config.json``
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APP_CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
AI_PLAYER_CONFIG_FILE = os.path.join(BASE_DIR, 'mcai_chat.json')
AUTH_PROFILE_FILE = os.path.join(BASE_DIR, 'auth_profile.json')
PROMPT_TEMPLATES_FILE = os.path.join(BASE_DIR, 'mcai_prompt_templates.json')
BOOK_PROGRESS_FILE = os.path.join(BASE_DIR, 'book_progress.json')


def _atomic_write_json(path: str, data: Any) -> bool:
    """把 JSON 原子地写到 ``path``：先写同目录的临时文件，成功后再 ``os.replace``。

    和 ``open(path, 'w')`` 的区别是**失败不会截断原文件**——``w`` 模式一打开就
    把原内容清空了，之后序列化/写盘/关闭任何一步出错，整份配置就没了。这里出错
    时原文件原样保留，临时文件被清掉。

    返回 ``True``/``False``，让调用方能判断成败。配置文件里有服务器地址、认证
    信息和 AI Key，静默失败会让界面照常提示「已保存」，用户完全不知道数据丢了。
    """
    folder = os.path.dirname(os.path.abspath(path))
    tmp = ''
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + '.',
                                   suffix='.tmp', dir=folder)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = ''                    # 换成功了，finally 里别再删
        return True
    except Exception as e:  # noqa: BLE001
        print(f'[mcai] Failed to save {path}: {e}')
        return False
    finally:
        # 只清理本次调用自己创建的临时文件；原配置文件绝不动
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


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
DEFAULT_SEARCH_TRIGGER_REGEX = (
    r'(?i)(搜索|搜一下|搜下|帮我搜|查一下|查查|帮我查|最新|最近|现在|今天|'
    r'新闻|天气|股价|价格|汇率|比分|几点|什么时候|什么是|是谁'
    r'|latest|news|weather|price|score|current|today|who is|what is)')


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
    # Also feed server/system messages (announcements, command output) into the
    # AI context so replies can reference them.
    serverMessagesInContext: bool = True

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

    # Periodic random chat: each entry has its own random interval (min..max
    # seconds); entries fire independently.
    idleChatEnabled: bool = False
    idleChatItems: List[Dict[str, Any]] = field(default_factory=list)
    idleChatCountMin: int = 1
    idleChatCountMax: int = 1

    # Web search (auto when a message looks like it needs live info).
    searchEnabled: bool = False
    searchProvider: str = 'bing'            # bing | duckduckgo | custom
    searchApiUrl: str = ''
    searchApiKey: str = ''
    searchMaxResults: int = 5
    searchTriggerRegex: str = DEFAULT_SEARCH_TRIGGER_REGEX

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

        if self.idleChatItems is None:
            self.idleChatItems = []
        items = []
        for it in self.idleChatItems:
            if not isinstance(it, dict):
                continue
            text = str(it.get('text', '')).strip()
            if not text:
                continue
            lo = _clamp_int(it.get('min', 60), 1, 86400)
            hi = _clamp_int(it.get('max', lo), lo, 86400)
            items.append({'text': text, 'min': lo, 'max': hi})
        self.idleChatItems = items
        self.idleChatCountMin = _clamp_int(self.idleChatCountMin, 1, 10)
        self.idleChatCountMax = _clamp_int(self.idleChatCountMax, self.idleChatCountMin, 10)

        self.searchProvider = (self.searchProvider or 'bing').lower()
        if self.searchProvider not in ('duckduckgo', 'bing', 'custom'):
            self.searchProvider = 'bing'
        self.searchApiUrl = str(self.searchApiUrl or '').strip()
        self.searchApiKey = str(self.searchApiKey or '').strip()
        self.searchMaxResults = _clamp_int(self.searchMaxResults, 1, 10)
        if not self.searchTriggerRegex:
            self.searchTriggerRegex = DEFAULT_SEARCH_TRIGGER_REGEX

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

    def save(self, path: Optional[str] = None) -> bool:
        """写盘。返回 ``True`` 表示成功，``False`` 表示失败（原文件保持不变）。"""
        path = path or AI_PLAYER_CONFIG_FILE
        return _atomic_write_json(path, self.to_dict())


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
class VerifyConfig:
    """Auto-pass a chest-GUI "human verification" (find the odd slots).

    The verifier opens a container filled mostly with one kind of block plus a
    few different ones; we group slots by a signature (item name + metadata +
    custom name), treat the majority as background and click the odd slots.
    """
    enabled: bool = False
    titleKeyword: str = 'HumanVerify'   # only act when the title contains this
    nameKeyword: str = '点击这里'        # click items whose name contains this
    minMajority: float = 0.6            # fallback: required share of background sig
    maxTargets: int = 12                # give up if more "odd" slots than this
    clickDelayMs: int = 250             # delay (plus a random extra) between clicks
    startDelayMs: int = 600             # wait for items to populate before reading
    debug: bool = True                  # log window title + slot contents

    def validate(self) -> None:
        self.titleKeyword = str(self.titleKeyword or '').strip()
        self.nameKeyword = str(self.nameKeyword or '').strip()
        self.startDelayMs = _clamp_int(self.startDelayMs, 0, 10000)
        try:
            self.minMajority = float(self.minMajority)
        except (TypeError, ValueError):
            self.minMajority = 0.6
        self.minMajority = max(0.3, min(0.99, self.minMajority))
        self.maxTargets = _clamp_int(self.maxTargets, 1, 54)
        self.clickDelayMs = _clamp_int(self.clickDelayMs, 0, 5000)


@dataclass
class CommandBlockConfig:
    """Block outgoing commands (from AI replies / random chat) whose name is in
    the list, so prompt-injection can't run dangerous commands (pay/kick/ban...)."""
    enabled: bool = True
    commands: List[str] = field(default_factory=lambda: [
        'pay', 'kick', 'ban', 'ban-ip', 'pardon', 'op', 'deop', 'stop',
        'restart', 'whitelist', 'kill', 'give', 'tp', 'gamemode'])
    # Message sent to the server after a command is blocked.
    # Placeholders: {command}=command name, {player}=who requested it, {cmd}=alias.
    message: str = '&c已拦截危险命令：/{command}'

    def validate(self) -> None:
        if self.commands is None:
            self.commands = []
        norm = []
        for c in self.commands:
            s = str(c).strip().lstrip('/').strip().lower()
            if s and s not in norm:
                norm.append(s)
        self.commands = norm
        if self.message is None:
            self.message = '&c已拦截危险命令：/{command}'


@dataclass
class LogForwardConfig:
    """Forward program log lines (e.g. errors) to the MC public chat."""
    enabled: bool = False
    format: str = '&7Log -> &c{log}'
    levels: List[str] = field(default_factory=lambda: ['error'])

    def validate(self) -> None:
        if not self.format:
            self.format = '&7Log -> &c{log}'
        if self.levels is None:
            self.levels = ['error']
        norm = []
        for x in self.levels:
            s = str(x).strip().lower()
            if s and s not in norm:
                norm.append(s)
        self.levels = norm or ['error']


@dataclass
class HealthAlertConfig:
    """Say something in MC chat when the bot's health drops to/below a value."""
    enabled: bool = False
    threshold: int = 6
    message: str = '&c我血量过低了，快救救我！'
    cooldownSeconds: int = 30

    def validate(self) -> None:
        self.threshold = _clamp_int(self.threshold, 1, 20)
        self.cooldownSeconds = _clamp_int(self.cooldownSeconds, 0, 3600)
        if not self.message:
            self.message = '&c我血量过低了，快救救我！'


@dataclass
class QuizConfig:
    enabled: bool = True
    minDelay: int = 3000
    questionBank: str = 'questionBank.json'


@dataclass
class AutoEatConfig:
    enabled: bool = True


@dataclass
class OneBotConfig:
    """OneBot 11 endpoint settings for a single framework.

    SnowLuma and NapCat keep **separate** copies so each can have its own
    launcher path, port and token without clobbering the other.
    """
    # Folder (or launcher .bat/.exe) used by the 「启动」 button. Empty => the
    # default location (SnowLuma/ or the bundled NapCat/).
    launcherPath: str = ''
    mode: str = 'forward'
    url: str = 'ws://127.0.0.1:3001'
    accessToken: str = ''
    listenHost: str = '0.0.0.0'
    listenPort: int = 3002
    # NapCat only: auto-write the OneBot WS config for newly scanned accounts.
    autoConfigNewAccounts: bool = True

    def validate(self) -> None:
        self.launcherPath = str(self.launcherPath or '').strip()
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
        self.accessToken = str(self.accessToken or '')
        self.autoConfigNewAccounts = bool(self.autoConfigNewAccounts)


@dataclass
class QqConfig:
    """QQ group-chat relay via an OneBot 11 gateway (SnowLuma / NapCat).

    Connection/launch settings live in the per-framework ``snowluma`` and
    ``napcat`` sections; ``framework`` selects which one is used. The relay and
    formatting settings are shared. ``groups`` empty means every group and
    ``format`` is the MC chat template with ``{group}`` / ``{name}`` /
    ``{message}``.
    """
    enabled: bool = False
    framework: str = 'snowluma'    # 'snowluma' | 'napcat'
    snowluma: OneBotConfig = field(default_factory=OneBotConfig)
    napcat: OneBotConfig = field(default_factory=OneBotConfig)
    groups: List[str] = field(default_factory=list)
    format: str = '&7&o[{group}][{name}]：{message}'
    maxLength: int = 220
    # When on, QQ images are downloaded, saved into the local ``pic_http/``
    # folder, and relayed as a link served by the built-in Python HTTP server
    # (only the ``/pic_http/`` path is exposed).
    imageAsLink: bool = False
    imageHttpHost: str = '0.0.0.0'
    imageHttpPort: int = 8765
    # Public base used in the link, e.g. http://1.2.3.4:8765 . Empty => try to
    # auto-detect the machine's public IP, falling back to 127.0.0.1.
    imagePublicBase: str = ''

    # MC -> QQ relay: when an in-game chat message starts with ``mcTrigger``,
    # send the rest to a QQ group. ``mcTargetGroup`` empty => first ``groups``
    # entry; ``[sentQ:群号] 内容`` overrides the target for one message.
    mcToQqEnabled: bool = False
    # QQ -> MC relay: whether group messages are posted into MC chat. Turning it
    # off only stops forwarding; the QQ/OneBot connection keeps running.
    qqToMcEnabled: bool = True
    mcTrigger: str = '[sentQ]'
    mcTargetGroup: str = ''
    mcFormat: str = '[MC] {player}：{message}'
    mcCooldownSeconds: int = 3
    # Feedback sent to MC public chat after a relay attempt. `{player}` is the
    # in-game sender name; `&` color codes are allowed.
    mcSuccessMsg: str = '&a@{player}转述成功'
    mcFailureMsg: str = '&c@{player}转述失败'

    # Reconnect / health. When the QQ account looks restricted the engine stops
    # auto-reconnecting to avoid repeated logins that can worsen risk control.
    autoReconnect: bool = True
    healthCheckSeconds: int = 30
    # Last QQ number used for quick login (-q), preselected in the picker.
    lastLoginUin: str = ''

    def active(self) -> 'OneBotConfig':
        """The endpoint config for the currently selected framework."""
        return self.napcat if self.framework == 'napcat' else self.snowluma

    def validate(self) -> None:
        if self.framework not in ('snowluma', 'napcat'):
            self.framework = 'snowluma'
        if isinstance(self.snowluma, dict):
            self.snowluma = OneBotConfig(**{k: v for k, v in self.snowluma.items()
                                            if k in OneBotConfig.__dataclass_fields__})
        if isinstance(self.napcat, dict):
            self.napcat = OneBotConfig(**{k: v for k, v in self.napcat.items()
                                          if k in OneBotConfig.__dataclass_fields__})
        if not isinstance(self.snowluma, OneBotConfig):
            self.snowluma = OneBotConfig()
        if not isinstance(self.napcat, OneBotConfig):
            self.napcat = OneBotConfig()
        self.snowluma.validate()
        self.napcat.validate()
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
        self.mcToQqEnabled = bool(self.mcToQqEnabled)
        self.qqToMcEnabled = bool(self.qqToMcEnabled)
        if not self.mcSuccessMsg:
            self.mcSuccessMsg = '&a@{player}转述成功'
        if not self.mcFailureMsg:
            self.mcFailureMsg = '&c@{player}转述失败'
        if not self.imageHttpHost:
            self.imageHttpHost = '0.0.0.0'
        try:
            self.imageHttpPort = int(self.imageHttpPort)
        except (TypeError, ValueError):
            self.imageHttpPort = 8765
        self.imageHttpPort = _clamp_int(self.imageHttpPort, 1, 65535)
        self.autoReconnect = bool(self.autoReconnect)
        self.healthCheckSeconds = _clamp_int(self.healthCheckSeconds, 0, 3600)
        self.imagePublicBase = str(self.imagePublicBase or '').strip().rstrip('/')
        self.lastLoginUin = str(self.lastLoginUin or '').strip()


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
    # Server phrases (regex, case-insensitive) that force a reconnect even when
    # the connection did not visibly drop, e.g. a chat "You were kicked from lobby".
    customTriggers: List[str] = field(
        default_factory=lambda: ['You were kicked from lobby'])
    # Proactive reconnect: while online, drop + reconnect every N seconds.
    proactiveEnabled: bool = False
    proactiveIntervalSeconds: int = 300
    # Keep retrying past maxAttempts (e.g. while the server restarts) until it
    # comes back. keepRetryMaxMinutes 0 = no time limit.
    keepRetrying: bool = False
    keepRetryMaxMinutes: int = 0
    # Active reconnect helpers (below the reconnect section).
    # 1) send /lobby every N seconds while online;
    # 2) check online every N seconds; if offline, retry connecting every M
    #    seconds until it is back.
    autoLobbyEnabled: bool = False
    autoLobbyIntervalSeconds: int = 300
    onlineCheckEnabled: bool = False
    onlineCheckIntervalSeconds: int = 60
    onlineReconnectIntervalSeconds: int = 30

    def validate(self) -> None:
        # Allow up to 30 days for the reconnect interval (value + unit picker).
        self.delaySeconds = _clamp_int(self.delaySeconds, 0, 2592000)
        self.maxAttempts = _clamp_int(self.maxAttempts, 1, 100)
        self.kickLobbyDelaySeconds = _clamp_int(self.kickLobbyDelaySeconds, 0, 600)
        self.proactiveIntervalSeconds = _clamp_int(
            self.proactiveIntervalSeconds, 1, 2592000)
        self.keepRetryMaxMinutes = _clamp_int(self.keepRetryMaxMinutes, 0, 100000)
        self.autoLobbyIntervalSeconds = _clamp_int(self.autoLobbyIntervalSeconds, 1, 2592000)
        self.onlineCheckIntervalSeconds = _clamp_int(
            self.onlineCheckIntervalSeconds, 1, 2592000)
        self.onlineReconnectIntervalSeconds = _clamp_int(
            self.onlineReconnectIntervalSeconds, 1, 86400)
        if self.customTriggers is None:
            self.customTriggers = []
        norm = []
        for p in self.customTriggers:
            s = str(p).strip()
            if s and s not in norm:
                norm.append(s)
        self.customTriggers = norm[:50]


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
class DocBookConfig:
    """Document -> in-game book and quill (书与笔) settings.

    - ``pageChars``    : max characters per page (Java Edition limit is 1024).
    - ``pageLines``    : max rendered lines per page (Java Edition fits ~14).
    - ``pagesPerBook`` : pages per book (Java Edition limit is 100).
    - ``startText``    : spoken in game right before writing (may be empty).
    - ``endText``      : spoken in game after writing (may be empty).
    - ``endDrop``      : drop the book after writing.
    - ``autoGetBook``  : in creative mode obtain a book and quill if missing.
    - ``signBook``     : sign each book into a written book (成书) instead of
                         leaving it as a book and quill.
    - ``titleTemplate``: title used when signing; ``{number}`` is replaced by the
                         book number (digits only), e.g. ``《资本论》P{number}``.
    - ``lastFile``     : last document path, remembered between sessions.
    """
    pageChars: int = 1024
    pageLines: int = 14
    pagesPerBook: int = 100
    startText: str = ''
    endText: str = ''
    endDrop: bool = False
    autoGetBook: bool = True
    signBook: bool = False
    titleTemplate: str = ''
    lastFile: str = ''

    def validate(self) -> None:
        self.pageChars = _clamp_int(self.pageChars, 1, 1024)
        # A Java book page only fits 14 lines; never allow more (成书会截断).
        self.pageLines = _clamp_int(self.pageLines, 1, 14)
        self.pagesPerBook = _clamp_int(self.pagesPerBook, 1, 100)
        self.startText = str(self.startText or '')
        self.endText = str(self.endText or '')
        self.endDrop = bool(self.endDrop)
        self.autoGetBook = bool(self.autoGetBook)
        self.signBook = bool(self.signBook)
        self.titleTemplate = str(self.titleTemplate or '')
        self.lastFile = str(self.lastFile or '')


@dataclass
class SongConfig:
    """游戏内点歌（客户端 Mod 方案：玩家装 songmod，聊天里发 ``[点歌]歌名`` 本地播放，
    服务端无需任何改动）。

    - ``trigger``             : 触发词，Mod 与机器人共用，默认 ``[点歌]``。
    - ``replyEnabled``        : 机器人看到 ``[点歌]`` 时是否在公屏回一条 Mod 下载链接
                                （提醒没装 Mod、听不到的人）。
    - ``modUrl``              : 点歌 Mod 的下载链接（发布到 GitHub Releases 后填这里）。
    - ``replyText``           : 回复文案，留空用默认模板；可用 ``{url}`` 占位。
    - ``replyCooldownSeconds``: 机器人回复的冷却（秒），避免刷屏。
    """
    trigger: str = '[点歌]'
    stopWord: str = '停止'
    replyEnabled: bool = True
    modUrl: str = ''
    replyText: str = ''
    replyCooldownSeconds: int = 20
    # 机器人用会员 Cookie 解析歌曲，并把「可播放直链」发到公屏；客户端 Mod 只负责播放。
    postPlayLink: bool = True
    # 网易云登录 Cookie（含 MUSIC_U）。在「点歌」页扫码登录后自动写入。
    neteaseCookie: str = ''
    # 发出播放数据时的文案模板（后面会自动追加 " | 直链"）。
    # 可用 {player} 点歌的人、{music} 歌名 - 歌手、{title} 歌名、{artist} 歌手。
    playText: str = '{player} 点了一首 {music}'
    # 新点歌是否**立刻插播**（不等当前这首放完）。
    # True：新点歌 1 秒内就发出去，Mod 收到新直链会切歌（当前这首被打断）；
    # False：老老实实按歌长排队，但客户端 Mod 等不到直链会走 30s 本地兜底（不推荐）。
    instantPlay: bool = True
    # 是否**只放给点歌的人**（谁点谁听）：True = 播放数据用 /msg **私聊**发给点歌的人，
    # 公屏不再出现点歌消息、其他玩家的 Mod 不会跟着响；False = 老行为（公屏广播）。
    # 注：Mod 侧默认 onlyMySong=true，所以即使这里关了，也只有点歌的人会真的播放。
    privatePlay: bool = True

    def validate(self) -> None:
        self.trigger = str(self.trigger or '').strip() or '[点歌]'
        self.stopWord = str(self.stopWord or '').strip() or '停止'
        self.replyEnabled = bool(self.replyEnabled)
        self.modUrl = str(self.modUrl or '').strip()
        self.replyText = str(self.replyText or '')
        self.replyCooldownSeconds = _clamp_int(self.replyCooldownSeconds, 0, 3600)
        self.postPlayLink = bool(self.postPlayLink)
        self.neteaseCookie = str(self.neteaseCookie or '').strip()
        self.playText = str(self.playText or '').strip() or '{player} 点了一首 {music}'
        self.instantPlay = bool(self.instantPlay)
        self.privatePlay = bool(self.privatePlay)

    def default_reply(self) -> str:
        """默认回复文案（用 {url} 占位下载链接）。"""
        return '这里有人点歌啦～想听到歌，请安装「点歌 Mod」：{url}'

    def build_reply(self) -> str:
        text = self.replyText.strip() or self.default_reply()
        return text.replace('{url}', self.modUrl)


@dataclass
class AffinityConfig:
    """好感度（``[触发词]好感度`` 查询）。

    - ``enabled``             : 总开关。
    - ``chatGain``            : 和机器人聊天一次加多少分。
    - ``chatCooldownSeconds`` : 聊天加分的冷却（秒），防止刷屏刷分。
    - ``songGain``            : 点歌成功一次加多少分（点歌本身有节流，不再另设冷却）。
    - ``dailyGain``           : 每天第一次互动额外加多少分。
    """
    enabled: bool = True
    chatGain: int = 1
    chatCooldownSeconds: int = 300
    songGain: int = 2
    dailyGain: int = 1

    def validate(self) -> None:
        self.enabled = bool(self.enabled)
        self.chatGain = _clamp_int(self.chatGain, 0, 100)
        self.chatCooldownSeconds = _clamp_int(self.chatCooldownSeconds, 0, 86400)
        self.songGain = _clamp_int(self.songGain, 0, 100)
        self.dailyGain = _clamp_int(self.dailyGain, 0, 100)


# 视频推荐来源（GUI「杂项」页的下拉框按这个顺序 + `_VIDEO_SOURCE_KEYS` 对应）
VIDEO_SOURCES = {
    'anime': '番剧追番榜',        # pgc/web/rank/list?season_type=1
    'home': 'B 站首页推荐',       # x/web-interface/index/top/rcmd（首页那排，需先拿 buvid3）
    'hot': 'B 站首页热门',        # x/web-interface/popular
    'rank': 'B 站全站排行',       # x/web-interface/ranking/v2
    'fav': '我的收藏夹（登录）',   # x/v3/fav/resource/list（需 SESSDATA）
}


@dataclass
class MiscConfig:
    """杂项：视频推荐 / 今日幸运值 / 群发私聊。

    对应聊天指令（触发词 = AI 触发词或点歌触发词）：

    - ``[触发词]视频推荐``   → 公屏推一批 B 站视频（默认番剧/追番榜），**全服共用**冷却，
      冷却过后再点会**重新抓一批新的**（换一批 BV，越点越多）。
    - ``[触发词]今日幸运值`` → 当天固定的 1~``luckMax`` 幸运值（日期+玩家名做种子，隔天刷新）。
    - 群发私聊：GUI「杂项」页一个输入框 + 按钮，把一句话 ``/msg`` 给所有在线玩家。
    """

    # ---------------- 视频推荐 ----------------
    videoEnabled: bool = True
    # 来源：anime=番剧追番榜（默认）/ home=B 站首页推荐 / hot=B 站首页热门 /
    #       rank=B 站全站排行 / fav=我的收藏夹（要 Cookie）
    videoSource: str = 'anime'
    # 一次推几条
    videoCount: int = 5
    # 全服共用冷却（秒）：谁的请求都算，冷却过后再点 = 换一批新的
    videoCooldownSeconds: int = 30
    # 抬头文案：{player} 谁点的、{count} 条数、{source} 来源名
    videoTitle: str = '【视频推荐】{player} 点的{source}前 {count} 条：'
    # 每条文案：{index} 序号、{title} 标题、{owner} 备注（UP主/更新进度）、{link} 链接
    videoItemText: str = '{index}. {title} {owner} {link}'
    # 冷却中的提示：{wait} 还要等几秒
    videoCoolText: str = '【视频推荐】刚推过啦，{wait} 秒后再来（会换一批新的）'
    # 抓取失败
    videoFailText: str = '【视频推荐】B 站接口暂时抽风了，稍等一下再试~'
    # 来源选「我的收藏夹」时才需要：B 站登录 Cookie（整条或只填 SESSDATA 值都行）
    biliCookie: str = ''
    # 收藏夹 id（GUI 可点「读取收藏夹」自动填）
    biliFavMediaId: int = 0
    # 收藏夹名字（只是显示用，方便认）
    biliFavName: str = ''

    # ---------------- 今日幸运值 ----------------
    luckEnabled: bool = True
    luckMax: int = 100
    # 文案：{player} 玩家、{value} 幸运值、{max} 最大值、{comment} 评语、{date} 日期
    luckText: str = '【今日幸运值】{player}：{value}/{max} — {comment}（明天 0 点刷新）'

    # ---------------- 每日签到 ----------------
    checkinEnabled: bool = True
    # 金钱奖励：{value} 会在 [最少, 最多] 里随机（两个写一样就是固定值）
    checkinMoneyMin: int = 100
    checkinMoneyMax: int = 300
    # 物品奖励：留空 = 不给物品；{item} 与 {count}
    checkinItem: str = ''
    checkinItemCount: int = 1
    # 连签每多一天，额外多给多少金钱（0 = 不额外给）
    checkinStreakBonus: int = 0
    # 发放命令（两行，各一条）：{player} 玩家、{value} 金钱、{item} 物品 id、{count} 物品数量
    checkinMoneyCmd: str = 'eco give {player} {value}'
    checkinItemCmd: str = 'give {player} {item} {count}'
    # 文案：{player} 玩家、{money}/{value} 金钱、{item} 物品、{count} 数量、
    #       {items} 物品描述（没物品时为空）、{streak} 连签天数、{total} 累计次数
    checkinText: str = '【签到】{player} 签到成功！获得 {money} 金币{items}（连续 {streak} 天，累计 {total} 次）'
    checkinRepeatText: str = '【签到】{player} 今天已经签过啦，明天再来～（已连续 {streak} 天）'
    checkinOffText: str = '【签到】签到功能没开（服主可在「杂项」页打开）'

    # ---------------- 群发私聊（/msg 所有在线玩家）----------------
    # 每两个玩家之间的间隔（秒），防止刷太快被服务器当刷屏踢下线
    massGapSeconds: float = 0.5
    # 群发时是否跳过机器人自己和在线名单里不像玩家名的条目
    massSkipSelf: bool = True

    def validate(self) -> None:
        self.videoEnabled = bool(self.videoEnabled)
        src = str(self.videoSource or 'anime').strip().lower()
        self.videoSource = src if src in VIDEO_SOURCES else 'anime'
        self.videoCount = _clamp_int(self.videoCount, 1, 10)
        self.videoCooldownSeconds = _clamp_int(self.videoCooldownSeconds, 0, 3600)
        self.videoTitle = (str(self.videoTitle or '').strip()
                           or '【视频推荐】{player} 点的{source}前 {count} 条：')
        self.videoItemText = str(self.videoItemText or '').strip() or '{index}. {title} {owner} {link}'
        self.videoCoolText = (str(self.videoCoolText or '').strip()
                              or '【视频推荐】刚推过啦，{wait} 秒后再来（会换一批新的）')
        self.videoFailText = (str(self.videoFailText or '').strip()
                              or '【视频推荐】B 站接口暂时抽风了，稍等一下再试~')
        self.biliCookie = str(self.biliCookie or '').strip()
        self.biliFavMediaId = _clamp_int(self.biliFavMediaId, 0, 999_999_999)
        self.biliFavName = str(self.biliFavName or '').strip()
        self.luckEnabled = bool(self.luckEnabled)
        self.luckMax = _clamp_int(self.luckMax, 10, 1000)
        self.luckText = (str(self.luckText or '').strip()
                         or '【今日幸运值】{player}：{value}/{max} — {comment}（明天 0 点刷新）')
        self.checkinEnabled = bool(self.checkinEnabled)
        self.checkinMoneyMin = _clamp_int(self.checkinMoneyMin, 0, 10_000_000)
        self.checkinMoneyMax = _clamp_int(self.checkinMoneyMax, 0, 10_000_000)
        if self.checkinMoneyMax < self.checkinMoneyMin:
            self.checkinMoneyMin, self.checkinMoneyMax = self.checkinMoneyMax, self.checkinMoneyMin
        self.checkinItem = str(self.checkinItem or '').strip()
        self.checkinItemCount = _clamp_int(self.checkinItemCount, 0, 9999)
        self.checkinStreakBonus = _clamp_int(self.checkinStreakBonus, 0, 1_000_000)
        self.checkinMoneyCmd = (str(self.checkinMoneyCmd or '').strip()
                                or 'eco give {player} {value}')
        self.checkinItemCmd = (str(self.checkinItemCmd or '').strip()
                               or 'give {player} {item} {count}')
        self.checkinText = (str(self.checkinText or '').strip()
                            or '【签到】{player} 签到成功！获得 {money} 金币{items}'
                               '（连续 {streak} 天，累计 {total} 次）')
        self.checkinRepeatText = (str(self.checkinRepeatText or '').strip()
                                  or '【签到】{player} 今天已经签过啦，明天再来～（已连续 {streak} 天）')
        self.checkinOffText = (str(self.checkinOffText or '').strip()
                               or '【签到】签到功能没开（服主可在「杂项」页打开）')
        self.massGapSeconds = _clamp_float(self.massGapSeconds, 0.0, 10.0)
        self.massSkipSelf = bool(self.massSkipSelf)

    def source_name(self) -> str:
        """来源的中文名（给文案里的 {source} 用）。"""
        if (self.videoSource or '') == 'fav' and (self.biliFavName or '').strip():
            return '收藏夹「%s」' % str(self.biliFavName).strip()[:20]
        return VIDEO_SOURCES.get(self.videoSource or 'anime', VIDEO_SOURCES['anime'])


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    quiz: QuizConfig = field(default_factory=QuizConfig)
    autoeat: AutoEatConfig = field(default_factory=AutoEatConfig)
    healthAlert: HealthAlertConfig = field(default_factory=HealthAlertConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    commandBlock: CommandBlockConfig = field(default_factory=CommandBlockConfig)
    logForward: LogForwardConfig = field(default_factory=LogForwardConfig)
    qq: QqConfig = field(default_factory=QqConfig)
    roster: RosterConfig = field(default_factory=RosterConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)
    appearance: AppearanceConfig = field(default_factory=AppearanceConfig)
    docBook: DocBookConfig = field(default_factory=DocBookConfig)
    song: SongConfig = field(default_factory=SongConfig)
    affinity: AffinityConfig = field(default_factory=AffinityConfig)
    misc: MiscConfig = field(default_factory=MiscConfig)

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
        self.healthAlert.validate()
        self.verify.validate()
        self.commandBlock.validate()
        self.logForward.validate()
        self.qq.validate()
        self.reconnect.validate()
        self.appearance.validate()
        self.docBook.validate()
        self.song.validate()
        self.affinity.validate()
        self.misc.validate()

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
                    for section in ('server', 'auth', 'llm', 'quiz', 'autoeat', 'healthAlert',
                                    'verify', 'commandBlock', 'logForward', 'qq', 'roster',
                                    'reconnect', 'appearance', 'docBook', 'song', 'affinity',
                                    'misc'):
                        sub = data.get(section)
                        if isinstance(sub, dict):
                            cur = getattr(cfg, section)
                            for k, v in sub.items():
                                if hasattr(cur, k):
                                    setattr(cur, k, v)
                    # Migrate the old flat QQ keys (pre per-framework split) into
                    # the sub-config of the framework that was selected.
                    qq_data = data.get('qq')
                    if isinstance(qq_data, dict):
                        flat_keys = ('mode', 'url', 'accessToken', 'listenHost',
                                     'listenPort', 'launcherPath', 'autoConfigNewAccounts')
                        flat = {k: qq_data[k] for k in flat_keys if k in qq_data}
                        if flat:
                            fw = ('napcat' if str(qq_data.get('framework', '')).lower() == 'napcat'
                                  else 'snowluma')
                            if not isinstance(qq_data.get(fw), dict):
                                target = getattr(cfg.qq, fw, None)
                                if target is not None and not isinstance(target, dict):
                                    for k, v in flat.items():
                                        if hasattr(target, k):
                                            setattr(target, k, v)
            except Exception as e:
                print(f'[mcai] Failed to read {path}, using defaults: {e}')
        cfg.validate()
        return cfg

    def save(self, path: Optional[str] = None) -> bool:
        """写盘。返回 ``True`` 表示成功，``False`` 表示失败（原文件保持不变）。

        这份配置里有服务器地址、认证信息和 AI Key，一次写入失败（磁盘满、文件
        被占用）就会让整份归零，所以必须原子替换 + 把成败传给调用方。
        """
        path = path or APP_CONFIG_FILE
        return _atomic_write_json(path, self.to_dict())


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
    def _save_list(templates: List[Dict[str, str]]) -> bool:
        return _atomic_write_json(PROMPT_TEMPLATES_FILE, templates)

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
