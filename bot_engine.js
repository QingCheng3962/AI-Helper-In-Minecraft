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
//   {"event":"msaCode","user_code":..., "verification_uri":...}
// Commands (stdin, one JSON per line):
//   {"type":"chat","text":...}
//   {"type":"setAutoeat","enabled":bool}
//   {"type":"setQuiz","enabled":bool}
//   {"type":"ping"}
//   {"type":"quit"}

const fs = require('fs');
const readline = require('readline');
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

    // Guild / public with » or : separator, optional |[guild] prefix
    match = message.match(/^(?:\|\[(.*?)\])?\s*([A-Za-z0-9_]{1,16})\s*[»>:]\s*(.+)$/);
    if (match) {
        return { player: match[2].trim().replace('|', ''), content: match[3].trim(), isPrivate: false, guild: match[1] || null };
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
let quiz = null;
let autoEat = null;

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

// --- Bot events ---
let spawnDone = false;
let endEmitted = false;
let unlockTimer = null;

function ensureEnd(reason) {
    if (spawnDone || endEmitted) return;
    if (unlockTimer) { clearTimeout(unlockTimer); unlockTimer = null; }
    emitEvent({ event: 'end', reason: String(reason == null ? '' : reason) });
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
    if (unlockTimer) { clearTimeout(unlockTimer); unlockTimer = null; }
    emitEvent({ event: 'spawn', username: bot.username, players: Object.keys(bot.players) });
});

bot.on('kicked', (reason, loggedIn) => {
    const msg = typeof reason === 'string' ? reason : JSON.stringify(reason);
    emitEvent({ event: 'kicked', reason: msg, loggedIn: !!loggedIn });
    ensureEnd('kicked');
});

bot.on('error', (e) => {
    emitEvent({ event: 'error', message: e && e.message ? e.message : String(e) });
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
            if (bot && typeof bot.chat === 'function') {
                bot.chat(text);
                emitEvent({ event: 'chatSent', text });
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
            if (quiz && quiz.config) quiz.config.enabled = runtime.quiz.enabled;
            log('info', 'Quiz ' + (runtime.quiz.enabled ? 'enabled' : 'disabled'));
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
