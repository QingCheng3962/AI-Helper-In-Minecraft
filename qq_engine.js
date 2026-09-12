'use strict';

// OneBot 11 QQ group-message relay engine, controlled by the Python app over stdio.
// Usage: node qq_engine.js <qq-runtime-config.json>
//
// Config fields:
//   mode        "forward" (connect to the OneBot WS server, e.g. NapCat/Lagrange)
//               "reverse" (listen and let the OneBot server connect to us)
//   url         forward-mode WebSocket URL, e.g. ws://127.0.0.1:3001
//   accessToken optional token (sent/received as "Authorization: Bearer <token>")
//   listenHost  reverse-mode bind host (default 0.0.0.0)
//   listenPort  reverse-mode bind port (default 3002)
//   groups      array of group ids (strings) to relay; empty = every group
//
// Events (stdout, one JSON per line):
//   {"event":"log","level":..., "message":...}
//   {"event":"status","state":"connected"|"disconnected","mode":...}
//   {"event":"groups","groups":[{"id":...,"name":...}]}
//   {"event":"qqMessage","groupId":...,"groupName":...,"sender":...,"content":...,"raw":...}
//   {"event":"error","message":...}
//   {"event":"end","reason":...}
// Commands (stdin, one JSON per line):
//   {"type":"sendGroup","groupId":...,"text":...}
//   {"type":"setGroups","groups":[...]}
//   {"type":"ping"}
//   {"type":"quit"}

const fs = require('fs');
const readline = require('readline');

let WebSocket;
try {
    WebSocket = require('ws');
} catch (e) {
    process.stdout.write(JSON.stringify({
        event: 'error',
        message: 'ws module not found (run "npm install"): ' + e.message
    }) + '\n');
    process.exit(1);
}

function emitEvent(obj) {
    process.stdout.write(JSON.stringify(obj) + '\n');
}

function log(level, message) {
    emitEvent({ event: 'log', level, message: String(message) });
}

function logError(message) {
    emitEvent({ event: 'error', message: String(message) });
}

// Replace characters that could break the chat transport (control chars,
// lone surrogates, Unicode noncharacters) with '?'. Normal text, emoji and
// the '&' color codes are kept intact.
function sanitizeText(value) {
    if (value == null) return '';
    const s = String(value);
    let out = '';
    for (let i = 0; i < s.length; i++) {
        const code = s.charCodeAt(i);
        if (code >= 0xD800 && code <= 0xDBFF) {
            const next = s.charCodeAt(i + 1);
            if (next >= 0xDC00 && next <= 0xDFFF) {
                out += s[i] + s[i + 1];
                i++;
            } else {
                out += '?';
            }
        } else if (code >= 0xDC00 && code <= 0xDFFF) {
            out += '?';
        } else if (code < 0x20 || code === 0x7F ||
                   (code >= 0x80 && code <= 0x9F) ||
                   code === 0xFFFE || code === 0xFFFF) {
            out += '?';
        } else {
            out += s[i];
        }
    }
    return out;
}

// --- Config ---
const configPath = process.argv[2];
if (!configPath) {
    console.error('usage: node qq_engine.js <qq-runtime-config.json>');
    process.exit(1);
}

let config = {};
try {
    config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
} catch (e) {
    console.error('Failed to read qq runtime config: ' + e.message);
    process.exit(1);
}

const mode = config.mode === 'reverse' ? 'reverse' : 'forward';
const accessToken = String(config.accessToken || '');
const listenHost = String(config.listenHost || '0.0.0.0');
const listenPort = parseInt(config.listenPort, 10) || 3002;
let groupFilter = new Set((Array.isArray(config.groups) ? config.groups : [])
    .map((g) => String(g == null ? '' : g).trim())
    .filter(Boolean));

const groupNames = new Map();
let stopped = false;
const activeMode = mode;
let reconnectTimer = null;
let retry = 0;
let lastState = null;
const clients = new Set();
let forwardWs = null;

function setStatus(state, extra) {
    const payload = Object.assign({ event: 'status', state, mode: activeMode }, extra || {});
    emitEvent(payload);
    lastState = state;
}

// --- OneBot message parsing ---
function atText(data) {
    const q = data && data.qq;
    if (q === 'all' || q === 0 || q === '0') return '@全体成员';
    const name = data && (data.name || data.card);
    return '@' + (name || q || '');
}

function segmentText(seg) {
    if (!seg || typeof seg !== 'object') return '';
    const data = seg.data || {};
    switch (seg.type) {
        case 'text':
            return String(data.text == null ? '' : data.text);
        case 'at':
            return atText(data);
        case 'image':
            return '[图片]';
        case 'face':
        case 'mface':
            return '[表情]';
        case 'record':
            return '[语音]';
        case 'video':
            return '[视频]';
        case 'file':
            return '[文件]';
        case 'json':
        case 'xml':
            return '[卡片]';
        case 'forward':
            return '[合并转发]';
        case 'share':
            return '[链接]';
        case 'location':
            return '[位置]';
        case 'music':
            return '[音乐]';
        case 'reply':
            return '';
        default:
            return '[' + seg.type + ']';
    }
}

