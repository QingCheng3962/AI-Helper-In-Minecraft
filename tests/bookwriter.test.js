// SPDX-License-Identifier: Apache-2.0
'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { compileFunction } = require('node:vm');

// 載入實際原始碼；僅替換等待排程及物品依賴，無須連線或安裝遊戲套件。
const filename = path.join(__dirname, '..', 'bookwriter.js');
const source = readFileSync(filename, 'utf8');
const moduleBody = compileFunction(source, ['require', 'module', 'exports', 'setTimeout'], { filename });

class MockItem {
    constructor(type, count, metadata, nbt) {
        Object.assign(this, { type, count, metadata, nbt });
    }
}

function loadWriter() {
    const mod = { exports: {} };
    moduleBody((name) => {
        assert.equal(name, 'prismarine-item');
        return () => MockItem;
    }, mod, mod.exports, (callback) => setImmediate(callback));
    return mod.exports.writeBooks;
}

function fixture({ confirmations = [], books = 1, creative = false,
    components = false, fallbackFails = false } = {}) {
    const inventory = new EventEmitter();
    inventory.slots = Array(46).fill(null);
    const originals = Array.from({ length: books }, (_, index) => ({ type: 1, index, pages: [] }));
    originals.forEach((book, index) => { inventory.slots[36 + index] = book; });
    const packets = [];
    const progress = [];
    const drops = [];
    const chats = [];
    const fallback = [];
    const trace = [];
    const logs = [];
    let activations = 0;
    const bot = {
        version: 'mock',
        username: 'test-writer',
        registry: { itemsByName: { writable_book: { id: 1 }, written_book: { id: 2 } } },
        inventory,
        game: { gameMode: creative ? 'creative' : 'survival' },
        quickBarSlot: 0,
        get heldItem() { return inventory.slots[36 + this.quickBarSlot]; },
        supportFeature(name) { return name === 'itemsWithComponents' && components; },
        setQuickBarSlot(slot) { this.quickBarSlot = slot; },
        async moveSlotItem(from, to) {
            [inventory.slots[to], inventory.slots[from]] = [inventory.slots[from], inventory.slots[to]];
        },
        activateItem() { activations++; trace.push('activate'); },
        async tossStack(item) {
            drops.push(item);
            trace.push('drop');
            inventory.slots[36 + this.quickBarSlot] = null;
        },
        _client: {
            write(name, data) {
                assert.equal(name, 'edit_book');
                assert.equal(data.hand, 0);
                packets.push({ name, data, item: bot.heldItem, progressCount: progress.length });
                trace.push('write');
                if (confirmations[packets.length - 1]) {
                    const slot = 36 + bot.quickBarSlot;
                    setImmediate(() => {
                        inventory.slots[slot].pages = [...data.pages];
                        trace.push('confirm');
                        inventory.emit(`updateSlot:${slot}`);
                    });
                }
            }
        },
        creative: {
            async setInventorySlot(slot, item) {
                fallback.push({ slot, item, progressCount: progress.length });
                trace.push('fallback');
                if (fallbackFails) throw new Error('mock creative rejection');
                inventory.slots[slot] = item;
            }
        }
    };
    const writeBooks = loadWriter();
    async function run(options = {}) {
        const result = await writeBooks(bot, {
            pages: ['page 1'], pagesPerBook: 1, autoGetBook: false,
            endDrop: true, endText: 'done', ...options,
            onProgress(value) { progress.push(value); trace.push('progress'); }
        }, {
            log(level, message) { logs.push({ level, message }); },
            error(message) { logs.push({ level: 'error', message }); }
        }, (message) => { chats.push(message); });
        assert.equal(inventory.listenerCount('updateSlot:36'), 0);
        return result;
    }
    return { bot, originals, packets, progress, drops, chats, fallback, trace, logs, run,
        get activations() { return activations; } };
}

function assertStopped(result, { nextPage = 0, done = 0, book = 1 } = {}) {
    assert.deepEqual({ ok: result.ok, stopped: result.stopped, nextPage: result.nextPage },
        { ok: false, stopped: true, nextPage });
    assert.match(result.message, new RegExp(`已確認完成 ${done} 本`));
    assert.match(result.message, new RegExp(`第 ${book} 本.*未獲確認`));
    assert.match(result.message, new RegExp(`第 ${nextPage + 1} 頁`));
    assert.match(result.message, /核對遊戲內書籍後重試/);
}

test('首次寫入與重試均未確認：停止、不推進、不丟書、不發送結束文案', async () => {
    const f = fixture();
    const result = await f.run({ startText: 'start' });
    assertStopped(result);
    assert.equal(f.packets.length, 2);
    assert.equal(f.activations, 1);
    assert.deepEqual(f.progress, []);
    assert.deepEqual(f.drops, []);
    assert.deepEqual(f.chats, ['start']);
    assert.deepEqual(f.fallback, []);
    assert.equal(f.bot.heldItem, f.originals[0]);
    assert.deepEqual(f.originals[0].pages, []);
    assert.ok(f.logs.some(({ level }) => level === 'warn'));
});

