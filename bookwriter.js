// SPDX-License-Identifier: Apache-2.0
// 變更說明：寫入、重試與兜底均未獲確認時停止，僅推進已確認完成的續寫進度。
'use strict';

// Document -> in-game "book and quill" (书与笔) writer.
//
// Given a list of pages this module acquires a writable book, puts it in the
// main hand and sends the `edit_book` packet, then optionally drops the book.
// It is deliberately UI/engine agnostic so it can be unit-tested: the caller
// passes the mineflayer `bot`, a logger and a `sendChat` callback.

const HOTBAR0 = 36; // first hotbar slot of the player inventory window

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function writableBookId(bot) {
    try {
        const byName = (bot.registry && bot.registry.itemsByName) || {};
        const entry = byName['writable_book'] || byName['book_and_quill'];
        return entry ? entry.id : undefined;
    } catch (e) {
        return undefined;
    }
}

function gameIsCreative(bot) {
    return !!(bot && bot.game && bot.game.gameMode === 'creative');
}

function findBookSlot(bot, exclude) {
    const id = writableBookId(bot);
    if (id == null || !bot.inventory) return -1;
    for (let i = 9; i <= 44; i++) {
        if (exclude && exclude.has(i)) continue;
        const it = bot.inventory.slots[i];
        if (it && it.type === id) return i;
    }
    const off = bot.inventory.slots[45];
    if (off && off.type === id && !(exclude && exclude.has(45))) return 45;
    return -1;
}

async function createBookInCreative(bot) {
    const id = writableBookId(bot);
    if (id == null) return -1;
    let slot = -1;
    for (let i = 36; i <= 44 && slot < 0; i++) {
        if (bot.inventory && !bot.inventory.slots[i]) slot = i;
    }
    for (let i = 9; i <= 35 && slot < 0; i++) {
        if (bot.inventory && !bot.inventory.slots[i]) slot = i;
    }
    if (slot < 0) return -1;
    const Item = require('prismarine-item')(bot.registry);
    await bot.creative.setInventorySlot(slot, new Item(id, 1));
    return slot;
}

// Move the book into hotbar slot 0 and select it. This guarantees the book is
// both in the main hand (hand=0) *and* in hotbar slot 0, so the write works
// regardless of how the server reads the packet's first field.
async function holdBookInMainHand(bot, sourceSlot) {
    const id = writableBookId(bot);
    if (sourceSlot !== HOTBAR0) {
        try {
            await bot.moveSlotItem(sourceSlot, HOTBAR0);
        } catch (e) {
            const item = bot.inventory && bot.inventory.slots[sourceSlot];
            if (!item) throw new Error('背包中未找到书与笔');
            await bot.equip(item, 'hand');
        }
    }
    if (typeof bot.setQuickBarSlot === 'function') bot.setQuickBarSlot(0);
    await sleep(200);
    const held = bot.heldItem;
    if (!held || (id != null && held.type !== id)) {
        throw new Error('未能在主手持有书与笔');
    }
    return held;
}

function writePagesToHeldBook(bot, pages, title) {
    if (bot.supportFeature('editBookIsPluginChannel')) {
        throw new Error('当前游戏版本过旧（< 1.13），暂不支持写入书与笔。');
    }
    const signing = title != null;
    if (bot.supportFeature('editBookPacketUsesNbt')) {
        // 1.13 - 1.17: the packet carries the whole book item.
        const Item = require('prismarine-item')(bot.registry);
        const id = writableBookId(bot);
        const book = new Item(id, 1);
        const value = {
            pages: {
                type: 'list',
                value: {
                    type: 'string',
                    value: pages.map((p) => JSON.stringify({ text: String(p) }))
                }
            }
        };
        if (signing) {
            value.title = { type: 'string', value: String(title) };
            value.author = { type: 'string', value: String(bot.username || '') };
        }
        book.nbt = { type: 'compound', name: '', value };
        bot._client.write('edit_book', {
            new_book: Item.toNotch(book), signing, hand: 0
        });
    } else if (signing) {
        // 1.18+: providing the title signs the book into a written book.
        bot._client.write('edit_book', { hand: 0, pages, title: String(title) });
    } else {
        // 1.18+: hand + plain-text pages; omitting the title keeps it unsigned.
        bot._client.write('edit_book', { hand: 0, pages });
    }
}

