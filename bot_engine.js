'use strict';

// Headless mineflayer bot engine controlled by the Python app over stdio.
// Usage: node bot_engine.js <runtime-config.json>
// runtime config auth.type: "offline" | "littleskin" | "microsoft"
// Events (stdout, one JSON per line):
//   {"event":"log","level":..., "message":...}
//   {"event":"spawn","username":..., "players":[...]}
//   {"event":"chat","player":..., "content":..., "isPrivate":bool, "guild":...}
//   {"event":"actionBar","text":...}
//   {"event":"health","health":..., "food":...}
//   {"event":"kicked","reason":..., "loggedIn":bool}
//   {"event":"error","message":...}
//   {"event":"end","reason":...}
//   {"event":"chatSent","text":...}
//   {"event":"bookResult","ok":bool,"message":...}
//   {"event":"bookProgress","nextPage":int,"nextBook":int,"done":int,"total":int}
//   {"event":"msaCode","user_code":..., "verification_uri":...}
// Commands (stdin, one JSON per line):
//   {"type":"chat","text":...}
//   {"type":"setAutoeat","enabled":bool}
//   {"type":"setQuiz","enabled":bool}
//   {"type":"writeBook","pages":[...],"startPage":int,"bookStart":int,"signBook":bool,"titleTemplate":str,"startText":...,"endText":...,"endDrop":bool,"autoGetBook":bool,"pagesPerBook":int}
//   {"type":"clearBook"}
//   {"type":"bookControl","action":"stop"|"pause"}
//   {"type":"listPlayers"}   -> {"event":"players","players":[...]}   (在线玩家名单，群发私信用)
//   {"type":"ping"}
//   {"type":"quit"}

const fs = require('fs');
const readline = require('readline');

// --- 给外置登录(yggdrasil)的 HTTP 请求加 20s 硬超时 ---
// yggdrasil 用的是 node-fetch 且没设超时，皮肤站(sessionserver/join)挂掉时会一直挂住。
// 在加载 mineflayer（会间接加载 yggdrasil）之前，把 node-fetch 换成带 AbortSignal.timeout 的版本。
const AUTH_TIMEOUT_MS = parseInt(process.env.AAFM_AUTH_TIMEOUT_MS || '20000', 10) || 20000;
try {
    const nodeFetch = require('node-fetch');
    if (nodeFetch && !nodeFetch.__aafmTimeout && typeof AbortSignal !== 'undefined'
        && typeof AbortSignal.timeout === 'function') {
        const wrapped = function (url, options) {
            const opts = Object.assign({}, options || {});
            if (!opts.signal) {
                try { opts.signal = AbortSignal.timeout(AUTH_TIMEOUT_MS); } catch (e) { /* ignore */ }
            }
            return nodeFetch(url, opts);
        };
        Object.assign(wrapped, nodeFetch);
        wrapped.__aafmTimeout = true;
        wrapped.default = wrapped;
        require.cache[require.resolve('node-fetch')].exports = wrapped;
    }
} catch (e) { /* 没有 node-fetch 就跳过 */ }

const mineflayer = require('mineflayer');

function emitEvent(obj) {
    process.stdout.write(JSON.stringify(obj) + '\n');
}

function log(level, message) {
    emitEvent({ event: 'log', level, message: String(message) });
}

function logError(message) {
    emitEvent({ event: 'log', level: 'error', message: String(message) });
}

function makeLogger() {
    return {
        info: (m) => log('info', m),
        warn: (m) => log('warn', m),
        error: (m) => log('error', m)
    };
}

