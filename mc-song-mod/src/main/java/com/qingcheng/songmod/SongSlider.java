package com.qingcheng.songmod;

import net.minecraft.client.gui.components.AbstractSliderButton;
import net.minecraft.network.chat.Component;

/**
 * 通用滑条（进度条 / 音量条）。
 *
 * <p>为什么用 {@link AbstractSliderButton} 而不是自己画：它的<b>鼠标拖动、键盘方向键、
 * 焦点管理</b>都由各版本自己实现好了。1.21.1 的签名是 {@code onClick(double,double)}，
 * 1.21.9+/26.x 换成了 {@code onClick(MouseButtonEvent,boolean)} —— 自己写就跨不了版本，
 * 交给父类就没有这个问题。我们只实现 {@link #updateMessage()} / {@link #applyValue()}。
 */
public class SongSlider extends AbstractSliderButton {

    /** 拖动回调（拖动/点击时触发）。 */
    public interface Handler {
        void onChanged(SongSlider slider, double value);
    }

    private final Handler handler;
    private String label = "";
    /** 程序更新数值时不触发回调（避免「进度自动走」被当成用户拖动）。 */
    private boolean silent;

    public SongSlider(int x, int y, int width, int height, double value, Handler handler) {
        super(x, y, width, height, Component.literal(""), clamp(value));
        this.handler = handler;
        updateMessage();
    }

    /** 设置滑条上显示的文字。 */
    public void setLabel(String text) {
        String next = text == null ? "" : text;
        if (next.equals(label)) {
            return;
        }
        label = next;
        updateMessage();
    }

    /** 程序设置数值（不触发回调）。 */
    public void setValueSilently(double value) {
        double next = clamp(value);
        if (Math.abs(next - this.value) < 1e-4) {
            return;
        }
        silent = true;
        try {
            this.value = next;
            updateMessage();
        } finally {
            silent = false;
        }
    }

    public double value() {
        return this.value;
    }

    @Override
    protected void updateMessage() {
        setMessage(Component.literal(label));
    }

    @Override
    protected void applyValue() {
        if (silent || handler == null) {
            return;
        }
        handler.onChanged(this, this.value);
    }

    private static double clamp(double value) {
        if (Double.isNaN(value)) {
            return 0.0;
        }
        return Math.max(0.0, Math.min(1.0, value));
    }
}
