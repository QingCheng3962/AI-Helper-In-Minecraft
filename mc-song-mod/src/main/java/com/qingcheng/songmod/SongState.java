package com.qingcheng.songmod;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 客户端共享状态（给界面读的「当前在放什么、歌词第几句、一起听有哪些人」）。
 *
 * <p>全部是 {@code volatile} 简单字段：播放线程/网络线程写，游戏渲染线程读，
 * 不需要锁，也不会因为加锁把渲染卡住。<b>不依赖 Minecraft 类</b>。
 */
public final class SongState {

    /** 正在播放的歌（展示用标签，例如「起风了 - 冯沁苑」）。 */
    public static volatile String title = "";
    /** 正在播放的网易云歌曲 id（歌词、一起听要用）。 */
    public static volatile String songId = "";
    /** 这次播放是不是「一起听」来的（界面上标一下）。 */
    public static volatile String fromPlayer = "";

    /** 歌词整体状态（没歌词/加载中/加载失败的原因）。 */
    public static volatile String lyricsStatus = "";
    /** 当前歌词行。 */
    public static volatile String lyricLine = "";
    /** 下一句歌词（界面做预告）。 */
    public static volatile String nextLyricLine = "";

    /** 我点过的歌（最近 50 条，界面上倒序显示）。 */
    private static final List<String> MY_REQUESTS = Collections.synchronizedList(new ArrayList<String>());

    /** 一起听：服务器上正在听歌的玩家。 */
    public static volatile List<SongProtocol.ListenEntry> listeners =
            Collections.emptyList();
    /** 上次刷新一起听名单的时间（毫秒时间戳）。 */
    public static volatile long listenersAt = 0L;
    /** 一起听名单的提示（「正在刷新…」/「没有人在听歌」）。 */
    public static volatile String listenersStatus = "";

    private SongState() {
    }

    /** 换歌时清掉上一首的歌词。 */
    public static void beginPlayback(String label, String id, String from) {
        title = label == null ? "" : label;
        songId = id == null ? "" : id;
        fromPlayer = from == null ? "" : from;
        lyricsStatus = "";
        lyricLine = "";
        nextLyricLine = "";
    }

    public static void setLyrics(SongLyrics.Lyrics lyrics) {
        if (lyrics == null || lyrics.isEmpty()) {
            lyricLine = "";
            nextLyricLine = "";
            return;
        }
        // 只把时间轴存起来；当前行在 tick 里按播放进度算
        LYRICS = lyrics;
    }

    /** 当前歌词时间轴（可能为 null）。 */
    public static volatile SongLyrics.Lyrics LYRICS;

    /** 按播放进度刷新当前/下一句歌词。 */
    public static void syncLyrics(long positionMs) {
        SongLyrics.Lyrics lyrics = LYRICS;
        if (lyrics == null || lyrics.isEmpty()) {
            lyricLine = "";
            nextLyricLine = "";
            return;
        }
        lyricLine = lyrics.lineAt(positionMs);
        nextLyricLine = lyrics.nextLineAt(positionMs);
    }

    public static void addMyRequest(String query) {
        if (query == null || query.trim().isEmpty()) {
            return;
        }
        MY_REQUESTS.add(query.trim());
        while (MY_REQUESTS.size() > 50) {
            MY_REQUESTS.remove(0);
        }
    }

    /** 我点过的歌（新→旧）。 */
    public static List<String> myRequests() {
        synchronized (MY_REQUESTS) {
            List<String> out = new ArrayList<>(MY_REQUESTS);
            Collections.reverse(out);
            return out;
        }
    }

    public static void clearMyRequests() {
        MY_REQUESTS.clear();
    }

    public static void setListeners(List<SongProtocol.ListenEntry> entries) {
        listeners = entries == null ? Collections.<SongProtocol.ListenEntry>emptyList() : entries;
        listenersAt = System.currentTimeMillis();
    }
}
