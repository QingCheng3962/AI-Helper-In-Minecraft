'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { AutoEat } = require('../autoeat');

function makeBot() {
    const slots = new Array(45).fill(null);
    slots[9] = { name: 'bread' };
    return {
        food: 5,
        quickBarSlot: 0,
        heldItem: { name: 'bread' },
        inventory: { slots },
        moves: 0,
        activates: 0,
        setQuickBarSlot() {},
        async moveSlotItem() { this.moves++; },
        activateItem() { this.activates++; },
        on() {},
    };
}

function makeLog() {
    return { info() {}, warn() {}, error() {} };
}

test('stop cancels the eating interval so an old session cannot keep eating', async () => {
    const bot = makeBot();
    const eat = new AutoEat(bot, makeLog(), { autoeat: { enabled: true } });
    await eat._check();
    assert.ok(eat._interval, 'interval should start after moving food');

    eat.setEnabled(false);
    assert.strictEqual(eat._interval, null);
    assert.strictEqual(eat.eating, false);
    const before = bot.activates;
    await new Promise((resolve) => setTimeout(resolve, 600));
    assert.strictEqual(bot.activates, before, 'old interval must not touch the bot');
});

test('disabled AutoEat does not start a new interval', async () => {
    const bot = makeBot();
    const eat = new AutoEat(bot, makeLog(), { autoeat: { enabled: false } });
    await eat._check();
    assert.strictEqual(eat._interval, null);
    assert.strictEqual(bot.moves, 0);
});
