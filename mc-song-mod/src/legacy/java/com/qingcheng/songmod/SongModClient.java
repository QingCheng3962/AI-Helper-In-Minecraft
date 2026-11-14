package com.qingcheng.songmod;

import com.mojang.blaze3d.platform.InputConstants;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keybinding.v1.KeyBindingHelper;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 点歌 Mod（客户端，1.21.x）。
 *
 * <p>职责：
 * <ol>
 *   <li>听聊天里的 {@code [点歌]…}：自己发的（点歌 / 一起听 / 台 / 停止 / 歌词 / 倍速）和
 *       机器人发来的（播放数据 / 一起听名单）；</li>
 *   <li>把播放数据交给 {@link SongPlayer}（整首下载 → 可拖动、可倍速、可暂停）；</li>
 *   <li>按 {@link SongConfig#lyricMode} 把歌词同步显示到动作条 / 标题 / 聊天栏；</li>
 *   <li>游戏内界面 {@link SongScreen}（按键 K 或 {@code [点歌]台} 打开）。</li>
 * </ol>
 */
public class SongModClient implements ClientModInitializer {

    /** 按键注册名（对应 lang 里的 key.songmod.open）。 */
    private static final String KEY_NAME = "key.songmod.open";

    /** 等机器人响应的超时（可配置：SongConfig.botTimeoutSeconds）；等不到才本地兜底。 */
    private static long botTimeoutMs() {
        int seconds = config == null ? 30 : config.botTimeoutSeconds;
        return Math.max(5, Math.min(180, seconds)) * 1000L;
    }

    /** 等机器人回直链 / 一段时间内接受「给我的」播放数据。 */
    private static final long PENDING_WINDOW_MS = 60000L;

    private static final ScheduledExecutorService SCHED = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "songmod-wait");
        t.setDaemon(true);
        return t;
    });

    private static final ExecutorService POOL = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "songmod-net");
        t.setDaemon(true);
        return t;
    });

    private static final AtomicInteger PENDING = new AtomicInteger(0);

    static volatile SongConfig config;

    private static volatile KeyMapping openKey;
    private static volatile long lastKeyAt = 0L;
    private static volatile String lastKeyText = "";
    /** 我最近一次点歌的时间 / 关键词（「谁点谁听」用）。 */
    private static volatile long lastMyRequestAt = 0L;
    private static volatile String lastMyQuery = "";
    private static volatile boolean stoppedByUser = false;
    /** 正在等「一起听」的回复（此时机器人回的任何播放数据都算给我的）。 */
    private static volatile boolean expectingListenReply = false;
    private static volatile long expectingListenAt = 0L;
    /** 正在等一起听名单。 */
    private static volatile boolean expectingListReply = false;
    /** 上一次刷一起听名单 / 加入一起听的时间（客户端侧冷却，防连点刷屏）。 */
    private static volatile long lastListenRefreshAt = 0L;
    private static volatile long lastListenJoinAt = 0L;

    /** 歌词显示节流。 */
    private static volatile String lastShownLyric = "";
    private static volatile long lastLyricShownAt = 0L;
    private static volatile String lastTitleLyric = "";

    @Override
    public void onInitializeClient() {
        config = SongConfig.load();
        SongPlayer.setVolume(config.volume);
        SongPlayer.setSpeed(config.speed);
        System.out.println("[songmod] 版本 v0.2.0+gui2-20261114（新界面：进度条/倍速/一起听/歌词）");

        ClientSendMessageEvents.CHAT.register(text -> onChat(text, true, ""));
        // 服务器发来的聊天一律 mine=false（我自己的指令只看上面那个本地发送事件）
        ClientReceiveMessageEvents.CHAT.register((message, signedMessage, sender, params, timestamp) ->
                onChat(message == null ? "" : message.getString(), false, senderName(sender)));
        // 很多服务器把玩家聊天改格式后当「系统消息」重播：只走 GAME、不走 CHAT
        ClientReceiveMessageEvents.GAME.register((message, overlay) ->
                onChat(message == null ? "" : message.getString(), false, ""));

        // 客户端命令：被机器人可点击消息调用（`/songmod join <玩家>`），
        // 走 HTTPS 网关 API 加入一起听，不往服务器发任何聊天。
        try {
            net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback.EVENT.register(
                    (dispatcher, registryAccess) -> dispatcher.register(
                            net.fabricmc.fabric.api.client.command.v2.ClientCommandManager.literal("songmod")
                                    .then(net.fabricmc.fabric.api.client.command.v2.ClientCommandManager
                                            .literal("join")
                                            .then(net.fabricmc.fabric.api.client.command.v2.ClientCommandManager
                                                    .argument("player", com.mojang.brigadier.arguments.StringArgumentType.word())
                                                    .executes(ctx -> {
                                                        String target = com.mojang.brigadier.arguments.StringArgumentType
                                                                .getString(ctx, "player");
                                                        float speed = config == null ? 1f : config.speed;
                                                        requestJoin(target, speed);
                                                        return 1;
                                                    })))));
        } catch (Throwable t) {
            System.out.println("[songmod] 注册客户端命令失败（忽略）：" + t);
        }

        openKey = createKeyMapping();
        if (openKey != null) {
            KeyBindingHelper.registerKeyBinding(openKey);
            System.out.println("[songmod] 已注册按键 key.songmod.open（默认 K），可在「按键设置」里改");
        } else {
            System.out.println("[songmod] 按键注册失败：可在聊天里发 " + config.trigger + config.guiWord + " 打开界面");
        }

        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            try {
                if (openKey != null) {
                    while (openKey.consumeClick()) {
                        openScreen();
                    }
                }
                tickLyrics(client);
            } catch (Throwable t) {
                // 任何异常都不能漏进事件回调
                System.out.println("[songmod] tick 出错（已忽略）：" + t);
            }
        });
    }

    // ------------------------------------------------------------------
    // 按键（版本兼容靠反射：1.21.9+ 的 KeyMapping 构造器第 4 参换成了 Category）
    // ------------------------------------------------------------------

    private static KeyMapping createKeyMapping() {
        try {
            for (java.lang.reflect.Constructor<?> c : KeyMapping.class.getDeclaredConstructors()) {
                Class<?>[] p = c.getParameterTypes();
                if (p.length != 4 || p[0] != String.class || p[1] != InputConstants.Type.class
                        || p[2] != int.class) {
                    continue;
                }
                Object fourth;
                if (p[3] == String.class) {
                    fourth = "key.categories.songmod";
                } else {
                    fourth = firstStaticInstance(p[3]);
                }
                if (fourth == null) {
                    continue;
                }
                c.setAccessible(true);
                // 75 = GLFW_KEY_K；不直接引 org.lwjgl.glfw.GLFW，少一个编译期依赖。
                return (KeyMapping) c.newInstance(KEY_NAME,
                        InputConstants.Type.KEYSYM, 75, fourth);
            }
        } catch (Throwable t) {
            System.out.println("[songmod] 注册按键失败：" + t);
        }
        return null;
    }

    private static Object firstStaticInstance(Class<?> type) {
        try {
            for (java.lang.reflect.Field f : type.getFields()) {
                if (java.lang.reflect.Modifier.isStatic(f.getModifiers()) && f.getType() == type) {
                    Object v = f.get(null);
                    if (v != null) {
                        return v;
                    }
                }
            }
        } catch (Throwable ignored) {
            // ignore
        }
        return null;
    }

    public static void openScreen() {
        Minecraft mc = Minecraft.getInstance();
        if (mc == null) {
            return;
        }
        mc.execute(() -> {
            try {
                mc.setScreen(new SongScreen());
            } catch (Throwable ignored) {
                // ignore
            }
        });
    }

    // ------------------------------------------------------------------
    // 给界面用的接口
    // ------------------------------------------------------------------

    public static SongConfig settings() {
        return config;
    }

    public static String trigger() {
        return config == null || config.trigger == null || config.trigger.isEmpty()
                ? "[点歌]" : config.trigger;
    }

    public static String stopWord() {
        return config == null ? "停止" : config.stopWord;
    }

    public static String openKeyName() {
        try {
            return openKey == null ? "K" : openKey.getTranslatedKeyMessage().getString();
        } catch (Throwable t) {
            return "K";
        }
    }

    /** 当前玩家名（只在主线程调用）。 */
    private static String myName() {
        try {
            Minecraft mc = Minecraft.getInstance();
            return (mc != null && mc.player != null)
                    ? mc.player.getName().getString() : "";
        } catch (Throwable t) {
            return "";
        }
    }

    /** 自用模式：完全不连服务器（不发聊天、不请求机器人）。 */
    public static boolean modeSolo() {
        return config != null && "solo".equals(config.mode);
    }

    /** 是否启用 HTTP API（地址非空才算）。 */
    public static boolean apiEnabled() {
        return config != null && config.apiEnabled
                && config.apiBase != null && !config.apiBase.trim().isEmpty();
    }

    public static String modeLabel() {
        return modeSolo() ? "自用" : "服务器";
    }

    /** 切换「服务器 / 自用」模式。 */
    public static void toggleMode() {
        if (config == null) {
            return;
        }
        config.mode = modeSolo() ? "server" : "solo";
        config.save();
        if (modeSolo()) {
            SongState.listenersStatus = "自用模式：不连接服务器";
        }
        feedback("§b[点歌] §a模式：" + (modeSolo()
                ? "自用（本机搜歌，不发聊天、不连服务器）"
                : "服务器（点歌/一起听走机器人）"));
    }

    public static void setApiEnabled(boolean enabled) {
        if (config != null) {
            config.apiEnabled = enabled;
            config.save();
            feedback("§b[点歌] §aHTTP API：" + (enabled ? "开" : "关（只用聊天）"));
        }
    }

    public static void setApiBase(String base) {
        if (config != null) {
            config.apiBase = base == null ? "" : base.trim();
            config.save();
        }
    }

    public static void setApiToken(String token) {
        if (config != null) {
            config.apiToken = token == null ? "" : token.trim();
            config.save();
        }
    }

    public static void setBotTimeout(int seconds) {
        if (config != null) {
            config.botTimeoutSeconds = Math.max(5, Math.min(180, seconds));
            config.save();
        }
    }

    public static String neteaseCookie() {
        return (config == null || config.neteaseCookie == null) ? "" : config.neteaseCookie;
    }

    public static void setNeteaseCookie(String cookie) {
        if (config != null) {
            config.neteaseCookie = cookie == null ? "" : cookie.trim();
            config.save();
        }
    }

    /** 以玩家身份发一条聊天。 */
    public static void sendChat(String text) {
        if (text == null || text.isEmpty()) {
            return;
        }
        Minecraft mc = Minecraft.getInstance();
        if (mc != null && mc.player != null) {
            mc.player.connection.sendChat(text);
        }
    }

    /** 界面点歌：自用=本地播放；开 API=走 HTTP；否则聊天（带当前倍速）。 */
    public static void requestSong(String query) {
        String q = query == null ? "" : query.trim();
        if (q.isEmpty()) {
            return;
        }
        float speed = config == null ? 1f : config.speed;
        String note = Math.abs(speed - 1f) < 0.001f ? "" : "  [" + SongSpeed.label(speed) + "]";
        if (modeSolo()) {
            stoppedByUser = false;
            int seq = PENDING.incrementAndGet();
            SongState.addMyRequest(q + note);
            feedback("§b[点歌] §7自用模式：本机搜索播放 §f" + q);
            searchAndPlay(q, seq);
            return;
        }
        if (apiEnabled()) {
            final String base = config.apiBase;
            final String token = config.apiToken;
            final int timeout = config.botTimeoutSeconds;
            final String playerName = myName();
            final String apiQuery = q;
            final float apiSpeed = speed;
            final int seq = PENDING.incrementAndGet();
            SongState.addMyRequest(q + note);
            feedback("§b[点歌] §7正在通过 API 点歌：§f" + q);
            POOL.submit(() -> {
                try {
                    boolean ok = SongApiClient.requestSong(base, token, playerName,
                            apiQuery, apiSpeed, timeout);
                    if (ok) {
                        synchronized (SongModClient.class) {
                            markMyRequest(apiQuery);
                        }
                        feedbackIfCurrent(seq, "§b[点歌] §a已通过 API 点歌：§f" + apiQuery + note);
                        return;
                    }
                } catch (Throwable t) {
                    System.out.println("[songmod] API 点歌失败，回退聊天：" + t);
                }
                if (!isCurrent(seq)) {
                    return;
                }
                synchronized (SongModClient.class) {
                    if (!isCurrent(seq)) {
                        return;
                    }
                    feedbackIfCurrent(seq, "§b[点歌] §7API 不可用，改用聊天点歌…");
                    chatRequestSong(apiQuery, apiSpeed, note);
                }
            });
            return;
        }
        chatRequestSong(q, speed, note);
    }

    /** 老的聊天点歌流程（等机器人直链，超时本地兜底）。 */
    private static void chatRequestSong(String query, float speed, String note) {
        String suffix = Math.abs(speed - 1f) < 0.001f ? "" : " " + SongSpeed.key(speed);
        sendChat(trigger() + query + suffix);
        SongState.addMyRequest(query + note);
        markMyRequest(query);
        stoppedByUser = false;
        feedback("§b[点歌] §7正在点歌：§f" + query
                + (suffix.isEmpty() ? "" : "  §b" + SongSpeed.label(speed)));
        final String searchQuery = query;
        final int seq = PENDING.incrementAndGet();
        SCHED.schedule(() -> {
            synchronized (SongModClient.class) {
                if (!isCurrent(seq)) {
                    return;
                }
                if (config.localFallback) {
                    feedbackIfCurrent(seq, "§b[点歌] §7机器人没响应，本地兜底搜索…");
                    searchAndPlay(searchQuery, seq);
                } else {
                    feedbackIfCurrent(seq, "§b[点歌] §c机器人不在线（或没登录网易云），点歌失败。");
                }
            }
        }, botTimeoutMs(), TimeUnit.MILLISECONDS);
    }

    /** 界面停止。 */
    public static void requestStop() {
        sendChat(trigger() + stopWord());
    }

    /** 界面刷新一起听名单：自用=不连；API=HTTP；否则聊天（5 秒冷却）。 */
    public static void requestListenList() {
        if (config == null || !config.listenTogetherEnabled) {
            return;
        }
        if (modeSolo()) {
            SongState.listenersStatus = "自用模式：不连接服务器";
            return;
        }
        long now = System.currentTimeMillis();
        if (now - lastListenRefreshAt < 5000L) {
            return;                     // 冷却中：静默忽略，不重复发消息
        }
        lastListenRefreshAt = now;
        SongState.listenersStatus = "正在刷新…";
        if (apiEnabled()) {
            final String base = config.apiBase;
            final String token = config.apiToken;
            final int timeout = config.botTimeoutSeconds;
            POOL.submit(() -> {
                try {
                    List<SongProtocol.ListenEntry> list =
                            SongApiClient.fetchListeners(base, token, timeout);
                    synchronized (SongModClient.class) {
                        SongState.setListeners(list);
                        SongState.listenersStatus = list.isEmpty() ? "现在没有人在听歌" : "";
                    }
                } catch (Throwable t) {
                    System.out.println("[songmod] API 名单失败，回退聊天：" + t);
                    synchronized (SongModClient.class) {
                        chatListenList();
                    }
                }
            });
            return;
        }
        chatListenList();
    }

    private static void chatListenList() {
        expectingListReply = true;
        SongState.listenersStatus = "正在刷新…";
        sendChat(trigger() + SongProtocol.LISTEN_WORD);
    }

    /** 界面加入一起听：自用=不连；API=HTTP 拿播放信息；否则聊天（5 秒冷却）。 */
    public static void requestJoin(String player, float speed) {
        String name = player == null ? "" : player.trim();
        if (name.isEmpty()) {
            return;
        }
        if (modeSolo()) {
            SongState.listenersStatus = "自用模式：不连接服务器";
            return;
        }
        long nowJoin = System.currentTimeMillis();
        if (nowJoin - lastListenJoinAt < 5000L) {
            return;
        }
        lastListenJoinAt = nowJoin;
        if (apiEnabled()) {
            final String base = config.apiBase;
            final String token = config.apiToken;
            final int timeout = config.botTimeoutSeconds;
            final String playerName = myName();
            final String target = name;
            final float joinSpeed = speed;
            SongState.listenersStatus = "正在加入 " + target + " 的一起听…";
            POOL.submit(() -> {
                SongApiClient.JoinInfo info = null;
                try {
                    info = SongApiClient.joinListen(base, token, playerName, target, timeout);
                } catch (Throwable t) {
                    System.out.println("[songmod] API 一起听失败，回退聊天：" + t);
                }
                final SongApiClient.JoinInfo joinInfo = info;
                Minecraft mc = Minecraft.getInstance();
                if (mc == null) {
                    return;
                }
                mc.execute(() -> {
                    synchronized (SongModClient.class) {
                        if (joinInfo != null && joinInfo.hasUrl()) {
                            playApiJoin(joinInfo, joinSpeed);
                        } else {
                            SongState.listenersStatus = "API 不可用，改用聊天一起听…";
                            chatJoin(target, joinSpeed);
                        }
                    }
                });
            });
            return;
        }
        chatJoin(name, speed);
    }

    /** 用 API 返回的播放信息直接起播（不走聊天）。 */
    private static void playApiJoin(SongApiClient.JoinInfo info, float speed) {
        int seq = PENDING.incrementAndGet();
        long startMs = Math.max(0, info.atSeconds) * 1000L;
        SongState.beginPlayback(info.label, info.songId, "一起听");
        SongPlayer.setSpeed(config.speed);
        feedbackIfCurrent(seq, "§b[点歌] §a正在播放：§f" + info.label
                + (info.atSeconds > 0 ? " §7（一起听 " + formatTime(startMs) + " 处加入）" : ""));
        SongPlayer.play(info.url, config.volume, startMs,
                err -> feedbackIfCurrent(seq, "§b[点歌] §c播放失败：" + err));
        loadLyrics(info.songId);
        hintIfConfigured(seq);
    }

    private static void chatJoin(String name, float speed) {
        String suffix = speed > 0 && Math.abs(speed - 1f) >= 0.001f
                ? " " + SongSpeed.key(speed) : "";
        expectingListenReply = true;
        expectingListenAt = System.currentTimeMillis();
        markMyRequest(SongProtocol.LISTEN_WORD + " " + name);
        SongState.listenersStatus = "正在加入 " + name + " 的一起听…";
        sendChat(trigger() + SongProtocol.LISTEN_WORD + " " + name + suffix);
    }

    /** 播放/暂停。 */
    public static void togglePause() {
        SongPlayer.togglePause();
    }

    public static void stopPlayback() {
        PENDING.incrementAndGet();
        stoppedByUser = true;
        lastMyRequestAt = 0L;
        lastMyQuery = "";
        SongState.setLyrics(null);
        SongState.title = "";
        SongState.songId = "";
        SongPlayer.stop();
    }

    /** 设置倍速并写进配置（下次点歌继续用）。 */
    public static void setSpeed(float speed) {
        float value = SongSpeed.normalize(speed);
        if (config != null) {
            config.speed = value;
            config.save();
        }
        SongPlayer.setSpeed(value);
    }

    public static void setVolume(float volume) {
        if (config != null) {
            config.volume = Math.max(0f, Math.min(1f, volume));
            config.save();
        }
        SongPlayer.setVolume(volume);
    }

    /** 循环切换歌词显示方式：action → title → chat → off → action。 */
    public static String cycleLyricMode() {
        if (config == null) {
            return "action";
        }
        String mode = config.lyricMode == null ? "action" : config.lyricMode;
        switch (mode) {
            case "action": mode = "title"; break;
            case "title": mode = "chat"; break;
            case "chat": mode = "off"; break;
            default: mode = "action"; break;
        }
        config.lyricMode = mode;
        config.showLyrics = !"off".equals(mode);
        config.save();
        lastShownLyric = "";
        lastTitleLyric = "";
        return mode;
    }

    public static String lyricModeLabel() {
        if (config == null || !config.showLyrics) {
            return "歌词：关";
        }
        switch (config.lyricMode == null ? "action" : config.lyricMode) {
            case "title": return "歌词：标题";
            case "chat": return "歌词：聊天";
            default: return "歌词：动作条";
        }
    }

    // ------------------------------------------------------------------
    // 聊天处理
    // ------------------------------------------------------------------

    private static String senderName(Object sender) {
        try {
            if (sender == null) {
                return "";
            }
            Object profile = sender.getClass().getMethod("getProfile").invoke(sender);
            Object name = profile == null ? null
                    : profile.getClass().getMethod("getName").invoke(profile);
            return name == null ? "" : String.valueOf(name);
        } catch (Throwable ignored) {
            return "";
        }
    }

    private static void onChat(String message, boolean mine, String sender) {
        try {
            handleChat(message, mine, sender);
        } catch (Throwable t) {
            System.out.println("[songmod] 处理聊天出错（已忽略）：" + t);
        }
    }

    private static synchronized void handleChat(String message, boolean mine, String sender) {
        if (message == null || config == null) {
            return;
        }
        if (modeSolo()) {
            return;                     // 自用模式：完全不参与服务器聊天
        }
        String trigger = config.trigger;
        if (trigger == null || trigger.isEmpty()) {
            return;
        }
        int idx = message.indexOf(trigger);
        if (idx < 0) {
            return;
        }
        String payload = message.substring(idx + trigger.length()).trim();
        int newline = payload.indexOf('\n');
        if (newline >= 0) {
            payload = payload.substring(0, newline).trim();
        }
        if (payload.isEmpty()) {
            return;
        }
        System.out.println("[songmod] 收到点歌消息" + (mine ? "（我发的）: " : "（别人的）: ")
                + shorten(payload, 200));

        // ① 机器人发来的一起听名单（不要求正好在开头：服务器可能加前缀/改写格式）
        List<SongProtocol.ListenEntry> entries = SongProtocol.parseListenList(payload);
        if (payload.contains(SongProtocol.LISTEN_LIST_MARK) || !entries.isEmpty()) {
            expectingListReply = false;
            SongState.setListeners(entries);
            SongState.listenersStatus = entries.isEmpty() ? "现在没有人在听歌" : "";
            return;
        }

        // ② 机器人发来的播放数据
        SongProtocol.Playback playback = SongProtocol.parsePlayback(payload);
        if (playback != null) {
            handlePlayback(playback, payload, mine, sender);
            return;
        }

        // ③ 别人的（不是播放数据的）消息：一律忽略，绝不本地兜底
        if (!mine) {
            return;
        }

        // ④ 我自己的指令
        if (config.guiWord != null && !config.guiWord.isEmpty() && eq(payload, config.guiWord)) {
            openScreen();
            return;
        }
        if (eq(payload, config.stopWord)) {
            stopPlayback();
            feedback("§b[点歌] §e已停止播放");
            return;
        }
        if (eq(payload, config.loginWord) || eq(payload, config.logoutWord)) {
            feedback("§b[点歌] §e网易云登录在【机器人的「点歌」页】里做（扫码），本 Mod 不需要登录。");
            return;
        }
        // 歌词模式： [点歌]歌词 / [点歌]歌词 title / [点歌]歌词 off
        SongSpeed.Split lyricSplit = SongSpeed.splitTrailing(payload);
        String lyricCmd = lyricSplit.text;
        if (lyricCmd.startsWith("歌词") || lyricCmd.startsWith("歌詞")) {
            String arg = lyricCmd.length() > 2 ? lyricCmd.substring(2).trim().toLowerCase() : "";
            if (arg.isEmpty()) {
                feedback("§b[点歌] §a歌词显示方式已切换为：" + lyricModeLabel());
                cycleLyricMode();
            } else {
                if (config != null) {
                    if (arg.equals("关") || arg.equals("off") || arg.equals("关掉")) {
                        config.showLyrics = false;
                        config.lyricMode = "off";
                    } else if (arg.equals("标题") || arg.equals("title")) {
                        config.showLyrics = true;
                        config.lyricMode = "title";
                    } else if (arg.equals("聊天") || arg.equals("chat") || arg.equals("msg")) {
                        config.showLyrics = true;
                        config.lyricMode = "chat";
                    } else {
                        config.showLyrics = true;
                        config.lyricMode = "action";
                    }
                    config.save();
                }
                feedback("§b[点歌] §a歌词显示方式：" + lyricModeLabel());
            }
            return;
        }
        // 倍速： [点歌]倍速 1.5
        if (lyricCmd.startsWith("倍速")) {
            String arg = lyricCmd.length() > 2 ? lyricCmd.substring(2).trim() : "";
            float value = SongSpeed.parseToken(arg);
            if (value > 0) {
                setSpeed(value);
                feedback("§b[点歌] §a倍速已设为 §f" + SongSpeed.label(value));
            } else {
                feedback("§b[点歌] §e倍速可选：0.5 / 0.75 / 1.0 / 1.5 / 2.0");
            }
            return;
        }
        // 一起听： [点歌]一起听 [玩家] [倍速]
        SongProtocol.ListenJoin join = SongProtocol.parseListenJoin(payload);
        if (join != null) {
            if (config.listenTogetherEnabled == false) {
                feedback("§b[点歌] §e一起听已关闭");
                return;
            }
            if (join.listOnly) {
                requestListenList();
                if (join.speed > 0) {
                    setSpeed(join.speed);
                }

            } else {
                requestJoin(join.player, join.speed > 0 ? join.speed : config.speed);
                if (join.speed > 0) {
                    setSpeed(join.speed);
                }
SongState.listenersStatus = "正在加入 " + join.player + " 的一起听…";
            }
            return;
        }

        // ⑤ 我自己的点歌请求：等机器人回直链，等不到才本地兜底
        long now = System.currentTimeMillis();
        String key = payload.toLowerCase();
        if (key.equals(lastKeyText) && now - lastKeyAt < 1500) {
            return;
        }
        lastKeyText = key;
        lastKeyAt = now;
        stoppedByUser = false;
        SongSpeed.Split split = SongSpeed.splitTrailing(payload);
        String query = split.text;
        if (query.isEmpty()) {
            return;
        }
        if (split.speed > 0) {
            setSpeed(split.speed);
        }
        markMyRequest(query);
        feedback("§b[点歌] §7正在点歌：§f" + query
                + (split.speed > 0 ? "  §b" + SongSpeed.label(split.speed) : ""));
        final String searchQuery = query;
        final int seq = PENDING.incrementAndGet();
        SCHED.schedule(() -> {
            synchronized (SongModClient.class) {
                if (!isCurrent(seq)) {
                    return;
                }
                if (config.localFallback) {
                    feedbackIfCurrent(seq, "§b[点歌] §7机器人没响应，本地兜底搜索…");
                    searchAndPlay(searchQuery, seq);
                } else {
                    feedbackIfCurrent(seq, "§b[点歌] §c机器人不在线（或没登录网易云），点歌失败。");
                }
            }
        }, botTimeoutMs(), TimeUnit.MILLISECONDS);
    }

    /** 处理机器人发来的播放数据（含「一起听」的起播位置）。 */
    private static void handlePlayback(SongProtocol.Playback playback, String payload,
                                       boolean mine, String sender) {
        if (stoppedByUser && !mine) {
            return;
        }
        if (mine) {
            stoppedByUser = false;
            lastMyRequestAt = System.currentTimeMillis();
        }
        if (config.onlyMySong && !isForMe(payload, sender, playback)) {
            System.out.println("[songmod] 这条播放数据不是给我的，跳过：" + shorten(payload, 100));
            return;
        }
        final int seq = PENDING.incrementAndGet();
        consumeMyRequest();
        long startMs = playback.atSeconds > 0 ? playback.atSeconds * 1000L : 0L;
        SongState.beginPlayback(playback.label, playback.songId == null ? "" : playback.songId,
                playback.atSeconds >= 0 ? "一起听" : "");
        SongPlayer.setSpeed(config.speed);
        if (playback.hasUrl()) {
            feedbackIfCurrent(seq, "§b[点歌] §a正在播放：§f" + playback.label
                    + (playback.atSeconds > 0 ? " §7（一起听 " + formatTime(startMs) + " 处加入）" : ""));
            SongPlayer.play(playback.url, config.volume, startMs,
                    err -> feedbackIfCurrent(seq, "§b[点歌] §c播放失败：" + err));
            loadLyrics(playback.songId);
            hintIfConfigured(seq);
            return;
        }
        // 直链被服务器改坏的保险：用歌曲 id 自己取链
        feedback("§b[点歌] §7机器人直链不可用，改用歌曲 id 取链…");
        playById(playback.songId, startMs, seq);
    }

    private static void playById(String id, long startMs, int seq) {
        POOL.submit(() -> {
            if (!isCurrent(seq)) {
                return;
            }
            try {
                MusicSearch.Song song = MusicSearch.byId(id, config.neteaseCookie);
                synchronized (SongModClient.class) {
                    if (!isCurrent(seq)) {
                        return;
                    }
                    if (song == null || song.audioUrl == null || song.audioUrl.isEmpty()) {
                        feedbackIfCurrent(seq, "§b[点歌] §c用歌曲 id " + id + " 取不到直链");
                        return;
                    }
                    String trialNote = song.isTrial()
                            ? " §7（试听片段，可在「点歌台」填自己的 Cookie）" : "";
                    String label = song.title + " - " + song.artistText();
                    SongState.beginPlayback(label, song.id, "");
                    feedbackIfCurrent(seq, "§b[点歌] §a正在播放：§f" + label + trialNote);
                    SongPlayer.play(song.audioUrl, config.volume, startMs,
                            err -> feedbackIfCurrent(seq, "§b[点歌] §c播放失败：" + err));
                    loadLyrics(song.id);
                    hintIfConfigured(seq);
                }
            } catch (Throwable t) {
                feedbackIfCurrent(seq, "§b[点歌] §c出错：" + t);
            }
        });
    }

    /** 本地兜底：自己搜歌播放（机器人不在线时用）。 */
    private static void searchAndPlay(String query, int seq) {
        POOL.submit(() -> {
            if (!isCurrent(seq)) {
                return;
            }
            try {
                MusicSearch.Song song = MusicSearch.search(query, config.neteaseCookie);
                synchronized (SongModClient.class) {
                    if (!isCurrent(seq)) {
                        return;
                    }
                    if (song == null) {
                        feedbackIfCurrent(seq, "§b[点歌] §c没找到「" + query + "」");
                        return;
                    }
                    if (song.audioUrl == null || song.audioUrl.isEmpty()) {
                        feedbackIfCurrent(seq, "§b[点歌] §e" + song.title + " §7- "
                                + song.artistText() + "：" + (song.note == null ? "暂时无法播放" : song.note));
                        return;
                    }
                    String trialNote = song.isTrial() ? " §7（试听片段，可在「点歌台」填自己的 Cookie）" : "";
                    String label = song.title + " - " + song.artistText();
                    SongState.beginPlayback(label, song.id, "");
                    feedbackIfCurrent(seq, "§b[点歌] §a正在播放：§f" + label + trialNote);
                    SongPlayer.play(song.audioUrl, config.volume, 0L,
                            err -> feedbackIfCurrent(seq, "§b[点歌] §c播放失败：" + err));
                    loadLyrics(song.id);
                    hintIfConfigured(seq);
                }
            } catch (Throwable t) {
                feedbackIfCurrent(seq, "§b[点歌] §c出错：" + t);
            }
        });
    }

    /** 后台抓这首歌的歌词（按网易云 id）。 */
    private static void loadLyrics(String songId) {
        SongState.setLyrics(null);
        SongState.lyricsStatus = "";
        if (songId == null || songId.isEmpty()) {
            SongState.lyricsStatus = "没有歌曲 id，无法加载歌词";
            return;
        }
        if (config == null || !config.showLyrics || "off".equals(config.lyricMode)) {
            return;
        }
        SongState.lyricsStatus = "歌词加载中…";
        POOL.submit(() -> {
            try {
                SongLyrics.Lyrics lyrics = SongLyrics.get(songId, config == null ? "" : config.neteaseCookie);
                if (lyrics == null || lyrics.isEmpty()) {
                    SongState.setLyrics(null);
                    SongState.lyricsStatus = "这首歌没有可用歌词";
                } else {
                    SongState.setLyrics(lyrics);
                    SongState.lyricsStatus = "";
                }
            } catch (Throwable t) {
                SongState.lyricsStatus = "歌词加载失败：" + t;
            }
        });
    }

    private static void markMyRequest(String query) {
        long now = System.currentTimeMillis();
        lastMyRequestAt = now;
        lastMyQuery = query == null ? "" : query;
        stoppedByUser = false;
    }

    // ------------------------------------------------------------------
    // 歌词显示（tick 里按播放进度刷新）
    // ------------------------------------------------------------------

    private static void tickLyrics(Minecraft mc) {
        if (mc == null || config == null) {
            return;
        }
        if (!config.showLyrics || "off".equals(config.lyricMode)) {
            return;
        }
        long position = SongPlayer.positionMs();
        SongState.syncLyrics(position);
        String line = SongState.lyricLine;
        if (line == null || line.isEmpty()) {
            return;
        }
        String prefix = config.lyricPrefix == null ? "" : config.lyricPrefix;
        String text = prefix + line;
        long now = System.currentTimeMillis();
        String mode = config.lyricMode == null ? "action" : config.lyricMode;

        if ("title".equals(mode)) {
            if (text.equals(lastTitleLyric)) {
                return;
            }
            lastTitleLyric = text;
            try {
                mc.gui.setTimes(config.lyricTitleFadeIn, config.lyricTitleStay, config.lyricTitleFadeOut);
                mc.gui.setTitle(Component.literal(text));
            } catch (Throwable ignored) {
                // ignore
            }
            return;
        }
        if ("chat".equals(mode)) {
            if (text.equals(lastShownLyric)) {
                return;
            }
            lastShownLyric = text;
            feedback("§b♪ §f" + text);
            return;
        }
        // action：动作条会自己淡出，需要每隔一会儿重发一次
        if (text.equals(lastShownLyric) && now - lastLyricShownAt < 1200L) {
            return;
        }
        lastShownLyric = text;
        lastLyricShownAt = now;
        try {
            mc.gui.setOverlayMessage(Component.literal("§b♪ §f" + text), false);
        } catch (Throwable ignored) {
            // ignore
        }
    }

    // ------------------------------------------------------------------
    // 小工具
    // ------------------------------------------------------------------

    private static boolean isForMe(String payload, String sender, SongProtocol.Playback playback) {
        // ① 配了机器人名：只认这个发送者
        String bot = config.botName == null ? "" : config.botName.trim();
        if (!bot.isEmpty() && sender != null && !sender.isEmpty()) {
            return sender.equalsIgnoreCase(bot);
        }
        // ② 正在等「一起听」的回复：这段时间内机器人回的任何播放数据都算我的
        if (expectingListenReply && System.currentTimeMillis() - expectingListenAt < PENDING_WINDOW_MS) {
            expectingListenReply = false;
            return true;
        }
        // ③ 退回「我最近点的歌」关键词匹配
        if (System.currentTimeMillis() - lastMyRequestAt < 300000L) {
            String p = normalizeForMatch(payload);
            String q = normalizeForMatch(lastMyQuery);
            if (!q.isEmpty() && (p.contains(q) || q.contains(p))) {
                return true;
            }
            String label = normalizeForMatch(playback == null ? "" : playback.label);
            String q2 = normalizeForMatch(lastMyQuery);
            if (!label.isEmpty() && !q2.isEmpty() && (label.contains(q2) || q2.contains(label))) {
                return true;
            }
        }
        return false;
    }

    private static String normalizeForMatch(String s) {
        if (s == null) {
            return "";
        }
        StringBuilder b = new StringBuilder();
        for (char c : s.toLowerCase().toCharArray()) {
            if (Character.isLetterOrDigit(c)) {
                b.append(c);
            }
        }
        return b.toString();
    }

    private static void consumeMyRequest() {
        lastMyRequestAt = 0L;
        lastMyQuery = "";
        expectingListenReply = false;
    }

    private static boolean isCurrent(int seq) {
        return !stoppedByUser && PENDING.get() == seq;
    }

    private static void hintIfConfigured(int seq) {
        if (config.modUrl == null || config.modUrl.trim().isEmpty()) {
            return;
        }
        String hint = (config.hintText == null ? "" : config.hintText).replace("{url}", config.modUrl.trim());
        if (!hint.trim().isEmpty()) {
            feedbackIfCurrent(seq, "§b[点歌] §e" + hint);
        }
    }

    private static boolean eq(String a, String b) {
        return a != null && b != null && !b.isEmpty() && a.equalsIgnoreCase(b);
    }

    private static void feedbackIfCurrent(int seq, String message) {
        feedback(seq, message);
    }

    private static void feedback(String msg) {
        feedback(-1, msg);
    }

    private static void feedback(int seq, String msg) {
        Minecraft mc = Minecraft.getInstance();
        if (mc == null) {
            return;
        }
        mc.execute(() -> {
            synchronized (SongModClient.class) {
                if (seq >= 0 && !isCurrent(seq)) {
                    return;
                }
                try {
                    if (mc.gui != null && mc.gui.getChat() != null) {
                        mc.gui.getChat().addMessage(Component.literal(msg));
                    }
                } catch (Throwable ignored) {
                    // ignore
                }
            }
        });
    }

    private static String formatTime(long ms) {
        long total = Math.max(0L, ms) / 1000L;
        return String.format("%d:%02d", total / 60, total % 60);
    }

    private static String shorten(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "…";
    }
}