// --- Chat parsing (server-specific formats + standard vanilla) ---
function parseChat(message) {
    if (typeof message !== 'string' || !message) return null;

    // Private message: [player -> me] content
    let match = message.match(/^\[(.*?)\s*->\s*me\]\s*(.*)$/);
    if (match) {
        return { player: match[1].trim(), content: match[2].trim(), isPrivate: true, guild: null };
    }

    // Guild / public with » / > / : separator.
    // Allows an optional leading '|' and zero or more [title] prefixes,
    // e.g. "|[工会]Name » hi", "|Name » hi", "[工会]Name » hi", "Name » hi".
    match = message.match(/^\|?\s*((?:\[[^\]]*\]\s*)*)([A-Za-z0-9_]{1,16})\s*[»>:]\s*(.+)$/);
    if (match) {
        const prefix = match[1] || '';
        const gm = prefix.match(/\[([^\]]*)\]/);
        return {
            player: match[2].trim(),
            content: match[3].trim(),
            isPrivate: false,
            guild: gm ? gm[1].trim() : null
        };
    }

    // Standard vanilla: <player> content
    match = message.match(/^<([^>]{1,32})>\s*(.+)$/);
    if (match) {
        return { player: match[1].trim(), content: match[2].trim(), isPrivate: false, guild: null };
    }

    // [player] content  (player-like name to avoid matching system tags)
    match = message.match(/^\[([A-Za-z0-9_]{1,16})\]\s*(.+)$/);
    if (match) {
        return { player: match[1].trim(), content: match[2].trim(), isPrivate: false, guild: null };
    }

    return null;
}

// --- Config ---
const configPath = process.argv[2];
if (!configPath) {
    console.error('usage: node bot_engine.js <runtime-config.json>');
    process.exit(1);
}

let config;
try {
    config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
} catch (e) {
    console.error('Failed to read runtime config: ' + e.message);
    process.exit(1);
}

const server = config.server || {};
const auth = config.auth || { type: 'offline' };
const quizCfg = config.quiz || {};
const autoEatCfg = config.autoeat || {};

// Mutable runtime toggles shared with quiz / autoeat
const runtime = {
    autoeat: { enabled: !!autoEatCfg.enabled },
    quiz: { enabled: !!quizCfg.enabled }
};

// --- Auth: offline / littleskin / microsoft ---
// Third-party yggdrasil (LittleSkin): reuse a stored yggdrasil session and point
// the join-validation (sessionserver) at the configured skin site.
function makeLittleSkinAuth(site) {
    const sessionServer = site + '/api/yggdrasil/sessionserver';
    return function littleskinAuth(client, options) {
        client.username = options.username;
        client.uuid = options.uuid;
        options.sessionServer = sessionServer;
        options.auth = 'mojang';
        client.session = options.loginSession;
        options.haveCredentials = true;
        options.connect(client);
    };
}

function stripTrailing(s) {
    return String(s == null ? '' : s).trim().replace(/\/+$/, '');
}

const botOptions = {
    host: server.host,
    port: server.port || 25565,
    username: auth.username || 'AI_Bot',
    version: server.version || undefined,
    viewDistance: server.viewDistance || 'tiny',
    brand: server.brand || 'aafm-python',
    hideErrors: true,
    logErrors: false,
    auth: 'offline'
};

if (auth.type === 'littleskin') {
    const site = stripTrailing(auth.site) || 'https://littleskin.cn';
    if (auth.session && auth.uuid) {
        botOptions.username = auth.username;
        botOptions.uuid = auth.uuid;
        botOptions.auth = makeLittleSkinAuth(site);
        botOptions.loginSession = auth.session;
        botOptions.accessToken = auth.session.accessToken;
        botOptions.clientToken = auth.session.clientToken;
        log('info', 'LittleSkin: ' + site + '  (user ' + auth.username + ')');
    } else {
        log('warn', 'LittleSkin 登录信息缺失，将以离线身份尝试进入。请先在 GUI 设置页完成 LittleSkin 登录。');
    }
} else if (auth.type === 'microsoft') {
    botOptions.auth = 'microsoft';
    botOptions.username = auth.username || 'MSA_Account';
    if (auth.profilesFolder) botOptions.profilesFolder = auth.profilesFolder;
    botOptions.onMsaCode = (data) => {
        const obj = Object.assign({ event: 'msaCode' }, data || {});
        emitEvent(obj);
        // Keep a plain-text copy in the log stream for console users too.
        log('info', '[正版登录] 请在浏览器打开 ' + (data && data.verification_uri || 'https://www.microsoft.com/link') +
            ' 并输入代码 ' + (data && data.user_code || '') + ' 完成微软账户授权。');
    };
    log('info', '正版登录(微软): 使用账户标识 ' + botOptions.username);
}

let bot;
try {
    bot = mineflayer.createBot(botOptions);
} catch (e) {
    logError('createBot failed: ' + (e && e.message));
    process.exit(1);
}

