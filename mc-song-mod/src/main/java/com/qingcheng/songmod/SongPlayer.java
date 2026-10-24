package com.qingcheng.songmod;

import javazoom.jl.player.Player;

import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.function.Consumer;

/**
 * 流式播放一首歌：边下边放（JLayer 解码 MP3 → {@link VolumeAudioDevice} → 系统音频）。
 * 同时只放一首；换歌会先停掉上一首。
 *
 * <p>注意：网易云的直链会 302 跳到<b>明文 http</b> 的 CDN，而 JDK 的
 * {@link HttpURLConnection} <b>不会</b>跟随 https→http 的跳转（只会拿到 302 空响应）。
 * 所以这里手动逐跳解析 {@code Location}，再打开最终地址。
 */
public final class SongPlayer {

    private static final String UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";

    private static volatile Player player;
    private static long generation;

    private SongPlayer() {
    }

    public static synchronized void play(String audioUrl, float volume, Consumer<String> onError) {
        stop();
        final long request = generation;
        Thread thread = new Thread(() -> {
            Player owned = null;
            try {
                URL finalUrl = resolveFinalUrl(audioUrl);
                synchronized (SongPlayer.class) {
                    if (request != generation) {
                        return;
                    }
                }
                try (InputStream in = openStream(finalUrl)) {
                    owned = new Player(in, new VolumeAudioDevice(volume));
                    synchronized (SongPlayer.class) {
                        if (request != generation) {
                            owned.close();
                            return;
                        }
                        player = owned;
                    }
                    owned.play();
                }
            } catch (Throwable e) {
                boolean report;
                synchronized (SongPlayer.class) {
                    report = request == generation;
                }
                if (report && onError != null) {
                    onError.accept(e.getMessage() == null ? e.toString() : e.getMessage());
                }
            } finally {
                synchronized (SongPlayer.class) {
                    if (request == generation) {
                        player = null;
                    }
                }
                if (owned != null) {
                    owned.close();
                }
            }
        }, "songmod-player");
        thread.setDaemon(true);
        thread.start();
    }

    /** 手动跟随 3xx 跳转（HTTP 和 HTTPS 都跟），返回最终地址。 */
    private static URL resolveFinalUrl(String url) throws IOException {
        URL current = new URL(url);
        for (int i = 0; i < 6; i++) {
            HttpURLConnection conn = (HttpURLConnection) current.openConnection();
            conn.setInstanceFollowRedirects(false);
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(15000);
            conn.setRequestProperty("User-Agent", UA);
            conn.setRequestProperty("Referer", "https://music.163.com/");
            int code = conn.getResponseCode();
            if (code >= 300 && code < 400) {
                String location = conn.getHeaderField("Location");
                conn.disconnect();
                if (location == null || location.isEmpty()) {
                    break;
                }
                current = new URL(current, location);
                continue;
            }
            conn.disconnect();
            break;
        }
        return current;
    }

    private static InputStream openStream(URL url) throws IOException {
        try {
            return doOpen(url);
        } catch (IOException primary) {
            if ("http".equalsIgnoreCase(url.getProtocol())) {
                // 有的 CDN 也支持 https，http 打不开时回退 https 再试一次。
                try {
                    URL https = new URL("https", url.getHost(), url.getPort(), url.getFile());
                    return doOpen(https);
                } catch (IOException ignored) {
                    throw primary;
                }
            }
            throw primary;
        }
    }

    private static InputStream doOpen(URL url) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setInstanceFollowRedirects(true);
        conn.setConnectTimeout(8000);
        conn.setReadTimeout(15000);
        conn.setRequestProperty("User-Agent", UA);
        conn.setRequestProperty("Referer", "https://music.163.com/");
        conn.setRequestProperty("Accept", "*/*");
        int code = conn.getResponseCode();
        if (code >= 400) {
            conn.disconnect();
            throw new IOException("HTTP " + code + "（" + url.getHost() + "）");
        }
        return new BufferedInputStream(conn.getInputStream(), 1 << 16);
    }

    public static synchronized void stop() {
        generation++;
        Player p = player;
        if (p != null) {
            try {
                p.close();
            } catch (Exception ignored) {
                // ignore
            }
        }
        player = null;
    }

    public static boolean isPlaying() {
        return player != null;
    }
}