function extractText(message) {
    if (message == null) return '';
    if (typeof message === 'string') return message.trim();
    if (!Array.isArray(message)) return '';
    let out = '';
    for (const seg of message) out += segmentText(seg);
    return sanitizeText(out.replace(/\r?\n/g, ' ').replace(/\s+/g, ' ').trim());
}

function senderName(event) {
    const s = (event && event.sender) || {};
    const card = s.card ? String(s.card).trim() : '';
    const nick = s.nickname ? String(s.nickname).trim() : '';
    if (card && card !== nick) return card;
    if (nick) return nick;
    if (card) return card;
    if (s.user_id != null) return 'QQ' + s.user_id;
    return '未知';
}

function groupName(groupId) {
    return groupNames.get(groupId) || ('群' + groupId);
}

// --- Outgoing OneBot actions ---
function rawSend(text) {
    let sent = false;
    if (activeMode === 'forward') {
        if (forwardWs && forwardWs.readyState === WebSocket.OPEN) {
            forwardWs.send(text);
            sent = true;
        }
    } else {
        for (const ws of clients) {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(text);
                sent = true;
            }
        }
    }
    return sent;
}

function sendAction(action, params, echo) {
    const payload = { action, params: params || {}, echo: echo || (action + '_' + Date.now()) };
    rawSend(JSON.stringify(payload));
    return payload.echo;
}

function requestGroupList() {
    sendAction('get_group_list', {}, 'get_group_list');
}

// --- Incoming OneBot events ---
function handleGroupList(data) {
    if (!Array.isArray(data)) return;
    groupNames.clear();
    const list = [];
    for (const g of data) {
        if (!g) continue;
        const id = String(g.group_id != null ? g.group_id : g.groupId);
        if (!id) continue;
        const name = sanitizeText(g.group_name || g.groupName || ('群' + id));
        groupNames.set(id, name);
        list.push({ id, name });
    }
    emitEvent({ event: 'groups', groups: list });
}

function handleObj(obj) {
    if (!obj || typeof obj !== 'object') return;

    if (obj.echo === 'get_group_list') {
        handleGroupList(obj.data);
        return;
    }
    if (obj.post_type === 'meta_event') {
        if (obj.meta_event_type === 'lifecycle' && obj.self_id != null) {
            setStatus('connected', { selfId: String(obj.self_id) });
        }
        return;
    }
    if (obj.post_type !== 'message' || obj.message_type !== 'group') return;

    const groupId = String(obj.group_id);
    if (groupFilter.size && !groupFilter.has(groupId)) return;

    const content = extractText(obj.message != null ? obj.message : obj.raw_message);
    if (!content) return;

    emitEvent({
        event: 'qqMessage',
        groupId,
        groupName: sanitizeText(groupName(groupId)),
        sender: sanitizeText(senderName(obj)),
        content,
        raw: sanitizeText(obj.raw_message || '')
    });
}

function handleRaw(data) {
    if (data == null) return;
    let text = typeof data === 'string' ? data : data.toString('utf8');
    text = text.trim();
    if (!text) return;
    let obj;
    try {
        obj = JSON.parse(text);
    } catch (e) {
        return;
    }
    if (Array.isArray(obj)) obj.forEach(handleObj);
    else handleObj(obj);
}

// --- Forward mode (we connect out to the OneBot WS server) ---
function scheduleReconnect(reason) {
    if (stopped || activeMode !== 'forward') return;
    retry += 1;
    const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(retry, 5)));
    log('warn', 'QQ 连接断开（' + reason + '），' + Math.round(delay / 1000) + ' 秒后重连…');
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectForward, delay);
}

function connectForward() {
    if (stopped || activeMode !== 'forward') return;
    let url = String(config.url || '').trim() || 'ws://127.0.0.1:3001';
    if (accessToken && !/[?&]access_token=/.test(url)) {
        url += (url.indexOf('?') >= 0 ? '&' : '?') + 'access_token=' + encodeURIComponent(accessToken);
    }
    const options = {};
    if (accessToken) options.headers = { Authorization: 'Bearer ' + accessToken };

    let ws;
    try {
        ws = new WebSocket(url, options);
    } catch (e) {
        logError('无法创建 QQ WebSocket: ' + (e && e.message));
        scheduleReconnect('create failed');
        return;
    }
    forwardWs = ws;

    ws.on('open', () => {
        retry = 0;
        log('info', '已连接 OneBot 正向 WebSocket: ' + url);
        setStatus('connected');
        requestGroupList();
    });
    ws.on('message', (data) => handleRaw(data));
    ws.on('close', (code, reason) => {
        if (lastState !== 'disconnected') setStatus('disconnected');
        const why = reason ? reason.toString() : String(code);
        scheduleReconnect('closed ' + why);
    });
    ws.on('error', (err) => {
        const code = err && err.code;
        const msg = err && err.message ? err.message : String(err);
        if (code === 'ECONNREFUSED') {
            logError('无法连接 ' + url + '（ECONNREFUSED）。请在 NapCat 的「网络配置」里开启'
                + ' OneBot 11 的 WebSocket 服务器，端口与这里保持一致；或改选 reverse（反向监听）模式。');
        } else if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') {
            logError('无法解析地址 ' + url + '（' + code + '）。请检查地址是否填写正确。');
        } else {
            logError('QQ WebSocket 错误: ' + msg);
        }
    });
}

