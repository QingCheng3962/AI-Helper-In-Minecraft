package com.qingcheng.songmod;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * 点歌 HTTP API 客户端（纯 Java 标准库，<b>不依赖 Minecraft</b>）。
 *
 * <p>对应机器人端 `/api/song/*`（见 docs/点歌API.md）：
 * <ul>
 *   <li>{@code POST /api/song/request} 点歌</li>
 *   <li>{@code GET  /api/song/listeners} 一起听名单</li>
 *   <li>{@code POST /api/song/join} 加入某人的一起听（返回可播放信息）</li>
 * </ul>
 *
 * <p>支持 http / https；Token 通过 {@code X-Token} 头发送。
 */
public final class SongApiClient {

    /**
     * 点歌服务地址（<b>示例地址</b>）：cubexmusic.qingchengsep.top。
     *
     * <p>客户端把这个地址<b>写死</b>，玩家不用也不能在界面里改；
     * 要换成你自己的机器人/网关，请修改这里重新编译 Mod。
     * 也欢迎把你的机器人接入本示例站点（服务端开 song.apiEnabled + Token 即可）。
     */
    public static final String DEFAULT_BASE = "https://cubexmusic.qingchengsep.top";

    /**
     * 内置握手 Token（32 位随机字符）：服务器端 config 里 `song.apiToken` 的默认值就是它。
     * 玩家不用配置；两边要改就一起改（改 Mod 需重新编译）。
     */
    public static final String DEFAULT_TOKEN = "qpDEsVriYMSMGBvV25AYUXXpSJYaxMds";

    private SongApiClient() {
    }

    /** API 调用失败（网络/鉴权/业务错误）。 */
    public static final class ApiException extends Exception {
        public ApiException(String message) {
            super(message);
        }
    }

    /** /api/song/request 返回的播放数据（服务端已查好直链/歌词）。 */
    public static final class Song {
        public final String title;
        public final String artist;
        public final String label;
        public final String url;
        public final String songId;
        public final String lyrics;
        public final long durationMs;
        public final int atSeconds;

        Song(String title, String artist, String label, String url, String songId,
             String lyrics, long durationMs, int atSeconds) {
            this.title = title == null ? "" : title;
            this.artist = artist == null ? "" : artist;
            this.label = label == null ? "" : label;
            this.url = url == null ? "" : url;
            this.songId = songId == null ? "" : songId;
            this.lyrics = lyrics == null ? "" : lyrics;
            this.durationMs = durationMs;
            this.atSeconds = atSeconds;
        }

        public boolean hasUrl() {
            return !url.isEmpty();
        }
    }

    /** /api/song/join 的返回。 */
    public static final class JoinInfo {
        public final String url;
        public final String songId;
        public final String label;
        public final String title;
        public final String artist;
        public final int atSeconds;

        JoinInfo(String url, String songId, String label, String title,
                 String artist, int atSeconds) {
            this.url = url == null ? "" : url;
            this.songId = songId == null ? "" : songId;
            this.label = label == null ? "" : label;
            this.title = title == null ? "" : title;
            this.artist = artist == null ? "" : artist;
            this.atSeconds = atSeconds;
        }

        public boolean hasUrl() {
            return !url.isEmpty();
        }
    }

    /**
     * 点歌：把歌名发给服务端，服务端用**机器人自己的网易云 Cookie**搜索、取直链、取歌词，
     * 再把完整播放数据返回。客户端拿到就能直接播。
     */
    public static Song requestSong(String base, String token, String player,
                                   String query, float speed, int timeoutSeconds)
            throws ApiException {
        String speedKey = Math.abs(speed - 1f) < 0.001f ? "" : SongSpeed.key(speed);
        String body = "{\"player\":" + jsonQuote(player)
                + ",\"query\":" + jsonQuote(query)
                + (speedKey.isEmpty() ? "" : ",\"speed\":" + jsonQuote(speedKey))
                + "}";
        String response = post(base, token, "/api/song/request", body, timeoutSeconds);
        if (!response.contains("\"ok\":true")) {
            String error = jsonString(response, "error");
            throw new ApiException(error == null ? "服务器点歌失败" : error);
        }
        String data = jsonObject(response, "data");
        if (data == null) {
            throw new ApiException("服务器没有返回播放数据");
        }
        return new Song(
                jsonString(data, "title"),
                jsonString(data, "artist"),
                jsonString(data, "label"),
                jsonString(data, "url"),
                jsonString(data, "id"),
                jsonString(data, "lyrics"),
                jsonLong(data, "duration"),
                (int) Math.max(0, jsonLong(data, "at")));
    }

