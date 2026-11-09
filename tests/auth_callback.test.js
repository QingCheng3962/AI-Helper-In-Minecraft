'use strict';

process.env.AAFM_AUTH_NO_BROWSER = '1';
process.env.AAFM_AUTH_REDIRECT_PORT = '17322';
process.env.AAFM_AUTH_CALLBACK_TIMEOUT_MS = '1000';

const test = require('node:test');
const assert = require('node:assert');
const http = require('http');
const { runOAuth } = require('../3rdparty_auth');

function canBind(port) {
    return new Promise((resolve, reject) => {
        const server = http.createServer();
        server.once('error', reject);
        server.listen(port, '127.0.0.1', () => {
            server.close(() => resolve(true));
        });
    });
}

test('OAuth callback timeout closes the local server', async () => {
    await assert.rejects(runOAuth(), /timeout/i);
    assert.strictEqual(await canBind(17322), true);
});

test('OAuth callback rejects a bad state and closes the server', async () => {
    const pending = runOAuth();
    const rejection = assert.rejects(pending, /state/i);
    await new Promise((resolve) => setTimeout(resolve, 150));
    await fetch('http://127.0.0.1:17322/auth?state=bad&code=x');
    await rejection;
    assert.strictEqual(await canBind(17322), true);
});
