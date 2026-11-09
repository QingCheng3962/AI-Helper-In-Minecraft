// LittleSkin (third-party yggdrasil) OAuth login helper.
//
// Reads LittleSkin app settings from config.json -> auth (type = "littleskin"):
//   auth.site           e.g. https://littleskin.cn  (skin site root)
//   auth.clientId       OAuth client id
//   auth.clientSecret   OAuth client secret
//   auth.redirectPort   local callback port
// Falls back to the built-in LittleSkin.cn defaults when config is absent.
//
// Modes:
//   default            refresh stored session if expired, else skip
//   relogin (--relogin)  try refresh_token first; if it fails, clear the profile
//                       and run the full OAuth flow (auto-opens the browser)
//   force   (--force)    clear the profile and always run the OAuth flow
//
// Saves the resulting yggdrasil session to auth_profile.json:
//   { session, refreshToken, expireTime, uuid, username, site }
//
// Usage:  node 3rdparty_auth.js [relogin|force]

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFile } = require('child_process');
const utils = require('./utils');

const DEFAULT_SITE = 'https://littleskin.cn';
const DEFAULT_CLIENT_ID = '1153';
const DEFAULT_CLIENT_SECRET = '3C1p27R9qxnH4lBrJjM9NsWVjYG2UuAluag95ydS';
const DEFAULT_REDIRECT_PORT = 7322;

// ---- Load LittleSkin app settings (config.json -> auth) ----
function loadLittleSkinConfig() {
    const base = {
        site: DEFAULT_SITE,
        clientId: DEFAULT_CLIENT_ID,
        clientSecret: DEFAULT_CLIENT_SECRET,
        redirectPort: DEFAULT_REDIRECT_PORT,
    };
    try {
        const cfg = utils.readConfig('config.json');
        if (cfg && cfg.auth && cfg.auth.type === 'littleskin') {
            const a = cfg.auth;
            if (typeof a.site === 'string' && a.site.trim()) base.site = a.site.trim();
            if (typeof a.clientId !== 'undefined' && String(a.clientId).trim()) base.clientId = String(a.clientId).trim();
            if (typeof a.clientSecret === 'string') base.clientSecret = a.clientSecret.trim();
            if (a.redirectPort) {
                const p = parseInt(a.redirectPort, 10);
                if (!isNaN(p) && p > 0 && p < 65536) base.redirectPort = p;
            }
        }
    } catch (e) {
        console.error('Failed to read config.json for LittleSkin settings:', e.message);
    }
    const envPort = parseInt(process.env.AAFM_AUTH_REDIRECT_PORT || '', 10);
    if (!isNaN(envPort) && envPort > 0 && envPort < 65536) {
        base.redirectPort = envPort;
    }
    base.site = String(base.site).replace(/\/+$/, '');
    return base;
}

const LS = loadLittleSkinConfig();
const tokenEndpoint = `${LS.site}/oauth/token`;
const rolesEndpoint = `${LS.site}/api/yggdrasil/sessionserver/session/minecraft/profile`;
const mctokenEndpoint = `${LS.site}/api/yggdrasil/authserver/oauth`;
const redirectUri = `http://localhost:${LS.redirectPort}/auth`;

// 外置登录各 HTTP 请求的硬超时（毫秒），可用环境变量 AAFM_AUTH_TIMEOUT_MS 覆盖。
const AUTH_TIMEOUT_MS = parseInt(process.env.AAFM_AUTH_TIMEOUT_MS || '20000', 10) || 20000;
// 浏览器回调不能无限等：默认 5 分钟，测试可用环境变量缩短（#48）。
const CALLBACK_TIMEOUT_MS = Math.max(
    1000, parseInt(process.env.AAFM_AUTH_CALLBACK_TIMEOUT_MS || '300000', 10) || 300000);