    public static List<SongProtocol.ListenEntry> fetchListeners(String base, String token,
                                                                int timeoutSeconds) {
        List<SongProtocol.ListenEntry> out = new ArrayList<SongProtocol.ListenEntry>();
        String response = get(base, token, "/api/song/listeners", timeoutSeconds);
        String array = jsonArray(response, "listeners");
        if (array == null) {
            return out;
        }
        for (String item : splitObjects(array)) {
            String player = jsonString(item, "player");
            if (player == null || player.isEmpty()) {
                continue;
            }
            String title = jsonString(item, "title");
            String artist = jsonString(item, "artist");
            String label = artist == null || artist.isEmpty()
                    ? (title == null ? "" : title)
                    : (title == null ? artist : title + " - " + artist);
            long elapsed = jsonLong(item, "elapsed");
            out.add(new SongProtocol.ListenEntry(player, label,
                    elapsed < 0 ? -1 : (int) elapsed));
            if (out.size() >= 10) {
                break;
            }
        }
        return out;
    }

    public static JoinInfo joinListen(String base, String token, String player,
                                      String target, int timeoutSeconds) throws ApiException {
        String body = "{\"player\":" + jsonQuote(player)
                + ",\"target\":" + jsonQuote(target) + "}";
        String response = post(base, token, "/api/song/join", body, timeoutSeconds);
        if (!response.contains("\"ok\":true")) {
            String error = jsonString(response, "error");
            throw new ApiException(error == null ? "一起听失败" : error);
        }
        String url = jsonString(response, "url");
        String songId = jsonString(response, "song_id");
        String label = jsonString(response, "label");
        String title = jsonString(response, "title");
        String artist = jsonString(response, "artist");
        long at = jsonLong(response, "at");
        return new JoinInfo(url, songId, label, title, artist, at < 0 ? 0 : (int) at);
    }

    // ------------------------------------------------------------------
    // HTTP
    // ------------------------------------------------------------------
    private static String post(String base, String token, String path, String body,
                               int timeoutSeconds) {
        return call(base, token, path, body, timeoutSeconds);
    }

    private static String get(String base, String token, String path,
                              int timeoutSeconds) {
        return call(base, token, path, null, timeoutSeconds);
    }

