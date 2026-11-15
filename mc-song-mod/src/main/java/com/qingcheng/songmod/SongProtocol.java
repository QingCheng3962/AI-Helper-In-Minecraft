package com.qingcheng.songmod;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * 机器人 ⇄ 客户端 Mod 的聊天协议（纯字符串解析，<b>不依赖 Minecraft 类</b>，方便单测）。
 *
 * <p>协议总览（触发词默认 {@code [点歌]}）：
 * <ul>
 *   <li><b>播放数据</b>（机器人 → 我，公屏或 /msg）：{@code 歌名 - 歌手 | ¤混淆直链 id:123456 at:30}
 *       —— {@code ¤}=http、{@code ¥}=https；{@code id:} 用于直链被服务器改坏时自己重取；
 *       {@code at:} 是「一起听」的可选起播位置（秒）。</li>
 *   <li><b>一起听名单</b>（机器人 → 我）：{@code 一起听名单|玩家~歌名~已播秒数;玩家2~歌名2~秒数}</li>
 *   <li><b>一起听请求</b>（我 → 机器人）：{@code 一起听}（要名单）/{@code 一起听 玩家}（加入）</li>
 *   <li><b>点歌</b>（我 → 机器人）：{@code 歌名 1.5}（末尾可选倍速）</li>
 * </ul>
 */
public final class SongProtocol {

    /** 混淆直链标记：http。 */
    public static final String MARK_HTTP = "\u00a4";
    /** 混淆直链标记：https。 */
    public static final String MARK_HTTPS = "\u00a5";

    /** 「一起听」指令词。 */
    public static final String LISTEN_WORD = "一起听";
    /** 名单标记（机器人回包前缀）。 */
    public static final String LISTEN_LIST_MARK = "一起听名单|";

    private SongProtocol() {
    }

    // ------------------------------------------------------------------
    // 播放数据
    // ------------------------------------------------------------------

    /** 解析一条可能是播放数据的聊天内容；不是播放数据时返回 null。 */
    public static Playback parsePlayback(String payload) {
        if (payload == null || payload.isEmpty()) {
            return null;
        }
        String url = extractObfuscatedUrl(payload);
        if (url == null) {
            url = firstAudioUrl(payload);
        }
        String id = extractSongId(payload);
        if (url == null && id == null) {
            return null;
        }
        return new Playback(url, id, extractAtSeconds(payload), songLabel(payload, url));
    }

    /**
     * 还原机器人发来的混淆直链。
     *
     * <p>公屏的链接过滤会把 {@code http(s)://…} 整条替换成「[链接]」，所以机器人发的是：
     * {@code ¤}<b>混淆直链</b>（{@code ¤}=http、{@code ¥}=https，且把
     * {@code . / :} 换成了 {@code · ¦ ∶}、去掉了 query）。这里还原成完整 URL。
     */
    public static String extractObfuscatedUrl(String payload) {
        if (payload == null) {
            return null;
        }
        int a = payload.indexOf(MARK_HTTP);
        int b = payload.indexOf(MARK_HTTPS);
        int pos = a < 0 ? b : (b < 0 ? a : Math.min(a, b));
        if (pos < 0) {
            return null;
        }
        String scheme = payload.charAt(pos) == MARK_HTTPS.charAt(0) ? "https://" : "http://";
        String rest = payload.substring(pos + 1).trim();
        int end = 0;
        while (end < rest.length() && !Character.isWhitespace(rest.charAt(end))) {
            end++;
        }
        String body = rest.substring(0, end)
                .replace('·', '.').replace('¦', '/').replace('∶', ':');
        return body.isEmpty() ? null : scheme + body;
    }

    /** 从文本里找第一个看起来像音频直链的 URL（机器人万一没混淆时的兜底）。 */
    public static String firstAudioUrl(String text) {
        if (text == null) {
            return null;
        }
        int i = text.indexOf("http");
        while (i >= 0) {
            int end = i;
            while (end < text.length() && !Character.isWhitespace(text.charAt(end))) {
                end++;
            }
            String candidate = text.substring(i, end);
            String low = candidate.toLowerCase(Locale.ROOT);
            if (low.contains(".mp3") || low.contains("music.126.net") || low.contains(".m4a")
                    || low.contains(".flac") || low.contains("song/media/outer/url")) {
                return candidate;
            }
            i = text.indexOf("http", end);
        }
        return null;
    }