// --- Optional handlers (quiz / autoeat from the original small project) ---
let QuizHandler = null;
let AutoEat = null;
let VerifyHandler = null;
let quiz = null;
let autoEat = null;
let verify = null;

try {
    QuizHandler = require('./quiz').QuizHandler;
} catch (e) {
    logError('quiz module not available: ' + e.message);
}
try {
    AutoEat = require('./autoeat').AutoEat;
} catch (e) {
    logError('autoeat module not available: ' + e.message);
}
try {
    VerifyHandler = require('./verify').VerifyHandler;
} catch (e) {
    logError('verify module not available: ' + e.message);
}

const engineLogger = makeLogger();

if (QuizHandler) {
    const quizConfig = {
        quiz: {
            enabled: runtime.quiz.enabled,
            minDelay: quizCfg.minDelay,
            questionBank: quizCfg.questionBank
        },
        llm: config.llm || {}
    };
    const chatLog = { info: (m) => emitEvent({ event: 'quizLog', message: String(m) }) };
    try {
        quiz = new QuizHandler(bot, chatLog, engineLogger, quizConfig);
    } catch (e) {
        logError('quiz init: ' + e.message);
    }
}

if (AutoEat) {
    try {
        // Construct with enabled=false so the engine drives the health check itself.
        autoEat = new AutoEat(bot, engineLogger, { autoeat: { enabled: false } });
        autoEat.enabled = runtime.autoeat.enabled;
    } catch (e) {
        logError('autoeat init: ' + e.message);
    }
}

if (VerifyHandler) {
    try {
        verify = new VerifyHandler(bot, engineLogger, config);
        if (verify && verify.enabled) {
            log('info', '人机验证自动通过已启用。');
        }
    } catch (e) {
        logError('verify init: ' + e.message);
    }
}

// --- Bot events ---
let spawnDone = false;
let endEmitted = false;
let unlockTimer = null;
const chatQueue = [];

// Some mineflayer / minecraft-protocol combinations don't set `client.chat`
// when the server's configuration handshake is unusual (proxies, reconnects).
// Patch a working implementation so sending never throws.
function ensureClientChat() {
    try {
        if (!bot || !bot._client || typeof bot._client.chat === 'function') return;
        if (typeof bot._client._signedChat === 'function') {
            bot._client.chat = bot._client._signedChat;
        } else {
            bot._client.chat = (message) => {
                try {
                    bot._client.write('chat_message', {
                        message,
                        timestamp: BigInt(Date.now()),
                        salt: 0n,
                        signature: undefined,
                        offset: 0
                    });
                } catch (e) {
                    bot._client.write('chat', { message });
                }
            };
        }
        log('warn', '已为客户端补上 chat 发送接口。');
    } catch (e) { /* ignore */ }
}

function sendChat(text) {
    if (!text) return false;
    ensureClientChat();
    if (bot && typeof bot.chat === 'function'
        && bot._client && typeof bot._client.chat === 'function') {
        bot.chat(text);
        return true;
    }
    return false;
}

function ensureEnd(reason) {
    if (spawnDone || endEmitted) return;
    if (unlockTimer) { clearTimeout(unlockTimer); unlockTimer = null; }
    emitEvent({ event: 'end', reason: String(reason == null ? '' : reason) });
}

// --- Document -> book and quill (书与笔) ----------------------------------
const bookwriter = require('./bookwriter');

// Set when the GUI asks to pause/stop the current write; the writer checks it
// between books.
let bookStop = false;