// Send the packet and wait for the server to echo the updated slot. A slot
// update is the existing confirmation signal; it does not validate page content.
async function writePagesAndConfirm(bot, pages, title) {
    const heldSlot = HOTBAR0 + (bot.quickBarSlot || 0);
    const event = `updateSlot:${heldSlot}`;
    let applied = false;
    const handler = () => { applied = true; };
    try { bot.inventory.on(event, handler); } catch (e) { /* ignore */ }
    try {
        writePagesToHeldBook(bot, pages, title);
        for (let i = 0; i < 12 && !applied; i++) {
            await sleep(100);
        }
    } finally {
        try { bot.inventory.removeListener(event, handler); } catch (e) { /* ignore */ }
    }
    return applied;
}

// Creative-only last resort (pre-1.20.5): write the page data straight into the
// held slot. Works even if the server ignores `edit_book`. When ``title`` is
// given the item becomes a signed written book (成书).
async function setBookNbtInCreative(bot, pages, title) {
    if (!gameIsCreative(bot) || bot.supportFeature('itemsWithComponents')) return false;
    try {
        const Item = require('prismarine-item')(bot.registry);
        const byName = bot.registry.itemsByName || {};
        const signing = title != null;
        const id = signing
            ? (byName['written_book'] || {}).id
            : writableBookId(bot);
        if (id == null) return false;
        const heldSlot = HOTBAR0 + (bot.quickBarSlot || 0);
        const value = {
            pages: {
                type: 'list',
                value: {
                    type: 'string',
                    value: pages.map((p) => JSON.stringify({ text: String(p) }))
                }
            }
        };
        if (signing) {
            value.title = { type: 'string', value: String(title) };
            value.author = { type: 'string', value: String(bot.username || '') };
        }
        const nbt = { type: 'compound', name: '', value };
        await bot.creative.setInventorySlot(heldSlot, new Item(id, 1, null, nbt));
        return true;
    } catch (e) {
        return false;
    }
}

async function dropHeldBook(bot, logger) {
    try {
        const held = bot.heldItem;
        if (held) {
            await bot.tossStack(held);
            await sleep(250);
        }
    } catch (e) {
        logger.error('丢出书与笔失败: ' + (e && e.message ? e.message : e));
    }
}

// --- Clear a book and quill -------------------------------------------------
function writeClearToHeldBook(bot) {
    if (bot.supportFeature('editBookIsPluginChannel')) {
        throw new Error('当前游戏版本过旧（< 1.13），暂不支持清空书与笔。');
    }
    if (bot.supportFeature('editBookPacketUsesNbt')) {
        // 1.13 - 1.17: the packet carries the whole book item.
        const Item = require('prismarine-item')(bot.registry);
        const id = writableBookId(bot);
        const book = new Item(id, 1);
        book.nbt = {
            type: 'compound',
            name: '',
            value: { pages: { type: 'list', value: { type: 'string', value: [] } } }
        };
        bot._client.write('edit_book', {
            new_book: Item.toNotch(book), signing: false, hand: 0
        });
    } else {
        // 1.18+: an empty page list wipes the book (still a book and quill).
        bot._client.write('edit_book', { hand: 0, pages: [] });
    }
}

async function clearHeldBookAndConfirm(bot) {
    const heldSlot = HOTBAR0 + (bot.quickBarSlot || 0);
    const event = `updateSlot:${heldSlot}`;
    let applied = false;
    const handler = () => { applied = true; };
    try { bot.inventory.on(event, handler); } catch (e) { /* ignore */ }
    try {
        writeClearToHeldBook(bot);
        for (let i = 0; i < 12 && !applied; i++) {
            await sleep(100);
        }
    } finally {
        try { bot.inventory.removeListener(event, handler); } catch (e) { /* ignore */ }
    }
    return applied;
}

