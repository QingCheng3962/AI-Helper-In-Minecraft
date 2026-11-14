package com.qingcheng.songmod;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Mod 配置，保存在 {@code config/songmod.json}。
 *
 * <p>新版本加了「倍速 / 歌词 / 一起听」相关字段，老配置文件缺失的字段会自动用默认值补上
 * （Gson 反序列化只覆盖文件里出现的字段），不会因为升级丢配置。
 */
public final class SongConfig {

    /** 触发词：聊天里出现它，后面的文字被当作歌名。 */
    public String trigger = "[点歌]";
    /** 停止词：写成 {@code [点歌]停止} 即停止播放。 */
    public String stopWord = "停止";
    /** 登录词：{@code [点歌]登录} 会提示去机器人的「点歌」页登录。 */
    public String loginWord = "登录";
    /** 登出词：{@code [点歌]登出}。 */
    public String logoutWord = "登出";
    /** 打开「点歌台」界面的聊天词：发 {@code [点歌]台} 也能打开（按键打不开时的备用）。 */
    public String guiWord = "台";
    /** 音量 0~1。 */
    public float volume = 0.8f;
    /** 上次用的倍速（0.5 / 0.75 / 1.0 / 1.5 / 2.0）。 */
    public float speed = 1.0f;
    /** 音源（目前仅 163 网易云）。 */
    public String provider = "163";
    /**
     * 自己的网易云 Cookie（含 {@code MUSIC_U}）。本地兜底搜索 / 歌词接口用；
     * 有会员 Cookie 就能放 VIP 完整版。可在 Mod 的「点歌台」界面里填。
     */
    public String neteaseCookie = "";
    /** 机器人不在线时，是否本地兜底自己搜歌播放（默认开；有机器人在线时优先用机器人）。 */
    public boolean localFallback = true;
    /**
     * 只播「给我的」歌（默认开 = **谁点谁听**）：机器人把播放数据私聊发过来，
     * 文案里带我的游戏名、或我最近点过歌时才播。
     */
    public boolean onlyMySong = true;
    /**
     * 机器人游戏名。设置后只接受这个发送者发来的播放数据；留空时退回
     * 「我最近一次点歌的关键词」匹配。
     */
    public String botName = "";
    /** 点歌 Mod 下载链接（播放成功后提示用）；留空则不提示。 */
    public String modUrl = "";
    /** 播放成功后的提示文案，{url} 会替换成 modUrl。 */
    public String hintText = "提示：没装点歌 Mod 的人听不到，请前往 {url} 下载";
    /** 歌名最大长度（防止超长）。 */
    public int maxKeywordLength = 60;

    // ------------------------------------------------------------------
    // 新功能：歌词 / 一起听 / 界面
    // ------------------------------------------------------------------

    /** 是否显示歌词（默认开）。 */
    public boolean showLyrics = true;
    /**
     * 歌词显示方式：
     * <ul>
     *   <li>{@code action} —— 屏幕下方动作条（默认，最不打扰）；</li>
     *   <li>{@code title} —— 屏幕中央大字（带淡入淡出）；</li>
     *   <li>{@code chat} —— 聊天栏；</li>
     *   <li>{@code off} —— 不显示（等同 showLyrics=false）。</li>
     * </ul>
     */
    public String lyricMode = "action";
    /** 歌词前缀（可空；例如填 "♪ " 会显示成「♪ 歌词」）。 */
    public String lyricPrefix = "";
    /** title 模式的淡入毫秒。 */
    public int lyricTitleFadeIn = 150;
    /** title 模式的停留毫秒。 */
    public int lyricTitleStay = 1200;
    /** title 模式的淡出毫秒。 */
    public int lyricTitleFadeOut = 150;

    /** 是否启用「一起听」（界面上的页签与聊天指令）。 */
    public boolean listenTogetherEnabled = true;

    // ------------------------------------------------------------------
    // v0.3.6-26.11.14：点歌通道 / 连接模式
    // ------------------------------------------------------------------

