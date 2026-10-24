package com.qingcheng.songmod;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 26.x 版点歌 Mod（Minecraft 26.x 的渲染 API 大改，这一版**不含游戏内界面**）。
 *
 * <p>逻辑和 1.21.x 版一致：优先播放机器人发来的直链；机器人不在线时本地兜底
 * （用 config 里的 {@code neteaseCookie}）；再不行就提示失败。
 * 提示用 26.x 的 {@code ChatComponent.addClientSystemMessage}。
 */
public class SongModClient implements ClientModInitializer {

    private static SongConfig config;

    private static final ScheduledExecutorService SCHED = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "songmod-wait");
        t.setDaemon(true);
        return t;
    });

    private static final ExecutorService POOL = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "songmod-search");
        t.setDaemon(true);
        return t;
    });

    private static final AtomicInteger PENDING = new AtomicInteger(0);
    private static final long WAIT_MS = 30000L;

    private static volatile String lastKey = "";
    private static volatile long lastKeyAt = 0L;
    /** 我最近一次点歌的时间（「谁点谁听」用）。 */
    private static volatile long lastMyRequestAt = 0L;
    /** 我最近一次点歌的关键词/链接/id —— 只有和它匹配的播放数据才算我的（#15）。 */
    private static volatile String lastMyQuery = "";
    private static volatile String currentLabel = "";

    @Override
    public void onInitializeClient() {
        config = SongConfig.load();
        System.out.println("[songmod] 版本 v1.0.0+obflink5-20261017（26.x 无界面版；谁点谁听）");
        ClientSendMessageEvents.CHAT.register(text -> onChat(text, true));   // 我自己发的
        // 服务器发来的聊天一律 mine=false（我自己的点歌只看本地发送事件）
        ClientReceiveMessageEvents.CHAT.register((message, signedMessage, sender, params, timestamp) ->
                onChat(message == null ? "" : message.getString(), false));
        // 服务器把玩家聊天当「系统消息」重播时只走 GAME、不走 CHAT —— 两个通道都得收
        ClientReceiveMessageEvents.GAME.register((message, overlay) ->
                onChat(message == null ? "" : message.getString(), false));
    }

    private static void onChat(String message, boolean mine) {
        // 兜一层：异常不能漏到 Fabric 事件回调里。
        try {
            handleChat(message, mine);
        } catch (Throwable t) {
            System.out.println("[songmod] 处理聊天出错（已忽略）：" + t);
        }
    }

    private static void handleChat(String message, boolean mine) {
        if (message == null || config == null) {
            return;
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

        // 机器人发来的播放数据：[点歌]… | ¤<混淆直链>（公屏会把 http 链接换成 [链接]，所以机器人做了混淆）
        String audioUrl = extractObfuscatedUrl(payload);
        if (audioUrl == null) {
            audioUrl = firstAudioUrl(payload);
        }
        if (audioUrl != null) {
            if (config.onlyMySong && !isForMe(payload)) {
                System.out.println("[songmod] 这条点歌不是给我的，跳过：" + shorten(payload, 100));
                return;
            }
            PENDING.incrementAndGet();
            consumeMyRequest();
            String label = songLabel(payload, audioUrl);
            currentLabel = label;
            System.out.println("[songmod] 用机器人直链播放: " + shorten(label, 80)
                    + " <- " + shorten(audioUrl, 90));
            feedback("§b[点歌] §a正在播放：§f" + label);
            hintIfConfigured();
            SongPlayer.play(audioUrl, config.volume,
                    err -> feedback("§b[点歌] §c播放失败：" + err));
            return;
        }

        // 机器人直链被服务器改坏时的第二道保险：… | ¤直链 id:1901371647 → 用歌曲 id 自己取直链
        String songId = extractSongId(payload);
        if (songId != null) {
            if (config.onlyMySong && !isForMe(payload)) {
                System.out.println("[songmod] 这条 id 取链不是给我的，跳过：" + shorten(payload, 100));
                return;
            }
            PENDING.incrementAndGet();
            consumeMyRequest();
            feedback("§b[点歌] §7机器人直链不可用，改用歌曲 id 取链…");
            playById(songId);
            return;
        }

        // 既不是播放数据、又不是我自己发的 → 别人的点歌/指令，一律忽略
        // （关键：以前这里会往下走 → 30 秒后本地兜底搜索 → 别人点的歌在我这儿响）
        if (!mine) {
            System.out.println("[songmod] 别人的消息，忽略（不触发兜底）：" + shorten(payload, 100));
            return;
        }

        // 我自己的指令：[点歌]台（这一版没界面）/ [点歌]停止 / [点歌]登录
        if (config.guiWord != null && !config.guiWord.isEmpty() && eq(payload, config.guiWord)) {
            feedback("§b[点歌] §e这一版（26.x）没有游戏内界面，直接在聊天发 [点歌]歌名 就行。");
            return;
        }
        if (eq(payload, config.stopWord)) {
            SongPlayer.stop();      // 只停我自己的
            feedback("§b[点歌] §e已停止播放");
            return;
        }
        if (eq(payload, config.loginWord) || eq(payload, config.logoutWord)) {
            feedback("§b[点歌] §e网易云登录在【机器人的「点歌」页】里做；本 Mod 的 Cookie 请写在 config/songmod.json。");
            return;
        }

        long now = System.currentTimeMillis();
        String key = payload.toLowerCase();
        if (key.equals(lastKey) && now - lastKeyAt < 1500) {
            return;
        }
        lastKey = key;
        lastKeyAt = now;
        // 记下「我点过歌」的时刻和关键词：机器人私聊回来的播放数据用它判断「这是给我的」
        lastMyRequestAt = now;
        lastMyQuery = payload;

        feedback("§b[点歌] §7正在点歌：§f" + payload);
        final String query = payload;
        final int seq = PENDING.incrementAndGet();
        SCHED.schedule(() -> {
            if (PENDING.get() != seq) {
                return;
            }
            if (config.localFallback) {
                feedback("§b[点歌] §7机器人没响应，本地兜底搜索…");
                searchAndPlay(query);
            } else {
                feedback("§b[点歌] §c机器人不在线（或没登录网易云），点歌失败。");
            }
        }, WAIT_MS, TimeUnit.MILLISECONDS);
    }

    /** 用歌曲 id 自己取直链播放（机器人直链被服务器改坏时的保险）。 */
    private static void playById(String id) {
        POOL.submit(() -> {
            try {
                MusicSearch.Song song = MusicSearch.byId(id, config.neteaseCookie);
                if (song == null || song.audioUrl == null || song.audioUrl.isEmpty()) {
                    feedback("§b[点歌] §c用歌曲 id " + id + " 取不到直链");
                    return;
                }
                String trialNote = song.isTrial() ? " §7（试听片段）" : "";
                String label = song.title + " - " + song.artistText();
                currentLabel = label;
                System.out.println("[songmod] 用歌曲 id 取链播放: #" + id + " -> "
                        + shorten(song.audioUrl, 90));
                feedback("§b[点歌] §a正在播放：§f" + label + trialNote);
                hintIfConfigured();
                SongPlayer.play(song.audioUrl, config.volume,
                        err -> feedback("§b[点歌] §c播放失败：" + err));
            } catch (Throwable t) {
                feedback("§b[点歌] §c出错：" + t);
            }
        });
    }

    /**
     * 从机器人文案里找歌曲 id：机器人格式是 {@code … | ¤混淆直链 id:3399839173}。
     * ⚠ 不能用 {@code #}（会被服务器当十六进制颜色码吃掉）、也不能用 {@code &}（MC 颜色符）。
     */
    private static String extractSongId(String payload) {
        if (payload == null) {
            return null;
        }
        String low = payload.toLowerCase();
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

    private static void searchAndPlay(String query) {
        POOL.submit(() -> {
            try {
                MusicSearch.Song song = MusicSearch.search(query, config.neteaseCookie);
                if (song == null) {
                    feedback("§b[点歌] §c没找到「" + query + "」");
                    return;
                }
                if (song.audioUrl == null || song.audioUrl.isEmpty()) {
                    feedback("§b[点歌] §e" + song.title + " §7- " + song.artistText()
                            + "：" + (song.note == null ? "暂时无法播放" : song.note));
                    return;
                }
                String trialNote = song.isTrial() ? " §7（试听片段）" : "";
                String label = song.title + " - " + song.artistText();
                currentLabel = label;
                feedback("§b[点歌] §a正在播放：§f" + label + trialNote);
                hintIfConfigured();
                SongPlayer.play(song.audioUrl, config.volume,
                        err -> feedback("§b[点歌] §c播放失败：" + err));
            } catch (Throwable t) {
                feedback("§b[点歌] §c出错：" + t);
            }
        });
    }

    private static void hintIfConfigured() {
        if (config.modUrl == null || config.modUrl.trim().isEmpty()) {
            return;
        }
        String hint = (config.hintText == null ? "" : config.hintText).replace("{url}", config.modUrl.trim());
        if (!hint.trim().isEmpty()) {
            feedback("§b[点歌] §e" + hint);
        }
    }

    private static boolean eq(String a, String b) {
        return a != null && b != null && !b.isEmpty() && a.equalsIgnoreCase(b);
    }

    /** 这条播放数据是不是放给我的（谁点谁听）。 */
    private static boolean isForMe(String payload) {
        String p = payload == null ? "" : payload.toLowerCase();
        String me = myName();
        if (!me.isEmpty() && p.contains(me.toLowerCase())) {
            return true;   // 文案里有我的名字 → 肯定是给我的
        }
        // 只有「我最近点的歌」才对得上；不能因为 5 分钟内点过歌，
        // 就把别人点的歌也放出来（#15）。
        if (System.currentTimeMillis() - lastMyRequestAt < 300000L) {
            String q = lastMyQuery;
            String np = normalizeForMatch(p);
            String nq = normalizeForMatch(q);
            if (!nq.isEmpty() && (np.contains(nq) || nq.contains(np))) {
                return true;
            }
        }
        if (me.isEmpty()) {
            return true;   // 认不出自己是谁就别拦
        }
        return false;
    }

    /** 匹配用：小写、只留字母/数字/汉字（去掉空格、标点、颜色符）。 */
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

    /** 收到属于自己的播放数据后清掉「最近点歌」，避免 5 分钟内继续接受别人的歌。 */
    private static void consumeMyRequest() {
        lastMyRequestAt = 0L;
        lastMyQuery = "";
    }

    /** 自己的游戏名（先取登录账号名，取不到再取玩家名）。 */
    private static String myName() {
        try {
            Minecraft mc = Minecraft.getInstance();
            if (mc == null) {
                return "";
            }
            if (mc.getUser() != null && mc.getUser().getName() != null) {
                return mc.getUser().getName();
            }
            if (mc.player != null && mc.player.getName() != null) {
                return mc.player.getName().getString();
            }
        } catch (Throwable ignored) {
            // 任何 API 差异都不该影响播放
        }
        return "";
    }

    /** 日志用：太长就截断。 */
    private static String shorten(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "…";
    }

    /**
     * 还原机器人发来的混淆直链（{@code ¤}=http、{@code ¥}=https，{@code . / :} → {@code · ¦ ∶}，
     * 见机器人侧 {@code Controller._encode_url}）。公屏的链接过滤会把 {@code http(s)://…}
     * 整条替换成「[链接]」，所以机器人发的是去协议头、去 query 的混淆形式。没有标记时返回 null。
     */
    private static String extractObfuscatedUrl(String payload) {
        if (payload == null) {
            return null;
        }
        int a = payload.indexOf('¤');
        int b = payload.indexOf('¥');
        int pos = a < 0 ? b : (b < 0 ? a : Math.min(a, b));
        if (pos < 0) {
            return null;
        }
        String scheme = payload.charAt(pos) == '¥' ? "https://" : "http://";
        String rest = payload.substring(pos + 1).trim();
        int end = 0;
        while (end < rest.length() && !Character.isWhitespace(rest.charAt(end))) {
            end++;
        }
        String body = rest.substring(0, end)
                .replace('·', '.').replace('¦', '/').replace('∶', ':');
        return body.isEmpty() ? null : scheme + body;
    }

    /** 播放标签：取直链前面那段（机器人格式是「文案 | 直链」）。 */
    private static String songLabel(String payload, String audioUrl) {
        String label = payload;
        int bar = label.lastIndexOf('|');
        if (bar > 0) {
            label = label.substring(0, bar);
        } else {
            int idx = payload.indexOf(audioUrl);
            if (idx > 0) {
                label = payload.substring(0, idx);
            }
        }
        label = label.replace('¤', ' ').replace('¥', ' ')
                .replaceAll("[|\\-]+\\s*$", "").trim();
        return label.isEmpty() ? "歌曲" : label;
    }

    /** 从文本里找第一个看起来是音频直链的 URL（机器人没混淆时的兜底）。 */
    private static String firstAudioUrl(String text) {
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
            String low = candidate.toLowerCase();
            if (low.contains(".mp3") || low.contains("music.126.net") || low.contains(".m4a")
                    || low.contains(".flac") || low.contains("song/media/outer/url")) {
                return candidate;
            }
            i = text.indexOf("http", end);
        }
        return null;
    }

    private static void feedback(String msg) {
        Minecraft mc = Minecraft.getInstance();
        if (mc == null) {
            return;
        }
        mc.execute(() -> addChatLine(msg));
    }

    /**
     * 往聊天栏打一行客户端提示。
     *
     * <p>26.x 里这个入口挪过窝：<b>26.1.x</b> 是 {@code mc.gui.getChat()}，
     * <b>26.2</b> 变成了 {@code mc.gui.hud.getChat()}（Gui 里只剩 {@code hud}）。所以用反射两条路都试，
     * 失败就退化成写日志 —— 任何版本差异都不该影响播放本身。
     */
    private static void addChatLine(String msg) {
        Minecraft mc = Minecraft.getInstance();
        try {
            if (mc == null || mc.gui == null) {
                return;
            }
            Object chat = callNoArg(mc.gui, "getChat");
            if (chat == null) {
                Object hud = getField(mc.gui, "hud");
                if (hud != null) {
                    chat = callNoArg(hud, "getChat");
                }
            }
            if (chat == null) {
                System.out.println("[songmod] " + msg);
                return;
            }
            chat.getClass().getMethod("addClientSystemMessage", Component.class)
                    .invoke(chat, Component.literal(msg));
        } catch (Throwable t) {
            // 最后兜底：至少日志里有
            System.out.println("[songmod] " + msg);
        }
    }

    /** 反射调用无参方法（拿不到返回 null）。 */
    private static Object callNoArg(Object target, String name) {
        try {
            return target.getClass().getMethod(name).invoke(target);
        } catch (Throwable t) {
            return null;
        }
    }

    /** 反射取字段（沿父类往上找，拿不到返回 null）。 */
    private static Object getField(Object target, String name) {
        Class<?> c = target.getClass();
        while (c != null) {
            try {
                java.lang.reflect.Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(target);
            } catch (Throwable t) {
                c = c.getSuperclass();
            }
        }
        return null;
    }
}