function authSignal() {
    try {
        return (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function')
            ? AbortSignal.timeout(AUTH_TIMEOUT_MS) : undefined;
    } catch (e) {
        return undefined;
    }
}

const ARGS = process.argv.slice(2);
const RELOGIN = ARGS.includes('relogin') || ARGS.includes('--relogin');
const FORCE = ARGS.includes('force') || ARGS.includes('--force');

// ---- Browser auto-open ----
function openBrowser(targetUrl) {
    let parsed;
    try {
        parsed = new URL(String(targetUrl));
    } catch (e) {
        console.warn('(登录链接无效，请手动复制上方链接访问)');
        return;
    }
    // 只允许 http/https，且不要把 URL 拼进 shell 命令（#55）。
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        console.warn('(登录链接协议不受支持，请手动复制上方链接访问)');
        return;
    }
    const href = parsed.toString();
    let command;
    let args;
    if (process.platform === 'win32') {
        command = 'rundll32';
        args = ['url.dll,FileProtocolHandler', href];
    } else if (process.platform === 'darwin') {
        command = 'open';
        args = [href];
    } else {
        command = 'xdg-open';
        args = [href];
    }
    execFile(command, args, (err) => {
        if (err) console.warn('(未能自动打开浏览器，请手动复制上方链接访问)');
    });
}

async function refreshSession(refreshToken) {
    const requestBody = new URLSearchParams();
    requestBody.append('grant_type', 'refresh_token');
    requestBody.append('client_id', LS.clientId);
    requestBody.append('client_secret', LS.clientSecret);
    requestBody.append('refresh_token', refreshToken);
    let response = await fetch(tokenEndpoint, {
        method: 'POST',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: requestBody.toString(),
        signal: authSignal()
    });
    if (!response.ok) throw new Error(`Token refresh HTTP ${response.status}`);
    return await response.json();
}

const cf = 'auth_profile.json';
var Config = utils.readConfig(cf, undefined, {
    session: {},
    refreshToken: '',
    expireTime: -1,
    uuid: '',
    username: '',
    site: LS.site
});
if (!Config) Config = { session: {}, refreshToken: '', expireTime: -1, uuid: '', username: '', site: LS.site };
if (Config.site && LS.site && Config.site !== LS.site) {
    console.log(`Auth profile site (${Config.site}) differs from current config (${LS.site}); using ${LS.site}.`);
}

const BEARER_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json"
};

async function fetchMcToken(accessToken, selectedUuid) {
    const headers = Object.assign({}, BEARER_JSON_HEADERS, {
        "Authorization": 'Bearer ' + accessToken
    });
    const response = await fetch(mctokenEndpoint, {
        method: 'POST',
        headers,
        body: JSON.stringify({ "uuid": selectedUuid }),
        signal: authSignal()
    });
    if (!response.ok) throw new Error(`Failed to obtain MC token: HTTP ${response.status}`);
    return await response.json();
}

async function fetchRoles(accessToken) {
    const headers = new Headers(BEARER_JSON_HEADERS);
    headers.append("Authorization", 'Bearer ' + accessToken);
    let retries = 0;
    while (retries < 3) {
        try {
            const response = await fetch(rolesEndpoint, { headers, signal: authSignal() });
            if (!response.ok) throw new Error(`Failed to fetch roles: ${response.status}`);
            const roles = await response.json();
            if (JSON.stringify(roles).indexOf('"error":') !== -1) throw new Error('No response from Roles API');
            return roles;
        } catch (error) {
            console.error(`Attempt ${retries + 1} failed:`, error.message);
            retries++;
            await new Promise(resolve => setTimeout(resolve, 5000));
        }
    }
    throw new Error('Failed to fetch roles after retries');
}

function saveProfile() {
    utils.writeConfig(cf, Config);
    console.log('Config saved to auth_profile.json.');
}

