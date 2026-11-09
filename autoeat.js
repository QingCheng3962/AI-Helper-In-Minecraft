const FOODS = [
    'bread', 'apple', 'golden_carrot', 'cooked_beef', 'cooked_porkchop',
    'cooked_chicken', 'cooked_cod', 'cooked_salmon', 'cookie',
    'melon_slice', 'carrot', 'potato', 'baked_potato', 'beetroot',
    'cooked_rabbit', 'mutton', 'cooked_mutton', 'chorus_fruit',
    'dried_kelp', 'sweet_berries', 'glow_berries'
];

const FOOD_THRESHOLD = 7; // 饱食度低于此值时进食

class AutoEat {
    constructor(bot, Log, Config) {
        this.bot = bot;
        this.Log = Log;
        this.enabled = Config.autoeat?.enabled ?? true;
        this.eating = false;
        this.originalSlot = null;
        // 每次进食一个代次；stop/disable/断开后旧定时器不能再操作新会话（#47）。
        this._runId = 0;
        this._interval = null;

        // Register the health listener exactly once; the callback itself checks
        // `enabled`, so runtime toggles (engine command or toggle()) never leak
        // or duplicate listeners (#25).
        this._onHealth = () => this._check();
        this.bot.on('health', this._onHealth);
        this.Log.info("AutoEat " + (this.enabled ? "enabled" : "disabled"));
    }

    _findFood() {
        for (let i = 9; i < 45; i++) {
            const item = this.bot.inventory.slots[i];
            if (item && FOODS.includes(item.name)) {
                return { slot: i, item };
            }
        }
        return null;
    }

    async _check() {
        if (this.eating || !this.enabled || this._interval) return;
        if ((this.bot.food || 0) > FOOD_THRESHOLD) return;

        const food = this._findFood();
        if (!food) {
            this.Log.warn("AutoEat: no food in inventory");
            return;
        }

        const bot = this.bot;
        const runId = ++this._runId;
        this.eating = true;
        this.originalSlot = bot.quickBarSlot;

        try {
            await bot.moveSlotItem(food.slot, bot.quickBarSlot + 36);
            await new Promise(r => setTimeout(r, 100));
            if (!this.enabled || runId !== this._runId || this.bot !== bot) {
                return;
            }
            bot.activateItem();
            this.Log.info(`AutoEat: eating ${food.item.name}`);

            this._interval = setInterval(() => {
                if (!this.enabled || runId !== this._runId || this.bot !== bot) {
                    this.stop();
                    return;
                }
                if (bot.food >= 20 || !bot.heldItem) {
                    const originalSlot = this.originalSlot;
                    this.stop();
                    this._finish(food.slot, bot, originalSlot);
                } else {
                    bot.activateItem();
                }
            }, 500);
        } catch (err) {
            this.Log.error(`AutoEat error: ${err.message}`);
            this.stop();
        }
    }

    async _finish(foodSlot, bot = this.bot, originalSlot = this.originalSlot) {
        try {
            if (bot.heldItem && foodSlot !== null) {
                await bot.moveSlotItem(bot.quickBarSlot + 36, foodSlot);
            }
            if (originalSlot !== null) {
                bot.setQuickBarSlot(originalSlot);
            }
        } catch (err) {
            this.Log.error(`AutoEat finish error: ${err.message}`);
        }
        this.eating = false;
        this.originalSlot = null;
        this.Log.info("AutoEat: done");
    }

    /** 取消当前进食和定时器；断开/关闭时调用，旧回调不能再碰 bot（#47）。 */
    stop() {
        this._runId++;
        if (this._interval) {
            clearInterval(this._interval);
            this._interval = null;
        }
        this.eating = false;
        this.originalSlot = null;
    }

    setEnabled(enabled) {
        this.enabled = !!enabled;
        if (!this.enabled) {
            this.stop();
        } else {
            this._check();
        }
        this.Log.info("AutoEat " + (this.enabled ? "enabled" : "disabled"));
        return this.enabled;
    }

    toggle() {
        return this.setEnabled(!this.enabled);
    }
}

module.exports = { AutoEat };
