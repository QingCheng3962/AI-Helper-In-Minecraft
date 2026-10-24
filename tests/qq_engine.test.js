// SPDX-License-Identifier: Apache-2.0
'use strict';

const assert = require('node:assert/strict');
const { EventEmitter, once } = require('node:events');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { compileFunction } = require('node:vm');

// 執行整個真實入口；替換網路、stdin 和設定，避免接觸真實 QQ 帳號。
const filename = path.join(__dirname, '..', 'qq_engine.js');
const boot = compileFunction(readFileSync(filename, 'utf8'),
    ['require', 'process', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout'], { filename });

function fixture(accessToken = 'test-token', realNetwork = false) {
    const servers = [];
    const events = [];
    const exits = [];
    const Parent = realNetwork ? require('ws').WebSocketServer : EventEmitter;
    class Server extends Parent {
        constructor() {
            super(realNetwork ? { host: '127.0.0.1', port: 0 } : undefined);
            servers.push(this);
        }
    }
    const fakeProcess = new EventEmitter();
    Object.assign(fakeProcess, {
        argv: ['node', filename, 'fake-runtime.json'], stdin: {},
        stdout: { write(line) { events.push(JSON.parse(line)); } },
        exit(code) { exits.push(code); }
    });
    boot((name) => {
        if (name === 'fs') return { readFileSync() { return JSON.stringify({ mode: 'reverse', accessToken }); } };
        if (name === 'readline') return { createInterface() { return new EventEmitter(); } };
        assert.equal(name, 'ws');
        return { Server, OPEN: 1 };
    }, fakeProcess, () => assert.fail('unexpected interval'), () => {},
    () => assert.fail('unexpected timer'), () => {});
    return {
        events, exits, servers,
        connect(url = '/', headers = {}) {
            const ws = Object.assign(new EventEmitter(), { readyState: 1, sent: [], closed: [] });
            ws.send = (text) => ws.sent.push(JSON.parse(text));
            ws.close = (...args) => ws.closed.push(args);
            servers[0].emit('connection', ws, { url, headers });
            return ws;
        }
    };
}

test('reverse rejects malformed, missing, empty and duplicate query credentials without throwing', () => {
    const f = fixture();
    for (const url of ['/?access_token=%', '/?access_token=%GG', '/?access_token=%E0%A4%A',
        '/?access_token=%C0%AF', '/?access_token', '/?access_token=', '/', '/?other=x',
        '/?access_token=test-token&access_token=test-token',
        '/?access_token=wrong&access_token=test-token',
        '/?%61ccess_token=test-token&access_token=test-token']) {
        let ws;
        assert.doesNotThrow(() => { ws = f.connect(url); }, url);
        assert.deepEqual(ws.closed, [[1008, 'invalid token']], url);
        assert.equal(ws.sent.length, 0, url);
        assert.equal(ws.listenerCount('message'), 0, url);
    }
    assert.deepEqual(f.exits, []);
    assert(!f.events.some((e) => e.state === 'connected'));
    assert(!JSON.stringify(f.events).includes('test-token'));
});

test('reverse remains usable after rejecting malformed credentials', () => {
    const f = fixture();
    assert.doesNotThrow(() => f.connect('/?access_token=%'));
    const ws = f.connect('/?access_token=test-token');
    assert.equal(ws.closed.length, 0);
    assert.equal(ws.listenerCount('message'), 1);
    assert.equal(ws.sent[0].action, 'get_group_list');
    assert(f.events.some((e) => e.state === 'connected'));
});

test('reverse supports encoded values and encoded parameter names without truncating equals', () => {
    for (const [token, query] of [['a=b&中+😀', '%61ccess_token=a%3Db%26%E4%B8%AD%2B%F0%9F%98%80'],
        ['a=b=c', 'access_token=a=b=c'], ['a b', 'access_token=a+b']]) {
        const ws = fixture(token).connect('/?' + query);
        assert.equal(ws.closed.length, 0);
        assert.equal(ws.sent[0].action, 'get_group_list');
    }
});

test('reverse keeps Authorization precedence for valid requests and rejects wrong headers', () => {
    for (const headers of [{ authorization: 'Bearer test-token' },
        { Authorization: 'bEaReR test-token' }, { authorization: 'test-token' }]) {
        assert.equal(fixture().connect('/?access_token=other', headers).closed.length, 0);
    }
    assert.deepEqual(fixture().connect('/?access_token=test-token',
        { authorization: 'Bearer wrong' }).closed, [[1008, 'invalid token']]);
});

test('reverse rejects malformed or ambiguous queries even with a correct header', () => {
    for (const url of ['/?access_token=%', '/?access_token=',
        '/?access_token=test-token&access_token=test-token']) {
        assert.deepEqual(fixture().connect(url, { authorization: 'Bearer test-token' }).closed,
            [[1008, 'invalid token']]);
    }
});

test('reverse keeps no-token mode and normal message/close lifecycle', () => {
    const f = fixture('');
    const ws = f.connect('/?access_token=%');
    assert.equal(ws.closed.length, 0);
    assert.doesNotThrow(() => ws.emit('message', JSON.stringify({ post_type: 'meta_event' })));
    ws.emit('close');
    assert(f.events.some((e) => e.state === 'disconnected'));
    assert.deepEqual(f.exits, []);
});

test('real loopback WebSocket rejects bad credentials then accepts a valid connection', async (t) => {
    const WebSocket = require('ws');
    const f = fixture('test-token', true);
    const server = f.servers[0];
    t.after(async () => {
        for (const client of server.clients) client.terminate();
        await new Promise((resolve) => server.close(resolve));
    });
    await once(server, 'listening');
    const url = 'ws://127.0.0.1:' + server.address().port;
    for (const query of ['access_token=%', 'access_token=wrong',
        'access_token=test-token&access_token=test-token']) {
        const ws = new WebSocket(url + '/?' + query);
        t.after(() => ws.terminate());
        const [code] = await once(ws, 'close');
        assert.equal(code, 1008);
    }
    const valid = new WebSocket(url + '/?access_token=test-token');
    t.after(() => valid.terminate());
    const [data] = await once(valid, 'message');
    assert.equal(JSON.parse(data).action, 'get_group_list');
    assert.equal(valid.readyState, WebSocket.OPEN);
    assert.deepEqual(f.exits, []);
});
