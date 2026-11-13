package com.qingcheng.songmod;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 游戏内歌词：按网易云歌曲 id 抓 LRC 并解析成时间轴。
 *
 * <p><b>不依赖 Minecraft 类</b>（方便单测）：网络走 {@link HttpURLConnection}，
 * JSON 只做最小提取，不引入额外库。
 */
public final class SongLyrics {

    private static final String UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";

    /** 最多缓存几首歌的歌词（来回听老歌不用重复请求）。 */
    private static final int CACHE_SIZE = 8;

    private static final Map<String, Lyrics> CACHE = new LinkedHashMap<String, Lyrics>(16, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Lyrics> eldest) {
            return size() > CACHE_SIZE;
        }
    };

    private SongLyrics() {
    }

    /** 取歌词（带缓存；失败返回 null）。 */
    public static Lyrics get(String songId, String cookie) {
        if (songId == null || songId.trim().isEmpty()) {
            return null;
        }
        String id = songId.trim();
        synchronized (CACHE) {
            if (CACHE.containsKey(id)) {
                return CACHE.get(id);
            }
        }
        Lyrics lyrics = fetch(id, cookie);
        if (lyrics != null && lyrics.isEmpty()) {
            lyrics = null;
        }
        synchronized (CACHE) {
            CACHE.put(id, lyrics == null ? Lyrics.EMPTY : lyrics);
        }
        return lyrics;
    }

    private static Lyrics fetch(String songId, String cookie) {
        try {
            String url = "https://music.163.com/api/song/lyric?os=pc&id=" + songId
                    + "&lv=-1&kv=-1&tv=-1";
            String body = httpGet(url, cookie);
            if (body == null) {
                return null;
            }
            String lrc = extractJsonString(body, "lyric");
            if (lrc == null || lrc.trim().isEmpty()) {
                return null;
            }
            return parse(lrc);
        } catch (Exception e) {
            return null;
        }
    }

    private static String httpGet(String url, String cookie) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(12000);
            conn.setRequestProperty("User-Agent", UA);
            conn.setRequestProperty("Referer", "https://music.163.com/");
            conn.setRequestProperty("Cookie", baseCookie(cookie));
            int code = conn.getResponseCode();
            if (code != 200) {
                return null;
            }
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                    if (out.size() > 2 << 20) {
                        break;
                    }
                }
                return new String(out.toByteArray(), StandardCharsets.UTF_8);
            }
        } catch (Exception e) {
            return null;
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private static String baseCookie(String cookie) {
        String base = "os=pc; appver=2.9.7; channel=netease;";
        if (cookie == null || cookie.trim().isEmpty()) {
            return base;
        }
        return base + " " + cookie.trim();
    }

    /** 从 JSON 文本里抠出某个字符串字段（支持换行、斜杠、引号、unicode 转义）。 */
    static String extractJsonString(String json, String field) {
        if (json == null) {
            return null;
        }
        String key = "\"" + field + "\"";
        int at = json.indexOf(key);
        if (at < 0) {
            return null;
        }
        int colon = json.indexOf(':', at + key.length());
        if (colon < 0) {
            return null;
        }
        int i = colon + 1;
        while (i < json.length() && Character.isWhitespace(json.charAt(i))) {
            i++;
        }
        if (i >= json.length() || json.charAt(i) != '"') {
            return null;
        }
        i++;
        StringBuilder out = new StringBuilder();
        while (i < json.length()) {
            char c = json.charAt(i++);
            if (c == '"') {
                return out.toString();
            }
            if (c != '\\') {
                out.append(c);
                continue;
            }
            if (i >= json.length()) {
                break;
            }
            char esc = json.charAt(i++);
            switch (esc) {
                case 'n': out.append('\n'); break;
                case 'r': out.append('\r'); break;
                case 't': out.append('\t'); break;
                case 'b': out.append('\b'); break;
                case 'f': out.append('\f'); break;
                case 'u':
                    if (i + 4 <= json.length()) {
                        try {
                            out.append((char) Integer.parseInt(json.substring(i, i + 4), 16));
                        } catch (NumberFormatException ignored) {
                            // 忽略坏转义
                        }
                        i += 4;
                    }
                    break;
                default:
                    out.append(esc);
            }
        }
        return out.toString();
    }

    // ------------------------------------------------------------------
    // LRC 解析
    // ------------------------------------------------------------------

    /**
     * 解析 LRC：{@code [00:12.34]歌词}，一行可以有多个时间标签。
     *
     * <p>跳过 {@code [ar:]}/{@code [ti:]} 之类的元信息、跳过空文本行；
     * {@code [offset:500]} 会整体平移时间轴。
     */
    public static Lyrics parse(String lrc) {
        if (lrc == null || lrc.trim().isEmpty()) {
            return Lyrics.EMPTY;
        }
        List<long[]> stamps = new ArrayList<>();
        List<String> texts = new ArrayList<>();
        long offset = 0L;

        for (String rawLine : lrc.split("\n")) {
            String line = rawLine.replace("\r", "").trim();
            if (line.isEmpty()) {
                continue;
            }
            List<Long> lineTimes = new ArrayList<>();
            int cursor = 0;
            while (cursor < line.length() && line.charAt(cursor) == '[') {
                int close = line.indexOf(']', cursor);
                if (close < 0) {
                    break;
                }
                String tag = line.substring(cursor + 1, close).trim();
                long ms = parseStamp(tag);
                if (ms < 0) {
                    // 不是时间标签：可能是 [offset:500]
                    if (tag.toLowerCase().startsWith("offset:")) {
                        try {
                            offset = Long.parseLong(tag.substring(7).trim());
                        } catch (NumberFormatException ignored) {
                            // 忽略
                        }
                    }
                    break;
                }
                lineTimes.add(ms);
                cursor = close + 1;
            }
            String text = line.substring(Math.min(cursor, line.length())).trim();
            if (lineTimes.isEmpty() || text.isEmpty()) {
                continue;
            }
            for (Long t : lineTimes) {
                stamps.add(new long[]{t});
                texts.add(text);
            }
        }
        if (stamps.isEmpty()) {
            return Lyrics.EMPTY;
        }
        Integer[] order = new Integer[stamps.size()];
        for (int i = 0; i < order.length; i++) {
            order[i] = i;
        }
        final long shift = offset;
        java.util.Arrays.sort(order, (a, b) -> Long.compare(stamps.get(a)[0], stamps.get(b)[0]));
        long[] times = new long[order.length];
        String[] lines = new String[order.length];
        for (int i = 0; i < order.length; i++) {
            int src = order[i];
            times[i] = Math.max(0L, stamps.get(src)[0] - shift);
            lines[i] = texts.get(src);
        }
        return new Lyrics(times, lines);
    }

    /** {@code mm:ss.xx} / {@code mm:ss.xxx} / {@code mm:ss} → 毫秒；不是时间标签返回 -1。 */
    private static long parseStamp(String tag) {
        if (tag.isEmpty()) {
            return -1;
        }
        int colon = tag.indexOf(':');
        if (colon <= 0) {
            return -1;
        }
        String mm = tag.substring(0, colon).trim();
        String rest = tag.substring(colon + 1).trim();
        if (!isDigits(mm)) {
            return -1;
        }
        int dot = rest.indexOf('.');
        String ss = dot < 0 ? rest : rest.substring(0, dot);
        String frac = dot < 0 ? "" : rest.substring(dot + 1);
        if (!isDigits(ss) || (!frac.isEmpty() && !isDigits(frac))) {
            return -1;
        }
        long minutes = Long.parseLong(mm);
        long seconds = Long.parseLong(ss);
        long ms = 0L;
        if (!frac.isEmpty()) {
            if (frac.length() <= 3) {
                String padded = (frac + "000").substring(0, 3);
                ms = Long.parseLong(padded);
            } else {
                ms = Long.parseLong(frac.substring(0, 3));
            }
        }
        return minutes * 60000L + seconds * 1000L + ms;
    }

    private static boolean isDigits(String s) {
        if (s.isEmpty() || s.length() > 9) {
            return false;
        }
        for (int i = 0; i < s.length(); i++) {
            if (!Character.isDigit(s.charAt(i))) {
                return false;
            }
        }
        return true;
    }

    /** 解析好的歌词时间轴（不可变）。 */
    public static final class Lyrics {
        public static final Lyrics EMPTY = new Lyrics(new long[0], new String[0]);

        public final long[] times;
        public final String[] lines;

        Lyrics(long[] times, String[] lines) {
            this.times = times;
            this.lines = lines;
        }

        public boolean isEmpty() {
            return times.length == 0;
        }

        /** 当前时间对应的行下标；还没到第一句时返回 -1。 */
        public int indexAt(long positionMs) {
            if (times.length == 0) {
                return -1;
            }
            int lo = 0;
            int hi = times.length - 1;
            int found = -1;
            while (lo <= hi) {
                int mid = (lo + hi) >>> 1;
                if (times[mid] <= positionMs) {
                    found = mid;
                    lo = mid + 1;
                } else {
                    hi = mid - 1;
                }
            }
            return found;
        }

        /** 当前行；没有则空串。 */
        public String lineAt(long positionMs) {
            int idx = indexAt(positionMs);
            return idx < 0 ? "" : lines[idx];
        }

        /** 下一行（界面上做「滚动预告」用）；没有则空串。 */
        public String nextLineAt(long positionMs) {
            int idx = indexAt(positionMs) + 1;
            return idx >= 0 && idx < lines.length ? lines[idx] : "";
        }

        public List<String> allLines() {
            List<String> out = new ArrayList<>(lines.length);
            Collections.addAll(out, lines);
            return out;
        }
    }
}