test('第一本確認、第二本未確認：僅保留第一本進度，第三本不開始', async () => {
    const f = fixture({ confirmations: [true, false, false], books: 3 });
    const result = await f.run({ pages: ['one', 'two', 'three'], endDrop: false });
    assertStopped(result, { nextPage: 1, done: 1, book: 2 });
    assert.deepEqual(f.progress, [{ nextPage: 1, nextBook: 2, done: 1, total: 3, signed: false }]);
    assert.deepEqual(f.packets.map(({ data }) => data.pages), [['one'], ['two'], ['two']]);
    assert.deepEqual(f.packets.map(({ progressCount }) => progressCount), [0, 1, 1]);
    assert.deepEqual(f.drops, [f.originals[0]]);
    assert.equal(f.bot.heldItem, f.originals[1]);
    assert.equal(f.bot.inventory.slots[38], f.originals[2]);
    assert.deepEqual(f.chats, []);
});

test('從非零頁碼與指定冊號開始失敗：保留原始續寫位置與冊號', async () => {
    const f = fixture();
    const result = await f.run({ pages: ['skip 1', 'skip 2', 'skip 3', 'four', 'five', 'six'],
        startPage: 3, bookStart: 7, pagesPerBook: 2, signBook: true, titleTemplate: '第 {number} 冊' });
    assertStopped(result, { nextPage: 3, book: 7 });
    assert.deepEqual(f.packets.map(({ data }) => data.pages), [['four', 'five'], ['four', 'five']]);
    assert.ok(f.packets.every(({ data }) => data.title === '第 7 冊'));
    assert.deepEqual(f.progress, []);
    assert.deepEqual(f.drops, []);
    assert.deepEqual(f.chats, []);
});

test('首次寫入確認成功：正常推進、計數並執行結束動作', async () => {
    const f = fixture({ confirmations: [true] });
    const result = await f.run();
    assert.deepEqual({ ok: result.ok, stopped: result.stopped, nextPage: result.nextPage },
        { ok: true, stopped: false, nextPage: 1 });
    assert.match(result.message, /共 1 本/);
    assert.deepEqual(f.progress, [{ nextPage: 1, nextBook: 2, done: 1, total: 1, signed: false }]);
    assert.equal(f.packets.length, 1);
    assert.equal(f.activations, 0);
    assert.deepEqual(f.fallback, []);
    assert.deepEqual(f.drops, [f.originals[0]]);
    assert.deepEqual(f.chats, ['done']);
    assert.deepEqual(f.trace, ['write', 'confirm', 'progress', 'drop']);
});

test('首次未確認、重新啟用後重試確認：僅在重試成功後計數一次', async () => {
    const f = fixture({ confirmations: [false, true] });
    const result = await f.run();
    assert.equal(result.ok, true);
    assert.equal(result.stopped, false);
    assert.equal(result.nextPage, 1);
    assert.deepEqual(f.progress, [{ nextPage: 1, nextBook: 2, done: 1, total: 1, signed: false }]);
    assert.deepEqual(f.packets.map(({ progressCount }) => progressCount), [0, 0]);
    assert.equal(f.activations, 1);
    assert.deepEqual(f.fallback, []);
    assert.deepEqual(f.trace, ['write', 'activate', 'write', 'confirm', 'progress', 'drop']);
    assert.deepEqual(f.chats, ['done']);
});

test('普通寫入未確認、創造模式兜底成功：兜底後才更新續寫進度', async () => {
    const f = fixture({ creative: true });
    const result = await f.run({ pages: ['skip', 'content'], startPage: 1, bookStart: 4,
        signBook: true, titleTemplate: 'Volume {number}', endDrop: false });
    assert.equal(result.ok, true);
    assert.equal(result.stopped, false);
    assert.equal(result.nextPage, 2);
    assert.equal(f.packets.length, 2);
    assert.equal(f.fallback.length, 1);
    const { slot, item, progressCount } = f.fallback[0];
    assert.equal(slot, 36);
    assert.equal(progressCount, 0);
    assert.equal(item.type, 2);
    assert.deepEqual(item.nbt.value.pages.value.value, [JSON.stringify({ text: 'content' })]);
    assert.equal(item.nbt.value.title.value, 'Volume 4');
    assert.equal(item.nbt.value.author.value, 'test-writer');
    assert.deepEqual(f.progress, [{ nextPage: 2, nextBook: 5, done: 1, total: 1, signed: true }]);
    assert.deepEqual(f.trace, ['write', 'activate', 'write', 'fallback', 'progress']);
    assert.deepEqual(f.chats, ['done']);
});

test('普通寫入未確認、創造模式兜底拋錯：停止並保留位置與書籍', async () => {
    const f = fixture({ creative: true, fallbackFails: true });
    const result = await f.run();
    assertStopped(result);
    assert.equal(f.packets.length, 2);
    assert.equal(f.fallback.length, 1);
    assert.deepEqual(f.progress, []);
    assert.deepEqual(f.drops, []);
    assert.deepEqual(f.chats, []);
    assert.equal(f.bot.heldItem, f.originals[0]);
});

test('物品元件版本無法使用創造模式 NBT 兜底：仍停止並保留位置', async () => {
    const f = fixture({ creative: true, components: true });
    const result = await f.run();
    assertStopped(result);
    assert.equal(f.packets.length, 2);
    assert.deepEqual(f.fallback, []);
    assert.deepEqual(f.progress, []);
    assert.deepEqual(f.drops, []);
    assert.deepEqual(f.chats, []);
});