// Try to refresh the stored LittleSkin OAuth refresh_token (no browser needed).
// Returns true on success, false otherwise.
async function tryRefresh() {
    if (!Config.refreshToken) return false;
    console.log('Refreshing session...');
    try {
        const ls_token = await refreshSession(Config.refreshToken);
        if (!ls_token || !ls_token.access_token) {
            console.error('Refresh returned no access token.');
            return false;
        }
        Config.refreshToken = ls_token.refresh_token || Config.refreshToken;
        Config.expireTime = Math.floor(Date.now() / 1000) + (ls_token.expires_in || 0);

        const roles = await fetchRoles(ls_token.access_token);
        let uuid = null;
        for (const item of roles) {
            if (item.name === Config.username) { uuid = item.id; break; }
        }
        if (!uuid && roles.length === 1) uuid = roles[0].id;
        if (!uuid) {
            console.error('当前角色已不在该 LittleSkin 账户内，需要重新选择角色。');
            return false;
        }
        Config.session = await fetchMcToken(ls_token.access_token, uuid);
        Config.uuid = utils.formatUUID(uuid);
        Config.site = LS.site;
        return true;
    } catch (e) {
        console.error('Refresh failed: ' + (e && e.message || e));
        return false;
    }
}

function safeEqual(a, b) {
    const left = Buffer.from(String(a || ''));
    const right = Buffer.from(String(b || ''));
    return left.length === right.length && crypto.timingSafeEqual(left, right);
}

