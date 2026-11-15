package com.qingcheng.songmod;

import java.util.Locale;

/**
 * 倍速：0.5 / 0.75 / 1.0 / 1.5 / 2.0。
 *
 * <p>两个用途：
 * <ol>
 *   <li>聊天语法 {@code [点歌]歌名 1.5} —— 解析/剥掉末尾的倍速 token（机器人搜索前也要剥掉）；</li>
 *   <li>客户端播放引擎的重采样倍率（见 {@link SongPlayer}）。</li>
 * </ol>
 *
 * 这个类<b>不依赖任何 Minecraft 类</b>，所以能脱离游戏做单元测试。
 */
public final class SongSpeed {

    /** 界面上提供的档位（顺序即显示顺序）。 */
    public static final float[] SPEEDS = {0.5f, 0.75f, 1.0f, 1.5f, 2.0f};

    /** 1.0 倍速（不重采样）。 */
    public static final float NORMAL = 1.0f;

    private SongSpeed() {
    }

    /** 把任意倍率吸附到最近的档位；非法值返回 1.0。 */
    public static float normalize(float speed) {
        float best = NORMAL;
        float bestDiff = Float.MAX_VALUE;
        for (float s : SPEEDS) {
            float diff = Math.abs(s - speed);
            if (diff < bestDiff) {
                bestDiff = diff;
                best = s;
            }
        }
        return best;
    }

    /** 档位的短名：{@code 0.5 / 0.75 / 1.0 / 1.5 / 2.0}。 */
    public static String key(float speed) {
        float s = normalize(speed);
        // 0.75 需要两位小数，其余一位就够。
        if (Math.abs(s - 0.75f) < 0.001f) {
            return "0.75";
        }
        return String.format(Locale.ROOT, "%.1f", s);
    }

    /** 界面显示名：{@code 1.5x}。 */
    public static String label(float speed) {
        return key(speed) + "x";
    }

    /**
     * 解析一个倍速 token（如 {@code 1.5} / {@code 1.5x} / {@code 1.5倍} / {@code x1.5}）。
     *
     * @return 吸附到档位后的值；不是倍速 token 时返回 {@code -1}
     */
    public static float parseToken(String token) {
        if (token == null) {
            return -1f;
        }
        String s = token.trim().toLowerCase(Locale.ROOT)
                .replace("倍", "")
                .replace("x", "")
                .trim();
        if (s.isEmpty() || s.length() > 6) {
            return -1f;
        }
        try {
            float value = Float.parseFloat(s);
            if (value <= 0.2f || value > 4.0f) {
                return -1f;
            }
            return normalize(value);
        } catch (NumberFormatException e) {
            return -1f;
        }
    }

    /**
     * 从一句话末尾剥掉倍速：{@code "起风了 1.5"} → {@code ("起风了", 1.5)}。
     *
     * <p>只有在「前面还有别的内容」时才剥，避免把歌名本身（例如纯数字歌名）吃掉。
     */
    public static Split splitTrailing(String text) {
        if (text == null) {
            return new Split("", -1f);
        }
        String s = text.trim();
        if (s.isEmpty()) {
            return new Split("", -1f);
        }
        int space = s.lastIndexOf(' ');
        if (space > 0) {
            String tail = s.substring(space + 1).trim();
            float speed = parseToken(tail);
            if (speed > 0) {
                return new Split(s.substring(0, space).trim(), speed);
            }
        }
        return new Split(s, -1f);
    }

    /** {@link #splitTrailing(String)} 的结果。 */
    public static final class Split {
        /** 去掉倍速后的内容。 */
        public final String text;
        /** 倍速；没有倍速时为 -1。 */
        public final float speed;

        public Split(String text, float speed) {
            this.text = text == null ? "" : text;
            this.speed = speed;
        }

        public boolean hasSpeed() {
            return speed > 0;
        }
    }
}
