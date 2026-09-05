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
const url = require('url');
const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');
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
    base.site = String(base.site).replace(/\/+$/, '');
    return base;
}

const LS = loadLittleSkinConfig();
const tokenEndpoint = `${LS.site}/oauth/token`;
const rolesEndpoint = `${LS.site}/api/yggdrasil/sessionserver/session/minecraft/profile`;
const mctokenEndpoint = `${LS.site}/api/yggdrasil/authserver/oauth`;
const redirectUri = `http://localhost:${LS.redirectPort}/auth`;

const ARGS = process.argv.slice(2);
const RELOGIN = ARGS.includes('relogin') || ARGS.includes('--relogin');
const FORCE = ARGS.includes('force') || ARGS.includes('--force');

// ---- Browser auto-open ----
function openBrowser(targetUrl) {
    let cmd = null;
    if (process.platform === 'win32') {
        cmd = `start "" "${targetUrl}"`;
    } else if (process.platform === 'darwin') {
        cmd = `open "${targetUrl}"`;
    } else {
        cmd = `xdg-open "${targetUrl}"`;
    }
    exec(cmd, (err) => {
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
        body: requestBody.toString()
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
        body: JSON.stringify({ "uuid": selectedUuid })
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
            const response = await fetch(rolesEndpoint, { headers, timeout: 5000 });
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

// Full OAuth flow (opens the browser).
async function runOAuth() {
    console.log(`LittleSkin 登录地址（${LS.site}）:`);
    const authorizeUrl =
        `${LS.site}/oauth/authorize?client_id=${encodeURIComponent(LS.clientId)}` +
        `&redirect_uri=${encodeURIComponent(redirectUri)}` +
        `&response_type=code&scope=Yggdrasil.PlayerProfiles.Read%20Yggdrasil.MinecraftToken.Create`;
    console.log(authorizeUrl);
    openBrowser(authorizeUrl);

    var logined = false;
    let server = http.createServer(async function (req, res) {
        const code = url.parse(req.url, true).query.code;
        const requestBody = new URLSearchParams();
        requestBody.append('grant_type', 'authorization_code');
        requestBody.append('client_id', LS.clientId);
        requestBody.append('client_secret', LS.clientSecret);
        requestBody.append('redirect_uri', redirectUri);
        requestBody.append('code', code);

        let response;
        try {
            response = await fetch(tokenEndpoint, {
                method: 'POST',
                headers: { 'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
                body: requestBody.toString()
            });
            if (!response.ok) throw new Error(`Token request HTTP ${response.status}`);
        } catch (e) {
            res.writeHead(500, { "Content-Type": "text/plain;charset=UTF-8" });
            res.end('Login failed: ' + e.message);
            console.error('OAuth token exchange failed: ' + e.message);
            return;
        }
        const ls_token = await response.json();
        Config.refreshToken = ls_token.refresh_token || '';
        Config.expireTime = Math.floor(Date.now() / 1000) + (ls_token.expires_in || 0);

        await new Promise(resolve => setTimeout(resolve, 1000));
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
            Config.session = await fetchMcToken(ls_token.access_token, selectedRole.id);
            Config.username = selectedRole.name;
            Config.uuid = utils.formatUUID(selectedRole.id);
            Config.site = LS.site;
        } catch (e) {
            res.writeHead(500, { "Content-Type": "text/plain;charset=UTF-8" });
            res.end('Login failed: ' + e.message);
            console.error('OAuth finalize failed: ' + e.message);
            return;
        }

        logined = true;
        res.writeHead(200, { "Content-Type": "text/html;charset=UTF-8" });
        res.end('Done Login. You can close this window now.');
        server.close();
    });

    server.listen(LS.redirectPort, '127.0.0.1', () => {
        console.log('等待回调… 如浏览器未自动打开，请手动复制上方链接访问。');
    });
    server.on('error', (e) => {
        console.error('本地回调端口 ' + LS.redirectPort + ' 被占用: ' + e.message);
        process.exit(1);
    });

    while (!logined) await new Promise(resolve => setTimeout(resolve, 1000));
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
    mfAuth
};

if (require.main === module) {
    main().catch((e) => {
        console.error('Login failed:', e && e.message ? e.message : e);
        process.exit(1);
    });
}