// Creative fallback: replace the held book with a brand-new (empty) one. A
// fresh item has no page data, so it works on every version.
async function clearBookInCreative(bot) {
    if (!gameIsCreative(bot)) return false;
    try {
        const Item = require('prismarine-item')(bot.registry);
        const id = writableBookId(bot);
        if (id == null) return false;
        const heldSlot = HOTBAR0 + (bot.quickBarSlot || 0);
        await bot.creative.setInventorySlot(heldSlot, new Item(id, 1));
        return true;
    } catch (e) {
        return false;
    }
}

/**
 * Erase the content (all pages) of every book and quill in the inventory.
 *
 * @returns {Promise<{ok:boolean,message:string}>}
 */
async function clearBooks(bot, logger) {
    const id = writableBookId(bot);
    if (id == null) return { ok: false, message: '无法识别「书与笔」物品。' };
    if (!bot.inventory) return { ok: false, message: '无法读取背包。' };

    const slots = [];
    for (let i = 9; i <= 44; i++) {
        const it = bot.inventory.slots[i];
        if (it && it.type === id) slots.push(i);
    }
    const off = bot.inventory.slots[45];
    if (off && off.type === id) slots.push(45);
    if (!slots.length) return { ok: false, message: '背包里没有书与笔。' };

    logger.log('info', `准备清空 ${slots.length} 本书与笔的内容。`);
    let cleared = 0;
    for (const slot of slots) {
        const it = bot.inventory.slots[slot];
        if (!it || it.type !== id) continue;
        try {
            await holdBookInMainHand(bot, slot);
            let applied = await clearHeldBookAndConfirm(bot);
            if (!applied) applied = await clearBookInCreative(bot);
            if (applied) cleared++;
            else logger.log('warn', '清空书与笔：未收到服务器回执，可能被服务器/反作弊拦截。');
        } catch (e) {
            logger.error('清空书与笔失败: ' + (e && e.message ? e.message : e));
        }
    }
    if (cleared === 0) {
        return { ok: false, message: '清空失败（服务器未接受，或机器人未持有书与笔）。' };
    }
    return { ok: true, message: `已清空 ${cleared} 本书与笔的内容。` };
}


function formatBookTitle(template, number) {
    return String(template == null ? '' : template).replace(/\{number\}/g, String(number));
}

/**
 * Write `opts.pages` into book(s) and run the configured start/end actions.
 *
 * @param {object} bot        mineflayer bot (must have spawned)
 * @param {object} opts       { pages, startPage, bookStart, pagesPerBook,
 *                              signBook, titleTemplate, startText, endText,
 *                              endDrop, autoGetBook, shouldStop, onProgress }
 * @param {object} logger     { log(level,msg), error(msg) }
 * @param {function} sendChat (text) => void
 * @returns {Promise<{ok:boolean,message:string,nextPage:number,stopped:boolean}>}
 */