    /**
     * 使用模式：
     * <ul>
     *   <li>{@code server} —— 连服务器/机器人：点歌、一起听都走机器人（默认）；</li>
     *   <li>{@code solo} —— 自用：完全不与服务器关联，本机搜歌播放，不发任何聊天、不请求机器人。</li>
     * </ul>
     */
    public String mode = "server";
    /** 机器人 HTTP API 基地址。客户端已写死为 SongApiClient.DEFAULT_BASE（示例地址），
     *  这里只作为显示/兼容字段，实际请求地址见 SongApiClient.DEFAULT_BASE。 */
    public String apiBase = SongApiClient.DEFAULT_BASE;
    /** 机器人 HTTP API 的 Token（= 机器人 config.json 里的 qq.webApiToken）。 */
    public String apiToken = "";
    /** 是否走 HTTP API（点歌/一起听名单/加入）；关掉才退回聊天（一般保持开启）。 */
    public boolean apiEnabled = true;
    /** 机器人超时（秒）：等聊天直链、HTTP API 请求都用它。 */
    public int botTimeoutSeconds = 30;
    /** 一起听名单自动刷新间隔（秒，0=只在打开界面时刷新）。 */
    public int listenRefreshSeconds = 15;

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    public static SongConfig load() {
        Path path = FabricLoader.getInstance().getConfigDir().resolve("songmod.json");
        SongConfig cfg = new SongConfig();
        if (Files.exists(path)) {
            try {
                SongConfig loaded = GSON.fromJson(Files.readString(path, StandardCharsets.UTF_8), SongConfig.class);
                if (loaded != null) {
                    cfg = loaded;
                }
            } catch (Exception ignored) {
                // keep defaults
            }
        }
        cfg.validate();
        cfg.save(path);
        return cfg;
    }

    /** 把越界/空值修正回合法范围（手改配置文件写坏了也不会崩）。 */
    public void validate() {
        if (trigger == null || trigger.isEmpty()) {
            trigger = "[点歌]";
        }
        if (stopWord == null || stopWord.trim().isEmpty()) {
            stopWord = "停止";
        }
        if (loginWord == null || loginWord.trim().isEmpty()) {
            loginWord = "登录";
        }
        if (logoutWord == null || logoutWord.trim().isEmpty()) {
            logoutWord = "登出";
        }
        if (guiWord == null || guiWord.trim().isEmpty()) {
            guiWord = "台";
        }
        if (provider == null || provider.isEmpty()) {
            provider = "163";
        }
        if (neteaseCookie == null) {
            neteaseCookie = "";
        }
        if (botName == null) {
            botName = "";
        }
        if (modUrl == null) {
            modUrl = "";
        }
        if (hintText == null || hintText.trim().isEmpty()) {
            hintText = "提示：没装点歌 Mod 的人听不到，请前往 {url} 下载";
        }
        volume = Math.max(0f, Math.min(1f, volume));
        speed = SongSpeed.normalize(speed);
        if (maxKeywordLength < 1 || maxKeywordLength > 200) {
            maxKeywordLength = 60;
        }
        if (lyricMode == null) {
            lyricMode = "action";
        }
        lyricMode = lyricMode.trim().toLowerCase();
        if (!lyricMode.equals("action") && !lyricMode.equals("title")
                && !lyricMode.equals("chat") && !lyricMode.equals("off")) {
            lyricMode = "action";
        }
        if (lyricPrefix == null) {
            lyricPrefix = "";
        }
        lyricTitleFadeIn = clamp(lyricTitleFadeIn, 0, 2000);
        lyricTitleStay = clamp(lyricTitleStay, 100, 10000);
        lyricTitleFadeOut = clamp(lyricTitleFadeOut, 0, 2000);
        listenRefreshSeconds = clamp(listenRefreshSeconds, 0, 600);
        mode = "solo".equalsIgnoreCase(String.valueOf(mode)) ? "solo" : "server";
        apiBase = apiBase == null ? "" : apiBase.trim();
        while (apiBase.endsWith("/")) {
            apiBase = apiBase.substring(0, apiBase.length() - 1);
        }
        apiToken = apiToken == null ? "" : apiToken.trim();
        botTimeoutSeconds = clamp(botTimeoutSeconds, 5, 180);
    }

    private static int clamp(int value, int lo, int hi) {
        return Math.max(lo, Math.min(hi, value));
    }

    private void save(Path path) {
        try {
            Files.createDirectories(path.getParent());
            Files.writeString(path, GSON.toJson(this), StandardCharsets.UTF_8);
        } catch (IOException ignored) {
            // ignore
        }
    }

    /** 保存到默认配置路径（例如扫码登录成功后写回 Cookie）。 */
    public void save() {
        save(FabricLoader.getInstance().getConfigDir().resolve("songmod.json"));
    }
}