// Full OAuth flow (opens the browser). 回调服务器有超时、state 校验和单次处理，
// 失败/重复回调都会关闭监听，不再无限轮询（#48）。
async function runOAuth() {
    const state = crypto.randomBytes(16).toString('hex');
    console.log(`LittleSkin 登录地址（${LS.site}）:`);
    const authorizeUrl =
        `${LS.site}/oauth/authorize?client_id=${encodeURIComponent(LS.clientId)}` +
        `&redirect_uri=${encodeURIComponent(redirectUri)}` +
        `&response_type=code&state=${encodeURIComponent(state)}` +
        `&scope=Yggdrasil.PlayerProfiles.Read%20Yggdrasil.MinecraftToken.Create`;
    console.log(authorizeUrl);
    if (process.env.AAFM_AUTH_NO_BROWSER !== '1') {
        openBrowser(authorizeUrl);
    }

    await new Promise((resolve, reject) => {
        let settled = false;
        let handling = false;
        let timer = null;
        let server = null;

        const finish = (err) => {
            if (settled) return;
            settled = true;
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
            try {
                if (server) server.close();
            } catch (e) { /* already closed */ }
            if (err) reject(err);
            else resolve();
        };

        server = http.createServer(async (req, res) => {
            let parsed;
            try {
                parsed = new URL(req.url || '/', 'http://127.0.0.1');
            } catch (e) {
                res.writeHead(400, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Bad request');
                return;
            }
            if (parsed.pathname !== '/auth') {
                res.writeHead(404, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Not found');
                return;
            }
            if (settled) {
                res.writeHead(409, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Login already finished');
                return;
            }
            if (handling) {
                res.writeHead(409, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Login already in progress');
                return;
            }
            const q = Object.fromEntries(parsed.searchParams.entries());
            if (q.error) {
                const msg = String(q.error_description || q.error);
                res.writeHead(400, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Login failed: ' + msg);
                finish(new Error('OAuth error: ' + msg));
                return;
            }
            if (typeof q.state !== 'string' || !safeEqual(q.state, state)) {
                res.writeHead(400, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Invalid state');
                finish(new Error('Invalid OAuth state'));
                return;
            }
            if (typeof q.code !== 'string' || !q.code) {
                res.writeHead(400, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Missing code');
                finish(new Error('Missing OAuth code'));
                return;
            }

            handling = true;
            const requestBody = new URLSearchParams();
            requestBody.append('grant_type', 'authorization_code');
            requestBody.append('client_id', LS.clientId);
            requestBody.append('client_secret', LS.clientSecret);
            requestBody.append('redirect_uri', redirectUri);
            requestBody.append('code', q.code);

            let ls_token;
            try {
                const response = await fetch(tokenEndpoint, {
                    method: 'POST',
                    headers: { 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: requestBody.toString(),
                    signal: authSignal()
                });
                if (!response.ok) throw new Error(`Token request HTTP ${response.status}`);
                ls_token = await response.json();
            } catch (e) {
                if (settled) return;
                res.writeHead(500, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Login failed: ' + e.message);
                console.error('OAuth token exchange failed: ' + e.message);
                finish(e);
                return;
            }
            Config.refreshToken = ls_token.refresh_token || '';
            Config.expireTime = Math.floor(Date.now() / 1000) + (ls_token.expires_in || 0);

            await new Promise(resolve => setTimeout(resolve, 1000));
            if (settled) return;
            try {
                const roles = await fetchRoles(ls_token.access_token);

                const roleNames = roles.map(role => role.name);
                console.log('\nAvailable profiles:');
                roleNames.forEach((name, index) => console.log(`[${index}] ${name}`));

                let selectedRole;
                if (roleNames.length === 1) {
                    selectedRole = roles[0];
                    console.log(`Auto-selected: ${selectedRole.name}`);
                } else {
                    const choice = await utils.getInput('\nChoose profile (index): ');
                    const selectedIndex = parseInt(choice);
                    if (isNaN(selectedIndex) || selectedIndex < 0 || selectedIndex >= roles.length) {
                        throw new Error('Invalid profile selection');
                    }
                    selectedRole = roles[selectedIndex];
                    console.log(`Selected: ${selectedRole.name}`);
                }

                await new Promise(resolve => setTimeout(resolve, 500));
                if (settled) return;
                Config.session = await fetchMcToken(ls_token.access_token, selectedRole.id);
                Config.username = selectedRole.name;
                Config.uuid = utils.formatUUID(selectedRole.id);
                Config.site = LS.site;
            } catch (e) {
                if (settled) return;
                res.writeHead(500, { 'Content-Type': 'text/plain;charset=UTF-8' });
                res.end('Login failed: ' + e.message);
                console.error('OAuth finalize failed: ' + e.message);
                finish(e);
                return;
            }

            if (settled) return;
            res.writeHead(200, { 'Content-Type': 'text/html;charset=UTF-8' });
            res.end('Done Login. You can close this window now.');
            finish();
        });

        server.on('error', (e) => {
            console.error('本地回调端口 ' + LS.redirectPort + ' 被占用: ' + e.message);
            finish(new Error('本地回调端口被占用: ' + e.message));
        });
        server.listen(LS.redirectPort, '127.0.0.1', () => {
            console.log('等待回调… 如浏览器未自动打开，请手动复制上方链接访问。');
        });
        timer = setTimeout(
            () => finish(new Error('OAuth callback timeout')), CALLBACK_TIMEOUT_MS);
        if (timer.unref) timer.unref();
    });
    console.log('Login done.');
}

async function main() {
    if (FORCE) {
        Config.session = {};
        Config.refreshToken = '';
        Config.expireTime = -1;
    }

    const nowSec = Math.floor(Date.now() / 1000);

    // refresh_token available and (relogin requested OR session expired)
    if (Config.refreshToken && (RELOGIN || Config.expireTime < nowSec)) {
        if (await tryRefresh()) {
            saveProfile();
            console.log('Session refreshed.');
            return;
        }
        if (!RELOGIN) {
            // Normal run with an expired-but-unrefreshable token: fall back to OAuth too.
        }
        console.log('将重新进行 LittleSkin 登录。');
        Config.session = {};
        Config.refreshToken = '';
        Config.expireTime = -1;
    } else if (Config.refreshToken && Config.expireTime > nowSec && !RELOGIN) {
        console.log('Session valid, skipping refresh.');
        Config.site = LS.site;
        saveProfile();
        return;
    }

    await runOAuth();
    saveProfile();
}

function mfAuth(client, options) {
    client.username = options.username;
    client.uuid = options.uuid;
    options.sessionServer = `${LS.site}/api/yggdrasil/sessionserver`;
    options.auth = 'mojang';
    client.session = options.loginSession;
    options.haveCredentials = true;
    options.connect(client);
}

module.exports = {
    main,
    mfAuth,
    runOAuth
};

if (require.main === module) {
    main().catch((e) => {
        console.error('Login failed:', e && e.message ? e.message : e);
        process.exit(1);
    });
}