    /**
     * 找歌曲 id：机器人格式是 {@code … | ¤混淆直链 id:3399839173}。
     *
     * <p>不能用 {@code #}（服务器当十六进制颜色码吃掉）、也不能用 {@code &}（MC 颜色符），
     * 所以用 {@code id:} 前缀；另兼容老的 {@code #123456} 写法。
     */
    public static String extractSongId(String payload) {
        if (payload == null) {
            return null;
        }
        String low = payload.toLowerCase(Locale.ROOT);
        for (int i = low.indexOf("id:"); i >= 0; i = low.indexOf("id:", i + 3)) {
            int end = i + 3;
            while (end < payload.length() && Character.isDigit(payload.charAt(end))) {
                end++;
            }
            if (end - (i + 3) >= 5) {
                return payload.substring(i + 3, end);
            }
        }
        int hash = payload.lastIndexOf('#');
        if (hash >= 0) {
            int end = hash + 1;
            while (end < payload.length() && Character.isDigit(payload.charAt(end))) {
                end++;
            }
            String digits = payload.substring(hash + 1, end);
            if (digits.length() >= 5) {
                return digits;
            }
        }
        return null;
    }

    /** 「一起听」起播位置：{@code at:125} → 125（秒）；没有则 -1。 */
    public static int extractAtSeconds(String payload) {
        if (payload == null) {
            return -1;
        }
        String low = payload.toLowerCase(Locale.ROOT);
        int i = low.indexOf("at:");
        if (i < 0) {
            return -1;
        }
        int end = i + 3;
        while (end < payload.length() && Character.isDigit(payload.charAt(end))) {
            end++;
        }
        if (end == i + 3 || end - (i + 3) > 6) {
            return -1;
        }
        try {
            return Integer.parseInt(payload.substring(i + 3, end));
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    /** 播放标签：取直链前面那段（机器人格式是「文案 | 直链」）。 */
    public static String songLabel(String payload, String audioUrl) {
        if (payload == null) {
            return "歌曲";
        }
        String label = payload;
        int bar = label.lastIndexOf('|');
        if (bar > 0) {
            label = label.substring(0, bar);
        } else if (audioUrl != null) {
            int idx = payload.indexOf(audioUrl);
            if (idx > 0) {
                label = payload.substring(0, idx);
            }
        }
        label = label.replace(MARK_HTTP, " ").replace(MARK_HTTPS, " ")
                .replaceAll("[|\\-]+\\s*$", "").trim();
        return label.isEmpty() ? "歌曲" : label;
    }

    // ------------------------------------------------------------------
    // 一起听
    // ------------------------------------------------------------------

    /**
     * 解析「一起听」相关指令（只看我自己发的那句，触发词之后的部分）。
     *
     * @param after 触发词之后的内容，例如 {@code "一起听 王小明 1.5"}
     * @return 不是一起听指令时返回 null
     */
    public static ListenJoin parseListenJoin(String after) {
        if (after == null) {
            return null;
        }
        String text = after.trim();
        if (text.isEmpty()) {
            return null;
        }
        // 全角/半角都认：一起听 / 一起听 玩家
        if (!text.startsWith(LISTEN_WORD)) {
            String folded = text.replace("\uFF0F", "/");
            if (!folded.startsWith(LISTEN_WORD)) {
                return null;
            }
        }
        String rest = text.substring(LISTEN_WORD.length()).trim();
        SongSpeed.Split split = SongSpeed.splitTrailing(rest);
        String player = split.text.trim();
        if (player.isEmpty()) {
            return new ListenJoin("", split.speed, true);
        }
        // 只取第一个词（玩家名不允许空格）
        int space = player.indexOf(' ');
        if (space > 0) {
            player = player.substring(0, space).trim();
        }
        if (player.isEmpty() || player.length() > 16) {
            return new ListenJoin("", split.speed, true);
        }
        // 玩家名只允许 ASCII 字母/数字/下划线（MC 命名规则）——防止把「一起听 好 听 的 歌」当玩家
        if (!isAsciiName(player)) {
            return null;
        }
        return new ListenJoin(player, split.speed, false);
    }

    /** 解析机器人回的一起听名单；格式不对时返回空 list。 */
    public static List<ListenEntry> parseListenList(String payload) {
        List<ListenEntry> out = new ArrayList<>();
        if (payload == null) {
            return out;
        }
        int mark = payload.indexOf(LISTEN_LIST_MARK);
        if (mark < 0) {
            return out;
        }
        String body = payload.substring(mark + LISTEN_LIST_MARK.length()).trim();
        if (body.isEmpty()) {
            return out;
        }
        for (String chunk : body.split(";")) {
            String item = chunk.trim();
            if (item.isEmpty()) {
                continue;
            }
            String[] parts = item.split("~", -1);
            if (parts.length < 2) {
                // 容错：只有名字（没有歌名）
                if (isPlayerName(parts[0].trim())) {
                    out.add(new ListenEntry(parts[0].trim(), "", -1));
                }
                continue;
            }
            String player = parts[0].trim();
            String label = parts[1].trim();
            if (!isPlayerName(player)) {
                continue;
            }
            int elapsed = -1;
            if (parts.length >= 3) {
                try {
                    elapsed = Integer.parseInt(parts[2].trim());
                } catch (NumberFormatException ignored) {
                    elapsed = -1;
                }
            }
            out.add(new ListenEntry(player, label, elapsed));
        }
        return out;
    }

    private static boolean isPlayerName(String s) {
        return s.length() <= 16 && isAsciiName(s);
    }

    /** 玩家名：只允许 ASCII 字母 / 数字 / 下划线（中文会被 Character.isLetterOrDigit 当成字母）。 */
    private static boolean isAsciiName(String s) {
        if (s.isEmpty()) {
            return false;
        }
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c > 0x7F) {
                return false;
            }
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                    || (c >= '0' && c <= '9') || c == '_')) {
                return false;
            }
        }
        return true;
    }

    // ------------------------------------------------------------------
    // 数据类
    // ------------------------------------------------------------------

    /** 一条播放数据。 */
    public static final class Playback {
        /** 可播放直链（可能为 null：只有 id 时自己取链）。 */
        public final String url;
        /** 网易云歌曲 id（可能为 null）。 */
        public final String songId;
        /** 一起听的起播秒数；-1 表示从头播。 */
        public final int atSeconds;
        /** 展示用标签（歌名 - 歌手）。 */
        public final String label;

        public Playback(String url, String songId, int atSeconds, String label) {
            this.url = url;
            this.songId = songId;
            this.atSeconds = atSeconds;
            this.label = label;
        }

        public boolean hasUrl() {
            return url != null && !url.isEmpty();
        }
    }

    /** 解析出来的「一起听」指令。 */
    public static final class ListenJoin {
        /** 目标玩家；空串 = 只要名单。 */
        public final String player;
        /** 倍速；-1 = 没写。 */
        public final float speed;
        /** true = 只要名单（{@code [点歌]一起听}）。 */
        public final boolean listOnly;

        public ListenJoin(String player, float speed, boolean listOnly) {
            this.player = player == null ? "" : player;
            this.speed = speed;
            this.listOnly = listOnly;
        }
    }

    /** 一起听名单里的一条：谁在听什么、已经播了多少秒。 */
    public static final class ListenEntry {
        public final String player;
        public final String label;
        /** 机器人估计的已播秒数；-1 = 未知。 */
        public final int elapsedSeconds;

        public ListenEntry(String player, String label, int elapsedSeconds) {
            this.player = player;
            this.label = label;
            this.elapsedSeconds = elapsedSeconds;
        }
    }
}