async function writeBooks(bot, opts, logger, sendChat) {
    const pages = Array.isArray(opts.pages) ? opts.pages.map((p) => String(p)) : [];
    if (!pages.length) return { ok: false, message: '没有可写入的页面。', nextPage: 0, stopped: false };

    const startText = String(opts.startText || '').trim();
    const endText = String(opts.endText || '').trim();
    const endDrop = !!opts.endDrop;
    const autoGetBook = opts.autoGetBook !== false;
    const signBook = !!opts.signBook;
    const titleTemplate = String(opts.titleTemplate || '');
    const shouldStop = typeof opts.shouldStop === 'function' ? opts.shouldStop : null;
    const onProgress = typeof opts.onProgress === 'function' ? opts.onProgress : null;

    let perBook = parseInt(opts.pagesPerBook, 10);
    if (!perBook || perBook < 1) perBook = 100;
    if (perBook > 100) perBook = 100;

    let startPage = parseInt(opts.startPage, 10);
    if (!startPage || startPage < 0) startPage = 0;
    if (startPage > pages.length) startPage = pages.length;
    let bookStart = parseInt(opts.bookStart, 10);
    if (!bookStart || bookStart < 1) bookStart = 1;

    if (startPage >= pages.length) {
        return { ok: true, stopped: false, nextPage: pages.length, message: '内容已全部写完。' };
    }

    const remaining = pages.length - startPage;
    const totalBooks = Math.ceil(remaining / perBook);
    logger.log('info', `准备写入书与笔：从第 ${startPage + 1} 页开始，共 ${pages.length} 页，本次 ${totalBooks} 本。`);
    logger.log('info', `书与笔：版本 ${bot.version || '?'}，游戏模式 ${(bot.game && bot.game.gameMode) || '?'}，${signBook ? '签名为成书' : '保持书与笔'}。`);
    if (startText) { try { sendChat(startText); } catch (e) { /* ignore */ } }

    const used = new Set();
    let done = 0;
    let stopped = false;
    let nextPage = startPage;
    try {
        for (let b = 0; b < totalBooks; b++) {
            if (shouldStop && shouldStop()) { stopped = true; break; }
            const isLast = b === totalBooks - 1;
            const from = startPage + b * perBook;
            const to = Math.min(from + perBook, pages.length);
            const bookPages = pages.slice(from, to);
            const bookNumber = bookStart + b;

            let slot = findBookSlot(bot, used);
            if (slot < 0 && autoGetBook && gameIsCreative(bot)) {
                slot = await createBookInCreative(bot);
            }
            if (slot < 0) {
                const none = '背包里没有书与笔。请先给机器人一本，或在创造模式下开启「自动获取书与笔」。';
                if (done === 0) return { ok: false, stopped: true, nextPage, message: none };
                if (endText) { try { sendChat(endText); } catch (e) { /* ignore */ } }
                return {
                    ok: true, stopped: true, nextPage,
                    message: `已写入 ${done} 本（书与笔用完，已记录进度：下次从第 ${nextPage + 1} 页继续）。`
                };
            }
            used.add(slot);
            await holdBookInMainHand(bot, slot);
            // The book is now in the main hand; remember that slot too so a
            // failed drop cannot make us rewrite the same book.
            used.add(HOTBAR0 + (bot.quickBarSlot || 0));

            const title = signBook ? formatBookTitle(titleTemplate, bookNumber) : undefined;
            let applied = await writePagesAndConfirm(bot, bookPages, title);
            if (!applied) {
                // Vanilla flow fallback: right-click the book so the server opens
                // it, then send the packet again.
                try { bot.activateItem(); } catch (e) { /* ignore */ }
                await sleep(300);
                applied = await writePagesAndConfirm(bot, bookPages, title);
            }
            if (!applied) {
                applied = await setBookNbtInCreative(bot, bookPages, title);
            }
            if (!applied) {
                // Preserve the last confirmed position and keep this book for inspection.
                const message = `已確認完成 ${done} 本；第 ${bookNumber} 本寫入未獲確認，已停止並保留續寫進度：下次從第 ${nextPage + 1} 頁繼續。請核對遊戲內書籍後重試。`;
                logger.log('warn', message);
                return { ok: false, stopped: true, nextPage, message };
            }
            await sleep(150);
            done++;
            nextPage = to;
            if (onProgress) {
                try {
                    onProgress({ nextPage, nextBook: bookNumber + 1, done, total: totalBooks, signed: signBook });
                } catch (e) { /* ignore */ }
            }
            if (endDrop || !isLast) {
                await dropHeldBook(bot, logger);
            }
        }
        if (endText) { try { sendChat(endText); } catch (e) { /* ignore */ } }
        const finished = nextPage >= pages.length;
        let message;
        if (!finished) {
            message = `已写入 ${done} 本（暂停/停止，已记录进度：下次从第 ${nextPage + 1} 页继续）。`;
        } else {
            message = `已写入书与笔：${pages.length} 页，共 ${done} 本${signBook ? '（成书）' : ''}。`;
        }
        return { ok: true, stopped, nextPage, message };
    } catch (e) {
        const msg = (e && e.message) ? e.message : String(e);
        logger.error('写入书与笔出错: ' + msg);
        return {
            ok: done > 0, stopped, nextPage,
            message: done > 0 ? `已写入 ${done} 本，随后出错：${msg}` : `写入失败：${msg}`
        };
    }
}

module.exports = { writeBooks, clearBooks, HOTBAR0 };
