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
    /** 音源（目前仅 163 网易云）。 */
    public String provider = "163";
    /**
     * 自己的网易云 Cookie（含 {@code MUSIC_U}）。机器人不在线时本地兜底要用；
     * 有会员 Cookie 就能放 VIP 完整版。可在 Mod 的「点歌台」界面里填。
     */
    public String neteaseCookie = "";
    /** 机器人不在线时，是否本地兜底自己搜歌播放（默认开；有机器人在线时优先用机器人）。 */
    public boolean localFallback = true;
    /**
     * 只播「给我的」歌（默认开 = **谁点谁听**）：机器人把播放数据私聊发过来，
     * 文案里带我的游戏名、或我最近点过歌时才播。
     * 关掉 = 老行为：公屏上任何人的播放数据我都跟着听（全服广播式）。
     */
    public boolean onlyMySong = true;
    /**
     * 机器人游戏名。设置后只接受这个发送者发来的播放数据；留空时退回
     * 「我最近一次点歌的关键词」匹配（不再只看正文里的名字）。
     */
    public String botName = "";
    /** 点歌 Mod 下载链接（播放成功后提示用）；留空则不提示。 */
    public String modUrl = "";
    /** 播放成功后的提示文案，{url} 会替换成 modUrl。 */
    public String hintText = "提示：没装点歌 Mod 的人听不到，请前往 {url} 下载";
    /** 歌名最大长度（防止超长）。 */
    public int maxKeywordLength = 60;

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
        if (cfg.trigger == null || cfg.trigger.isEmpty()) {
            cfg.trigger = "[点歌]";
        }
        if (cfg.stopWord == null || cfg.stopWord.trim().isEmpty()) {
            cfg.stopWord = "停止";
        }
        if (cfg.loginWord == null || cfg.loginWord.trim().isEmpty()) {
            cfg.loginWord = "登录";
        }
        if (cfg.logoutWord == null || cfg.logoutWord.trim().isEmpty()) {
            cfg.logoutWord = "登出";
        }
        if (cfg.guiWord == null || cfg.guiWord.trim().isEmpty()) {
            cfg.guiWord = "台";
        }
        if (cfg.provider == null || cfg.provider.isEmpty()) {
            cfg.provider = "163";
        }
        if (cfg.neteaseCookie == null) {
            cfg.neteaseCookie = "";
        }
        if (cfg.botName == null) {
            cfg.botName = "";
        }
        if (cfg.modUrl == null) {
            cfg.modUrl = "";
        }
        if (cfg.hintText == null || cfg.hintText.trim().isEmpty()) {
            cfg.hintText = "提示：没装点歌 Mod 的人听不到，请前往 {url} 下载";
        }
        cfg.volume = Math.max(0f, Math.min(1f, cfg.volume));
        if (cfg.maxKeywordLength < 1 || cfg.maxKeywordLength > 200) {
            cfg.maxKeywordLength = 60;
        }
        cfg.save(path);
        return cfg;
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