async function handleWriteBook(cmd) {
    if (!spawnDone) {
        emitEvent({ event: 'bookResult', ok: false, message: '尚未进入服务器，无法写入书与笔。' });
        return;
    }
    bookStop = false;
    try {
        const result = await bookwriter.writeBooks(
            bot,
            {
                pages: cmd.pages,
                startPage: cmd.startPage,
                bookStart: cmd.bookStart,
                signBook: !!(cmd.signBook),
                titleTemplate: cmd.titleTemplate,
                startText: cmd.startText,
                endText: cmd.endText,
                endDrop: cmd.endDrop,
                autoGetBook: cmd.autoGetBook !== false,
                pagesPerBook: cmd.pagesPerBook,
                shouldStop: () => bookStop,
                onProgress: (progress) => emitEvent({
                    event: 'bookProgress',
                    nextPage: progress.nextPage,
                    nextBook: progress.nextBook,
                    done: progress.done,
                    total: progress.total,
                    signed: progress.signed
                })
            },
            {
                log: (level, message) => log(level, message),
                error: (message) => logError(message)
            },
            (text) => { try { sendChat(text); } catch (e) { /* ignore */ } }
        );
        emitEvent({
            event: 'bookResult',
            ok: !!(result && result.ok),
            stopped: !!(result && result.stopped),
            nextPage: (result && result.nextPage) || 0,
            message: (result && result.message) || ''
        });
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        logError('写入书与笔出错: ' + msg);
        emitEvent({ event: 'bookResult', ok: false, message: '写入失败：' + msg });
    }
}

function handleBookControl(cmd) {
    const action = String((cmd && cmd.action) || 'stop');
    bookStop = true;
    log('info', '书与笔写入：收到' + (action === 'pause' ? '暂停' : '停止') + '请求，将在当前这本写完后停下。');
}


async function handleClearBook() {
    if (!spawnDone) {
        emitEvent({ event: 'bookResult', ok: false, message: '尚未进入服务器，无法清空书与笔。' });
        return;
    }
    try {
        const result = await bookwriter.clearBooks(bot, {
            log: (level, message) => log(level, message),
            error: (message) => logError(message)
        });
        emitEvent({
            event: 'bookResult',
            ok: !!(result && result.ok),
            message: (result && result.message) || ''
        });
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        logError('清空书与笔出错: ' + msg);
        emitEvent({ event: 'bookResult', ok: false, message: '清空失败：' + msg });
    }
}


bot.on('message', (jsonMsg, position) => {
    if (position === 'game_info') return;
    const raw = jsonMsg.toString();
    emitEvent({ event: 'message', raw });

    if (quiz) quiz.onChat(raw);

    const chat = parseChat(raw);
    if (chat) {
        if (chat.player && bot.username && chat.player.toLowerCase() === bot.username.toLowerCase()) return;
        emitEvent({
            event: 'chat',
            player: chat.player,
            content: chat.content,
            isPrivate: !!chat.isPrivate,
            guild: chat.guild,
            raw: raw
        });
        return;
    }
    // Anything that is not a player chat (server announcements, command
    // output, join/leave/death messages, plugin text...) is surfaced so the
    // user can see it in the log instead of being silently dropped.
    if (raw && raw.trim()) {
        emitEvent({ event: 'serverMessage', raw: raw });
    }
});

bot.on('actionBar', (jsonMsg) => {
    const text = jsonMsg.toString();
    if (!text || text === ' ') return;
    emitEvent({ event: 'actionBar', text });
    if (quiz) quiz.onActionBar(text);
});

bot.once('spawn', () => {
    spawnDone = true;
    ensureClientChat();
    if (unlockTimer) { clearTimeout(unlockTimer); unlockTimer = null; }
    emitEvent({ event: 'spawn', username: bot.username, players: Object.keys(bot.players) });
    while (chatQueue.length) {
        const queued = chatQueue.shift();
        if (sendChat(queued)) emitEvent({ event: 'chatSent', text: queued });
    }
});

bot.on('kicked', (reason, loggedIn) => {
    const msg = typeof reason === 'string' ? reason : JSON.stringify(reason);
    emitEvent({ event: 'kicked', reason: msg, loggedIn: !!loggedIn });
    ensureEnd('kicked');
});

// 把「外置登录 / 网络服务」类错误归类成友好提示，避免一屏看不懂的原始报错。
// 典型来源：yggdrasil 包在 LittleSkin sessionserver 返回 5xx(HTML) 时抛出的
// "Response is not JSON. Status code: 504"。
function friendlyError(raw, name) {
    const m = String(raw || '');
    const n = String(name || '');
    if (/AbortError/i.test(n) || /aborted|timed?\s?out|timeout/i.test(m)) {
        return {
            kind: 'timeout',
            message: '外置登录 / 网络请求超时（对方服务太慢或不可达），不是你的问题',
        };
    }
    if (/Response is not JSON/i.test(m)
        || /Status code:\s*5\d\d/i.test(m)
        || /HTTP\s*5\d\d/i.test(m)
        || /\b50[0-9]\b/.test(m)) {
        return {
            kind: 'http5xx',
            message: '外置登录 / 会话服务返回 5xx（对方服务暂时不可用，不是你的问题）',
        };
    }
    if (/(ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|socket hang up|fetch failed|network error|getaddrinfo)/i.test(m)) {
        return {
            kind: 'network',
            message: '连接服务失败（网络超时 / 对方不可达）',
        };
    }
    return null;
}