    private static String call(String base, String token, String path, String body,
                               int timeoutSeconds) {
        if (base == null || base.trim().isEmpty()) {
            throw new IllegalStateException("没有配置 API 地址");
        }
        HttpURLConnection conn = null;
        try {
            URL url = new URL(base.trim() + path);
            conn = (HttpURLConnection) url.openConnection();
            int timeout = Math.max(3000, Math.min(180000, timeoutSeconds * 1000));
            conn.setConnectTimeout(Math.min(10000, timeout));
            conn.setReadTimeout(timeout);
            conn.setRequestMethod(body == null ? "GET" : "POST");
            conn.setRequestProperty("Accept", "application/json");
            if (token != null && !token.trim().isEmpty()) {
                conn.setRequestProperty("X-Token", token.trim());
            }
            if (body != null) {
                byte[] payload = body.getBytes(StandardCharsets.UTF_8);
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                conn.setFixedLengthStreamingMode(payload.length);
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(payload);
                }
            }
            int code = conn.getResponseCode();
            InputStream stream = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            if (stream == null) {
                throw new IllegalStateException("HTTP " + code);
            }
            String text = readAll(stream);
            if (code >= 400) {
                throw new IllegalStateException("HTTP " + code + ": " + shorten(text, 120));
            }
            return text;
        } catch (IllegalStateException e) {
            throw e;
        } catch (Exception e) {
            throw new IllegalStateException(e.getMessage() == null ? e.toString() : e.getMessage());
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private static String readAll(InputStream in) throws Exception {
        try (InputStream stream = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int n;
            while ((n = stream.read(buffer)) > 0) {
                out.write(buffer, 0, n);
                if (out.size() > (1 << 20)) {
                    break;
                }
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    // ------------------------------------------------------------------
    // 极简 JSON 处理（只为本文件用，避免给 Mod 增加依赖）
    // ------------------------------------------------------------------
    static String jsonQuote(String text) {
        String value = text == null ? "" : text;
        StringBuilder builder = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '\\':
                    builder.append("\\\\");
                    break;
                case '"':
                    builder.append("\\\"");
                    break;
                case '\n':
                    builder.append("\\n");
                    break;
                case '\r':
                    builder.append("\\r");
                    break;
                case '\t':
                    builder.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        builder.append(String.format("\\u%04x", (int) c));
                    } else {
                        builder.append(c);
                    }
            }
        }
        return builder.append('"').toString();
    }

    static String jsonString(String json, String field) {
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
                case 'u':
                    if (i + 4 <= json.length()) {
                        try {
                            out.append((char) Integer.parseInt(json.substring(i, i + 4), 16));
                        } catch (NumberFormatException ignored) {
                            // ignore
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

    static long jsonLong(String json, String field) {
        if (json == null) {
            return -1L;
        }
        String key = "\"" + field + "\"";
        int at = json.indexOf(key);
        if (at < 0) {
            return -1L;
        }
        int colon = json.indexOf(':', at + key.length());
        if (colon < 0) {
            return -1L;
        }
        int i = colon + 1;
        while (i < json.length() && Character.isWhitespace(json.charAt(i))) {
            i++;
        }
        int start = i;
        while (i < json.length() && (Character.isDigit(json.charAt(i)) || json.charAt(i) == '-')) {
            i++;
        }
        try {
            return Long.parseLong(json.substring(start, i));
        } catch (NumberFormatException e) {
            return -1L;
        }
    }

    /** 取对象字段的原文（花括号之间）。 */
    static String jsonObject(String json, String field) {
        if (json == null) {
            return null;
        }
        String key = "\"" + field + "\"";
        int at = json.indexOf(key);
        if (at < 0) {
            return null;
        }
        int start = json.indexOf('{', at);
        if (start < 0) {
            return null;
        }
        int depth = 0;
        boolean inString = false;
        for (int i = start; i < json.length(); i++) {
            char c = json.charAt(i);
            if (inString) {
                if (c == '\\') {
                    i++;
                } else if (c == '"') {
                    inString = false;
                }
                continue;
            }
            if (c == '"') {
                inString = true;
            } else if (c == '{') {
                depth++;
            } else if (c == '}') {
                depth--;
                if (depth == 0) {
                    return json.substring(start, i + 1);
                }
            }
        }
        return null;
    }

    /** 取数组字段的原文（方括号之间）。 */
    static String jsonArray(String json, String field) {
        if (json == null) {
            return null;
        }
        String key = "\"" + field + "\"";
        int at = json.indexOf(key);
        if (at < 0) {
            return null;
        }
        int start = json.indexOf('[', at);
        if (start < 0) {
            return null;
        }
        int depth = 0;
        boolean inString = false;
        for (int i = start; i < json.length(); i++) {
            char c = json.charAt(i);
            if (inString) {
                if (c == '\\') {
                    i++;
                } else if (c == '"') {
                    inString = false;
                }
                continue;
            }
            if (c == '"') {
                inString = true;
            } else if (c == '[') {
                depth++;
            } else if (c == ']') {
                depth--;
                if (depth == 0) {
                    return json.substring(start, i + 1);
                }
            }
        }
        return null;
    }

    /** 把 [{...},{...}] 拆成每个对象原文。 */
    static List<String> splitObjects(String array) {
        List<String> out = new ArrayList<String>();
        if (array == null) {
            return out;
        }
        int depth = 0;
        int start = -1;
        boolean inString = false;
        for (int i = 0; i < array.length(); i++) {
            char c = array.charAt(i);
            if (inString) {
                if (c == '\\') {
                    i++;
                } else if (c == '"') {
                    inString = false;
                }
                continue;
            }
            if (c == '"') {
                inString = true;
            } else if (c == '{') {
                if (depth == 0) {
                    start = i;
                }
                depth++;
            } else if (c == '}') {
                depth--;
                if (depth == 0 && start >= 0) {
                    out.add(array.substring(start, i + 1));
                    start = -1;
                }
            }
        }
        return out;
    }

    private static String shorten(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "…";
    }
}