// --- Reverse mode (the OneBot WS server connects in to us) ---
function startReverse() {
    if (typeof WebSocket.Server !== 'function') {
        logError('当前 ws 版本不支持反向 WebSocket，请使用正向模式。');
        emitEvent({ event: 'end', reason: 'reverse-unsupported' });
        process.exit(1);
    }
    let wss;
    try {
        wss = new WebSocket.Server({ host: listenHost, port: listenPort });
    } catch (e) {
        logError('无法监听 ' + listenHost + ':' + listenPort + ' -> ' + (e && e.message));
        emitEvent({ event: 'end', reason: 'listen-failed' });
        process.exit(1);
    }

    wss.on('listening', () => {
        log('info', 'QQ 反向 WebSocket 正在监听 ' + listenHost + ':' + listenPort);
    });

    wss.on('connection', (ws, req) => {
        if (accessToken) {
            const header = (req.headers && (req.headers['authorization'] || req.headers['Authorization'])) || '';
            let token = String(header).replace(/^Bearer\s+/i, '').trim();
            if (!token && req.url && req.url.indexOf('access_token=') >= 0) {
                const q = req.url.split('?')[1] || '';
                for (const pair of q.split('&')) {
                    const kv = pair.split('=');
                    if (kv[0] === 'access_token') token = decodeURIComponent(kv[1] || '');
                }
            }
            if (token !== accessToken) {
                log('warn', '拒绝未授权连接（access token 不匹配）。');
                try { ws.close(1008, 'invalid token'); } catch (e) { /* ignore */ }
                return;
            }
        }
        clients.add(ws);
        log('info', 'OneBot 反向连接已建立。');
        setStatus('connected');
        requestGroupList();

        ws.on('message', (data) => handleRaw(data));
        const drop = () => {
            clients.delete(ws);
            if (clients.size === 0) setStatus('disconnected');
        };
        ws.on('close', drop);
        ws.on('error', drop);
    });

    wss.on('error', (err) => {
        logError('QQ 反向 WebSocket 错误: ' + (err && err.message ? err.message : String(err)));
    });
}

// --- Stdin commands ---
const rl = readline.createInterface({ input: process.stdin, terminal: false });

function handleCommand(cmd) {
    switch (cmd.type) {
        case 'sendGroup': {
            const gid = String(cmd.groupId == null ? '' : cmd.groupId).trim();
            const text = String(cmd.text || '');
            if (!gid || !text) return;
            sendAction('send_group_msg', { group_id: gid, message: text });
            break;
        }
        case 'setGroups':
            groupFilter = new Set((Array.isArray(cmd.groups) ? cmd.groups : [])
                .map((g) => String(g == null ? '' : g).trim())
                .filter(Boolean));
            log('info', 'QQ 群过滤已更新：' + (groupFilter.size ? Array.from(groupFilter).join(', ') : '全部群'));
            break;
        case 'ping':
            emitEvent({ event: 'pong' });
            break;
        case 'quit':
            stopped = true;
            if (reconnectTimer) clearTimeout(reconnectTimer);
            try { if (forwardWs) forwardWs.close(); } catch (e) { /* ignore */ }
            for (const ws of clients) { try { ws.close(); } catch (e) { /* ignore */ } }
            emitEvent({ event: 'end', reason: 'quit' });
            setTimeout(() => process.exit(0), 100);
            break;
        default:
            log('warn', '未知命令: ' + cmd.type);
    }
}

rl.on('line', (line) => {
    line = line.trim();
    if (!line) return;
    let cmd;
    try {
        cmd = JSON.parse(line);
    } catch (e) {
        logError('非法命令: ' + line);
        return;
    }
    try {
        handleCommand(cmd);
    } catch (e) {
        logError('命令执行出错: ' + e.message);
    }
});

process.on('SIGINT', () => { handleCommand({ type: 'quit' }); });
process.on('SIGTERM', () => { handleCommand({ type: 'quit' }); });

// --- Boot ---
if (activeMode === 'reverse') {
    startReverse();
} else {
    connectForward();
}

log('info', 'QQ 转述引擎已启动（模式: ' + activeMode + '）。');