bot.on('error', (e) => {
    const raw = e && e.message ? e.message : String(e);
    const friendly = friendlyError(raw, e && e.name);
    if (friendly) {
        // 交给 Python 去做退避重试 + 明确提示
        emitEvent({ event: 'serviceError', kind: friendly.kind,
                    message: friendly.message, detail: raw });
    } else {
        emitEvent({ event: 'error', message: raw });
    }
    // Unlock the UI if we fail before ever reaching spawn (auth / connect errors).
    if (!spawnDone && unlockTimer == null) {
        unlockTimer = setTimeout(() => ensureEnd('error'), 1500);
    }
});

bot.on('end', (reason) => {
    endEmitted = true;
    if (unlockTimer) { clearTimeout(unlockTimer); unlockTimer = null; }
    emitEvent({ event: 'end', reason: reason == null ? '' : String(reason) });
});

bot.on('health', () => {
    emitEvent({ event: 'health', health: bot.health, food: bot.food });
    if (autoEat) autoEat._check();
});

// --- Stdin commands ---
const rl = readline.createInterface({ input: process.stdin, terminal: false });

function handleCommand(cmd) {
    switch (cmd.type) {
        case 'chat': {
            const text = String(cmd.text || '');
            if (!text) return;
            if (!spawnDone) {
                // Not in play state yet: queue and send right after spawn.
                chatQueue.push(text);
                if (chatQueue.length > 50) chatQueue.shift();
                log('info', '消息已排队（等待进入服务器后发送）。');
                return;
            }
            if (sendChat(text)) {
                emitEvent({ event: 'chatSent', text });
            } else {
                logError('发送失败：客户端聊天接口未就绪。');
            }
            break;
        }
        case 'setAutoeat':
            runtime.autoeat.enabled = !!cmd.enabled;
            if (autoEat) autoEat.enabled = runtime.autoeat.enabled;
            log('info', 'AutoEat ' + (runtime.autoeat.enabled ? 'enabled' : 'disabled'));
            break;
        case 'setQuiz':
            runtime.quiz.enabled = !!cmd.enabled;
            if (quiz) quiz.setEnabled(runtime.quiz.enabled);
            log('info', 'Quiz ' + (runtime.quiz.enabled ? 'enabled' : 'disabled'));
            break;
        case 'writeBook':
            handleWriteBook(cmd).catch((e) => logError(
                'writeBook: ' + (e && e.message ? e.message : e)));
            break;
        case 'bookControl':
            handleBookControl(cmd);
            break;
        case 'clearBook':
            handleClearBook().catch((e) => logError(
                'clearBook: ' + (e && e.message ? e.message : e)));
            break;
        case 'listPlayers':
            try {
                const names = Object.keys((bot && bot.players) || {})
                    .filter((n) => n && n !== (bot && bot.username));
                emitEvent({ event: 'players', players: names });
            } catch (e) {
                emitEvent({ event: 'players', players: [] });
            }
            break;
        case 'ping':
            emitEvent({ event: 'pong' });
            break;
        case 'quit':
            try { if (bot) bot.end('quit'); } catch (e) { /* ignore */ }
            process.exit(0);
            break;
        default:
            log('warn', 'Unknown command: ' + cmd.type);
    }
}

rl.on('line', (line) => {
    line = line.trim();
    if (!line) return;
    let cmd;
    try {
        cmd = JSON.parse(line);
    } catch (e) {
        logError('Bad command: ' + line);
        return;
    }
    try {
        handleCommand(cmd);
    } catch (e) {
        logError('Command error: ' + e.message);
    }
});

log('info', 'Bot engine started. Waiting for spawn...');
