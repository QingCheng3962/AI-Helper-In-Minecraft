'use strict';

// Auto-solve a chest-GUI "human verification".
//
// Two strategies:
//   1) Named targets: click items whose name contains `nameKeyword`
//      (e.g. "点击这里", "点击这里步骤1"/"步骤2"); items with 步骤N are clicked
//      in numeric order.
//   2) Fallback: group container slots by signature (name + metadata + custom
//      name); the majority is the background, the odd slots are clicked.
//
// Enabled via config.verify (see aafm_py/config.py).

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class VerifyHandler {
    constructor(bot, Log, Config) {
        this.bot = bot;
        this.Log = Log;
        this.config = (Config && Config.verify) || {};
        this.enabled = this.config.enabled === true;
        this.busy = false;
        this.seen = new WeakSet();
        if (this.enabled) {
            this.bot.on('windowOpen', (window) => this.onWindow(window));
        }
    }

    onWindow(window) {
        if (!this.enabled || !window) return;
        if (this.seen.has(window)) return; // ignore duplicate events
        this.seen.add(window);
        const delay = Math.max(0, parseInt(this.config.startDelayMs, 10) || 0);
        setTimeout(() => {
            this._handle(window).catch((e) => {
                this.Log.warn('验证处理出错: ' + (e && e.message ? e.message : String(e)));
            });
        }, delay);
    }

    _slotSig(item) {
        if (!item) return null;
        const meta = item.metadata == null ? '' : item.metadata;
        const custom = item.customName || '';
        return `${item.name}#${meta}#${custom}`;
    }

    _nbtName(item) {
        try {
            const v = item && item.nbt && item.nbt.value;
            const disp = v && v.display && v.display.value;
            const nm = disp && disp.Name && disp.Name.value;
            if (nm == null) return '';
            return typeof nm === 'string' ? nm : JSON.stringify(nm);
        } catch (e) {
            return '';
        }
    }

    _text(item) {
        if (!item) return '';
        const parts = [];
        for (const v of [item.customName, this._nbtName(item), item.displayName, item.name]) {
            if (v) parts.push(String(v));
        }
        return parts.join(' ');
    }

    _step(text) {
        const s = String(text == null ? '' : text);
        const m = s.match(/步骤\s*(\d+)/) || s.match(/(\d+)\s*[）)】\]]?\s*$/);
        return m ? parseInt(m[1], 10) : null;
    }

    _filled(window) {
        const total = window.slots ? window.slots.length : 0;
        let end = total;
        if (typeof window.inventoryStart === 'number'
            && window.inventoryStart > 0 && window.inventoryStart <= total) {
            end = window.inventoryStart;
        }
        const out = [];
        for (let i = 0; i < end; i++) {
            const it = window.slots[i];
            if (it) out.push({ item: it, slot: i });
        }
        return out;
    }

    _dump(window, filled) {
        if (this.config.debug === false) return;
        const parts = filled.slice(0, 40).map((x) => {
            const nm = this._text(x.item);
            return `${x.slot}:${x.item.name}|${nm}`;
        });
        this.Log.info(`[验证] 标题="${String(window.title == null ? '' : window.title)}" `
            + `槽位${filled.length}: ${parts.join('  ')}`);
    }

    _findTargets(filled) {
        const nameKw = String(this.config.nameKeyword || '').trim();
        if (nameKw) {
            const named = filled.filter((x) => this._text(x.item).indexOf(nameKw) >= 0);
            if (named.length) {
                const withStep = named.map((x) => ({
                    item: x.item, slot: x.slot, step: this._step(this._text(x.item)),
                }));
                const hasSteps = withStep.some((x) => x.step != null);
                const pool = hasSteps ? withStep.filter((x) => x.step != null) : withStep;
                pool.sort((a, b) => (hasSteps ? a.step - b.step : a.slot - b.slot));
                return pool;
            }
        }
        if (filled.length < 6) return [];
        const counts = new Map();
        for (const x of filled) {
            const s = this._slotSig(x.item);
            counts.set(s, (counts.get(s) || 0) + 1);
        }
        let majorSig = null;
        let majorCount = -1;
        for (const [sig, c] of counts) {
            if (c > majorCount) { majorCount = c; majorSig = sig; }
        }
        const ratio = majorCount / filled.length;
        const minMajority = Number(this.config.minMajority) || 0.6;
        const maxTargets = Number(this.config.maxTargets) || 12;
        if (ratio < minMajority) return [];
        const odd = filled.filter((x) => this._slotSig(x.item) !== majorSig);
        if (odd.length < 1 || odd.length > maxTargets) return [];
        return odd;
    }

    async _handle(window) {
        const title = String(window.title == null ? '' : window.title);
        const titleKw = String(this.config.titleKeyword || '').trim();
        if (titleKw && title.indexOf(titleKw) < 0) return;

        // Items can arrive slightly after the window opens; retry a few times.
        let targets = [];
        for (let attempt = 0; attempt < 4; attempt++) {
            const filled = this._filled(window);
            if (attempt === 0) this._dump(window, filled);
            targets = this._findTargets(filled);
            if (targets.length) break;
            if (attempt < 3) await sleep(350);
        }
        if (!targets.length) return;

        this.busy = true;
        try {
            this.Log.info(`验证界面: 标题="${title}" 目标=${targets.length}`
                + (targets[0].step != null ? ` 步骤${targets.map((t) => t.step).join(',')}` : ''));
            const base = Number(this.config.clickDelayMs) || 0;
            for (const t of targets) {
                try {
                    await this.bot.clickWindow(t.slot, 0, 0);
                } catch (e) {
                    this.Log.warn('验证点击失败: ' + (e && e.message ? e.message : String(e)));
                }
                const wait = base + Math.floor(Math.random() * base);
                if (wait > 0) await sleep(wait);
            }
            this.Log.info(`验证点击完成：${targets.length} 个`);
        } finally {
            this.busy = false;
        }
    }
}

module.exports = { VerifyHandler };
