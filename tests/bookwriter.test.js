// SPDX-License-Identifier: Apache-2.0
'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { compileFunction } = require('node:vm');

// 載入實際原始碼；網路與等待使用替身，版本編解碼使用已安裝的專案依賴。
const filename = path.join(__dirname, '..', 'bookwriter.js');
const source = readFileSync(filename, 'utf8');
const moduleBody = compileFunction(source, ['require', 'module', 'exports', 'setTimeout'], { filename });

class MockItem {
    constructor(type, count, metadata, nbt) {
        Object.assign(this, { type, count, metadata, nbt });
    }
    static fromNotch(item) { return item; }
    static toNotch(item) { return item; }
}

function book(pages, { title, author = 'test-writer', components = false } = {}) {
    const signed = title != null;
    if (components) {
        const data = { pages: pages.map((p) => ({ content: signed
            ? { type: 'compound', value: { text: { type: 'string', value: p } } } : p })) };
        if (signed) Object.assign(data, { rawTitle: title, author, generation: 0, resolved: true });
        return { type: signed ? 2 : 1, count: 1, removedComponents: [], components: [
            { type: signed ? 'written_book_content' : 'writable_book_content', data }
        ] };
    }
    const value = { pages: { type: 'list', value: { type: 'string',
        value: pages.map((p) => signed ? JSON.stringify({ text: p }) : p) } } };
    if (signed) Object.assign(value, { title: { type: 'string', value: title }, author: { type: 'string', value: author } });
    return { type: signed ? 2 : 1, count: 1, nbt: { type: 'compound', name: '', value } };
}

function loadWriter(itemFactory = () => MockItem) {
    const mod = { exports: {} };
    moduleBody((name) => {
        assert.equal(name, 'prismarine-item');
        return itemFactory;
    }, mod, mod.exports, (callback) => setImmediate(callback));
    return mod.exports.writeBooks;
}

function fixture({ confirmations = [], books = 1, creative = false,
    components = false, fallbackFails = false, fallbackConfirms = true, nbtPacket = false, version } = {}) {
    const registry = version ? require('minecraft-data')(version)
        : { itemsByName: { writable_book: { id: 1 }, written_book: { id: 2 } } };
    const itemFactory = version ? require('prismarine-item') : () => MockItem;
    const Item = itemFactory(registry);
    if (version) components = registry.supportFeature('itemsWithComponents');
    const makeBook = (pages, options = {}) => ({ ...book(pages, { components, ...options }),
        type: registry.itemsByName[options.title != null ? 'written_book' : 'writable_book'].id });
    const inventory = new EventEmitter();
    inventory.slots = Array(46).fill(null);
    const originals = Array.from({ length: books }, (_, index) => ({ ...makeBook([]), index, pages: [] }));
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
        version: version || 'mock',
        username: 'test-writer',
        registry,
        inventory,
        game: { gameMode: creative ? 'creative' : 'survival' },
        quickBarSlot: 0,
        get heldItem() { return inventory.slots[36 + this.quickBarSlot]; },
        supportFeature(name) {
            return (name === 'itemsWithComponents' && components) || (name === 'editBookPacketUsesNbt' && nbtPacket);
        },
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
        _client: Object.assign(new EventEmitter(), {
            write(name, data) {
                assert.equal(name, 'edit_book');
                assert.equal(data.hand, 0);
                packets.push({ name, data, item: bot.heldItem, progressCount: progress.length });
                trace.push('write');
                const confirmation = confirmations[packets.length - 1];
                if (confirmation) {
                    const slot = 36 + bot.quickBarSlot;
                    setImmediate(() => {
                        const pages = data.pages || data.new_book.nbt.value.pages.value.value;
                        const expected = makeBook(pages, { title: data.title });
                        if (typeof confirmation === 'function') {
                            confirmation({ bot, data, slot, expected, reply });
                        } else {
                            const held = inventory.slots[slot];
                            Object.assign(held, expected, { pages: [...pages] });
                            trace.push('confirm');
                            reply(held);
                        }
                    });
                }
            }
        }),
        creative: {
            async setInventorySlot(slot, item) {
                fallback.push({ slot, item, progressCount: progress.length });
                trace.push('fallback');
                if (fallbackFails) throw new Error('mock creative rejection');
                inventory.slots[slot] = item;
                if (fallbackConfirms) reply(item);
            }
        }
    };
    function reply(item, event = 'set_slot', extra = {}) {
        const slot = 36 + bot.quickBarSlot;
        if (!Object.keys(extra).length) inventory.slots[slot] = item;
        const wire = item ? Item.toNotch(item) : item;
        if (event === 'window_items') {
            const items = Array(46).fill(null);
            items[slot] = wire;
            bot._client.emit(event, { windowId: 0, items, ...extra });
        } else if (event === 'set_player_inventory') {
            bot._client.emit(event, { slotId: slot, contents: wire, ...extra });
        } else bot._client.emit(event, { windowId: 0, slot, item: wire, ...extra });
    }
    const writeBooks = loadWriter(itemFactory);
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
        for (const event of ['set_slot', 'window_items', 'set_player_inventory']) {
            assert.equal(bot._client.listenerCount(event), 0);
        }
        return result;
    }
    return { bot, originals, packets, progress, drops, chats, fallback, trace, logs, run, reply,
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

for (const [name, invalid] of [
    ['只有本機槽位事件', ({ bot }) => bot.inventory.emit('updateSlot:36')],
    ['本機樂觀更新為目標內容', ({ bot, expected }) => {
        bot.inventory.slots[36] = expected;
        bot.inventory.emit('updateSlot:36');
    }],
    ['伺服器原樣重播空書', ({ bot, reply }) => reply(bot.heldItem)],
    ['錯誤頁面', ({ reply }) => reply(book(['wrong']))],
    ['錯誤物品', ({ expected, reply }) => reply({ ...expected, type: 3 })],
    ['缺少書籍資料', ({ reply }) => reply({ type: 1, count: 1 })],
    ['空槽位', ({ reply }) => reply(null)],
    ['無法解析的 NBT', ({ reply }) => reply({ type: 1, count: 1, nbt: { type: 'list', value: null } })],
    ['其他視窗', ({ expected, reply }) => reply(expected, 'set_slot', { windowId: 4 })],
    ['其他槽位', ({ expected, reply }) => reply(expected, 'set_slot', { slot: 37 })],
    ['其他背包槽位封包', ({ expected, reply }) => reply(expected, 'set_player_inventory', { slotId: 37 })]
]) {
    test(`${name}不構成確認`, async () => {
        const f = fixture({ confirmations: [invalid, invalid] });
        assertStopped(await f.run());
        assert.deepEqual(f.progress, []);
        assert.deepEqual(f.drops, []);
        assert.deepEqual(f.chats, []);
    });
}

for (const event of ['set_slot', 'window_items', 'set_player_inventory']) {
    test(`${event}的正確伺服器書頁可確認`, async () => {
        const f = fixture({ confirmations: [({ expected, reply }) => reply(expected, event)] });
        assert.equal((await f.run()).ok, true);
        assert.equal(f.progress.length, 1);
    });
}

for (const [name, badBook] of [
    ['未轉成成書', book(['page 1'])],
    ['錯誤書名', book(['page 1'], { title: 'wrong' })],
    ['錯誤作者', book(['page 1'], { title: 'Volume 1', author: 'someone-else' })],
    ['錯誤頁面順序', book(['two', 'one'], { title: 'Volume 1' })]
]) {
    test(`簽名確認拒絕${name}`, async () => {
        const send = ({ reply }) => reply(badBook);
        const f = fixture({ confirmations: [send, send] });
        assertStopped(await f.run({ pages: name === '錯誤頁面順序' ? ['one', 'two'] : ['page 1'],
            pagesPerBook: 100, signBook: true, titleTemplate: 'Volume {number}' }));
        assert.deepEqual(f.drops, []);
    });
}

for (const version of ['1.20.4', '1.21.1', '1.21.11']) {
    for (const signBook of [false, true]) {
        test(`${version}真實物品編解碼：${signBook ? '成書' : '書與筆'}保留中文、空白、換行與 emoji`, async () => {
            const f = fixture({ version, confirmations: [true] });
            const result = await f.run({ pages: [' 中文 😀\n  end ', '{"text":"literal JSON"}'],
                pagesPerBook: 100, signBook, titleTemplate: '第 {number} 冊' });
            assert.equal(result.ok, true);
            assert.equal(result.nextPage, 2);
        });
    }
}

test('舊 NBT 封包未簽名時使用原文頁面而非 JSON 包裝', async () => {
    const f = fixture({ nbtPacket: true, confirmations: [true] });
    assert.equal((await f.run()).ok, true);
    assert.deepEqual(f.packets[0].data.new_book.nbt.value.pages.value.value, ['page 1']);
});

test('創造兜底僅本機更新而沒有伺服器內容回執時仍失敗', async () => {
    const f = fixture({ creative: true, fallbackConfirms: false });
    assertStopped(await f.run());
    assert.deepEqual(f.progress, []);
    assert.deepEqual(f.drops, []);
});

test('創造兜底未簽名書頁保持原文且有伺服器回執才成功', async () => {
    const f = fixture({ creative: true });
    assert.equal((await f.run()).ok, true);
    assert.deepEqual(f.fallback[0].item.nbt.value.pages.value.value, ['page 1']);
});

test('上一冊遲到回執不確認目前冊，逾時後的回執不再推進進度', async () => {
    const old = ({ reply }) => reply(book(['one']));
    const f = fixture({ books: 3, confirmations: [true, old, old] });
    assertStopped(await f.run({ pages: ['one', 'two', 'three'] }), { done: 1, nextPage: 1, book: 2 });
    f.reply(book(['two']));
    assert.equal(f.progress.length, 1);
    assert.equal(f.drops.length, 1);
});

test('封包寫入例外也會清理全部確認監聽器', async () => {
    const f = fixture();
    f.bot._client.write = () => { throw new Error('injected write failure'); };
    const result = await f.run();
    assert.equal(result.ok, false);
    assert.deepEqual(f.progress, []);
});

test('伺服器改寫空白不能誤當作相同頁面', async () => {
    const send = ({ reply }) => reply(book(['page 1']));
    const f = fixture({ confirmations: [send, send] });
    assertStopped(await f.run({ pages: [' page 1 '] }));
});

test('主手切換後的原槽位回執不能確認這次寫入', async () => {
    const moved = ({ bot, expected, reply }) => {
        bot.quickBarSlot = 1;
        reply(expected, 'set_slot', { slot: 36 });
    };
    const f = fixture({ confirmations: [moved, moved] });
    assertStopped(await f.run());
});

test('Components 簽名文字的格式包裝可解開，但不能忽略未解析的動態文字', async () => {
    const good = ({ expected, reply }) => {
        expected.components[0].data.pages[0].content = {
            type: 'compound', value: {
                text: { type: 'string', value: 'page ' },
                extra: { type: 'list', value: { type: 'compound', value: [
                    { text: { type: 'string', value: '1' }, bold: { type: 'byte', value: 1 } }
                ] } }
            }
        };
        reply(expected);
    };
    const f = fixture({ components: true, confirmations: [good] });
    assert.equal((await f.run({ signBook: true, titleTemplate: 'Volume {number}' })).ok, true);
    const bad = ({ expected, reply }) => {
        expected.components[0].data.pages[0].content = {
            type: 'compound', value: { translate: { type: 'string', value: 'page 1' } }
        };
        reply(expected);
    };
    const g = fixture({ components: true, confirmations: [bad, bad] });
    assertStopped(await g.run({ signBook: true, titleTemplate: 'Volume {number}' }));
});
