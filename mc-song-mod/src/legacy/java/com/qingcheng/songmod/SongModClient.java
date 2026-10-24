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
import org.lwjgl.glfw.GLFW;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 点歌 Mod（客户端）。
 *
 * <p>只做两件事：
 * <ol>
 *   <li>监听聊天里的 {@code [点歌]...}（自己发的 + 服务器广播来的）；</li>
 *   <li>把**机器人**发到公屏的播放数据 {@code [点歌]歌名 - 歌手 | <直链>} 播放出来
 *       （机器人持有网易云会员，所以 VIP 歌也能放，玩家不用登录）。</li>
 * </ol>
 *
 * <p>机器人不在线（没发来直链）就提示**点歌失败**——本 Mod **不做任何本地兜底**，一切以机器人为准。
 */
public class SongModClient implements ClientModInitializer {

    private static SongConfig config;

    /** 按键注册名（对应 lang 里的 key.songmod.open）。 */
    private static final String KEY_NAME = "key.songmod.open";

    /** 定个超时等机器人发直链；等不到就本地兜底（或报失败）。 */
    private static final ScheduledExecutorService SCHED = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "songmod-wait");
        t.setDaemon(true);
        return t;
    });

    /** 本地兜底搜索用的单线程池。 */
    private static final ExecutorService POOL = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "songmod-search");
        t.setDaemon(true);
        return t;
    });

    /** 每次「点歌」递增；机器人直链到达时也递增，用来识别「这条请求已经有结果了」。 */
    private static final AtomicInteger PENDING = new AtomicInteger(0);

    /** 等机器人响应的时间（毫秒）。给足 30 秒，避免机器人还在启动/登录时就被本地兜底抢先。 */
    private static final long WAIT_MS = 30000L;

    private static volatile String lastKey = "";
    private static volatile long lastKeyAt = 0L;
    /** 我最近一次点歌的时间（「谁点谁听」用：机器人私聊回来的播放数据是给我的）。 */
    private static volatile long lastMyRequestAt = 0L;
    /** 我最近一次点歌的关键词/链接/id —— 只有和它匹配的播放数据才算我的（#15）。 */
    private static volatile String lastMyQuery = "";
    private static volatile boolean stoppedByUser = false;

    /** 打开「点歌台」界面的按键（可在游戏的「按键设置」里改）。 */
    private static KeyMapping openKey;
    private static volatile String currentLabel = "";
    private static final List<String> MY_REQUESTS = Collections.synchronizedList(new ArrayList<>());

    @Override
    public void onInitializeClient() {
        config = SongConfig.load();
        // 版本标记：换 jar 后用这行确认游戏里加载的是哪一版（见日志 [songmod] 开头那几行）
        System.out.println("[songmod] 版本 v1.0.0+obflink5-20261017"
                + "（修复：①[链接]替换→混淆直链；②CHAT+GAME 双通道；③id 标记不用 #/&；"
                + "④谁点谁听：只播给我的歌、别人的点歌不触发本地兜底；⑤停止只停自己的）");
        ClientSendMessageEvents.CHAT.register(text -> onChat(text, true));   // 我自己发的
        // 用 lambda 推断参数类型，避免依赖具体版本里 ChatType/MessageType 的类型名。
        // 服务器发来的聊天（包括别人转发的、以及我自己那条被服务器广播回来的）一律 mine=false：
        // 我自己的点歌只看上面那个本地发送事件，绝不靠「聊天里出现我的名字」来判断。
        ClientReceiveMessageEvents.CHAT.register((message, signedMessage, sender, params, timestamp) ->
                onChat(message == null ? "" : message.getString(), false));
        // ⚠ 很多服务器（聊天插件改格式后重播的那种，形如「[称号]名字 » 消息」）是把玩家聊天
        // 当**系统消息**发出来的，这种只走 GAME 不走 CHAT —— 机器人发的直链以前就是这么丢的
        // （表现：机器人明明发了，Mod 却干等 30 秒走本地兜底）。所以两个通道都要收。
        ClientReceiveMessageEvents.GAME.register((message, overlay) ->
                onChat(message == null ? "" : message.getString(), false));

        openKey = createKeyMapping();
        if (openKey != null) {
            KeyBindingHelper.registerKeyBinding(openKey);
            System.out.println("[songmod] 已注册按键 key.songmod.open（默认 K），可在「按键设置」里改");
        } else {
            System.out.println("[songmod] 按键注册失败：可在聊天里发 " + config.trigger + "台 打开界面");
        }
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            if (openKey != null) {
                while (openKey.consumeClick()) {
                    openScreen();
                }
            }
        });
    }

    /**
     * 用反射建 KeyMapping（版本差异大）：
     * <ul>
     *   <li>1.21.1~1.21.8：第 4 参是 String（分类名）；</li>
     *   <li>1.21.9+：第 4 参是 {@code KeyMapping.Category}。</li>
     * </ul>
     * <b>注意</b>：这里绝不能写死「net.minecraft.client.KeyMapping$Category」这类<b>开发期名字</b>——
     * 正式环境里 MC 的类名是混淆后的（开发名根本不存在），写死会 ClassNotFoundException，
     * 结果就是<b>按键压根没注册上</b>（K 键没反应的原因）。
     * 所以只按「构造器参数的类型/个数」找，再取该类型的静态实例，全程不依赖类名/字段名。
     */
    private static KeyMapping createKeyMapping() {
        try {
            for (java.lang.reflect.Constructor<?> c : KeyMapping.class.getDeclaredConstructors()) {
                Class<?>[] p = c.getParameterTypes();
                if (p.length != 4 || p[0] != String.class || p[1] != InputConstants.Type.class || p[2] != int.class) {
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
                return (KeyMapping) c.newInstance(KEY_NAME,
                        InputConstants.Type.KEYSYM, GLFW.GLFW_KEY_K, fourth);
            }
        } catch (Throwable t) {
            System.out.println("[songmod] 注册按键失败：" + t);
        }
        return null;
    }

    /** 取某类型的一个静态实例（如 Category 的 MOVEMENT/MISC/…），不依赖字段名。 */
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

    /** 打开「点歌台」界面（按键 / 聊天词都走这里）。 */
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

    // --- 给界面用的小接口 ---
    public static String trigger() {
        return config == null || config.trigger == null || config.trigger.isEmpty() ? "[点歌]" : config.trigger;
    }

    public static String stopWord() {
        return config == null || config.stopWord == null || config.stopWord.isEmpty() ? "停止" : config.stopWord;
    }

    public static String openKeyName() {
        try {
            return openKey == null ? "K" : openKey.getTranslatedKeyMessage().getString();
        } catch (Throwable t) {
            return "K";
        }
    }

    public static String currentLabel() {
        return currentLabel;
    }

    public static List<String> myHistory() {
        synchronized (MY_REQUESTS) {
            return new ArrayList<>(MY_REQUESTS);
        }
    }

    public static void addMyRequest(String query) {
        MY_REQUESTS.add(query);
        while (MY_REQUESTS.size() > 50) {
            MY_REQUESTS.remove(0);
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

    /** 以玩家身份发一条聊天（界面里点歌用）。 */
    public static void sendChat(String text) {
        Minecraft mc = Minecraft.getInstance();
        if (mc != null && mc.player != null) {
            mc.player.connection.sendChat(text);
        }
    }

    private static void onChat(String message, boolean mine) {
        // 兜一层：任何异常都不能漏到 Fabric 事件回调里（否则可能把客户端连接搞崩）。
        try {
            handleChat(message, mine);
        } catch (Throwable t) {
            System.out.println("[songmod] 处理聊天出错（已忽略）：" + t);
        }
    }

    private static synchronized void handleChat(String message, boolean mine) {
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
        // 诊断日志：带触发词的消息原文都留一条（定位「机器人发了但 Mod 没反应」时看这个）
        System.out.println("[songmod] 收到点歌消息" + (mine ? "（我发的）: " : "（别人的）: ")
                + shorten(payload, 200));

        // ① 机器人发来的播放数据：[点歌]… | ¤<混淆直链>（公屏会把 http 链接换成 [链接]，所以机器人做了混淆）
        String audioUrl = extractObfuscatedUrl(payload);
        if (audioUrl == null) {
            audioUrl = firstAudioUrl(payload);
        }
        if (audioUrl != null) {
            if (stoppedByUser && !mine) {
                return;
            }
            if (mine) {
                stoppedByUser = false;
                lastMyRequestAt = System.currentTimeMillis();
            }
            if (config.onlyMySong && !isForMe(payload)) {
                // 谁点谁听：这条不是给我的（别人的点歌 / 广播残留）→ 不播
                System.out.println("[songmod] 这条点歌不是给我的，跳过：" + shorten(payload, 100));
                return;
            }
            final int seq = PENDING.incrementAndGet();
            consumeMyRequest();          // 这条请求有结果了（#15）
            String label = songLabel(payload, audioUrl);
            currentLabel = label;
            System.out.println("[songmod] 用机器人直链播放: " + shorten(label, 80)
                    + " <- " + shorten(audioUrl, 90));
            feedbackIfCurrent(seq, "§b[点歌] §a正在播放：§f" + label);
            hintIfConfigured(seq);
            SongPlayer.play(audioUrl, config.volume,
                    err -> feedbackIfCurrent(seq, "§b[点歌] §c播放失败：" + err));
            return;
        }

        // 机器人直链被服务器改坏的**第二道保险**：[点歌]… | ¤直链 id:1901371647 → 用歌曲 id 自己取直链
        String songId = extractSongId(payload);
        if (songId != null) {
            if (stoppedByUser && !mine) {
                return;
            }
            if (mine) {
                stoppedByUser = false;
                lastMyRequestAt = System.currentTimeMillis();
            }
            if (config.onlyMySong && !isForMe(payload)) {
                System.out.println("[songmod] 这条 id 取链不是给我的，跳过：" + shorten(payload, 100));
                return;
            }
            final int seq = PENDING.incrementAndGet();
            consumeMyRequest();          // 这条请求有结果了（#15）
            feedback("§b[点歌] §7机器人直链不可用，改用歌曲 id 取链…");
            playById(songId, seq);
            return;
        }

        // ② 既不是播放数据、又不是我自己发的 —— 那就是**别人的点歌/指令**，一律忽略：
        //    关键！以前这里会往下走 → 30 秒后本地兜底搜索 → 别人点的歌在我这儿响了起来。
        if (!mine) {
            System.out.println("[songmod] 别人的消息，忽略（不触发兜底）：" + shorten(payload, 100));
            return;
        }

        // ③ 我自己的指令：打开「点歌台」界面（按键万一打不开时的备用入口）
        if (config.guiWord != null && !config.guiWord.isEmpty() && eq(payload, config.guiWord)) {
            openScreen();
            return;
        }

        // 停止： [点歌]停止（只停我自己的）
        if (eq(payload, config.stopWord)) {
            stopPlayback();
            feedback("§b[点歌] §e已停止播放");
            return;
        }
        // 登录在机器人那边： [点歌]登录
        if (eq(payload, config.loginWord) || eq(payload, config.logoutWord)) {
            feedback("§b[点歌] §e网易云登录在【机器人的「点歌」页】里做（扫码），本 Mod 不需要登录。");
            return;
        }

        // ④ 我自己的点歌请求：等机器人（私聊）回直链，等不到才本地兜底

        long now = System.currentTimeMillis();
        String key = payload.toLowerCase();
        if (key.equals(lastKey) && now - lastKeyAt < 1500) {
            return;
        }
        lastKey = key;
        lastKeyAt = now;
        stoppedByUser = false;
        // 记下「我点过歌」的时刻和关键词：机器人把播放数据私聊回来时用它判断这是给我的
        lastMyRequestAt = now;
        lastMyQuery = payload;

        feedback("§b[点歌] §7正在点歌：§f" + payload);
        final String query = payload;
        final int seq = PENDING.incrementAndGet();
        SCHED.schedule(() -> {
            synchronized (SongModClient.class) {
                if (!isCurrent(seq)) {
                    return;
                }
                if (config.localFallback) {
                    feedbackIfCurrent(seq, "§b[点歌] §7机器人没响应，本地兜底搜索…");
                    searchAndPlay(query, seq);
                } else {
                    feedbackIfCurrent(seq, "§b[点歌] §c机器人不在线（或没登录网易云），点歌失败。");
                }
            }
        }, WAIT_MS, TimeUnit.MILLISECONDS);
    }

    /** 用歌曲 id 自己取直链播放（机器人直链被服务器改坏时的保险）。 */
    private static void playById(String id, int seq) {
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
                    String trialNote = song.isTrial() ? " §7（试听片段，可在「点歌台」填自己的 Cookie）" : "";
                    String label = song.title + " - " + song.artistText();
                    currentLabel = label;
                    System.out.println("[songmod] 用歌曲 id 取链播放: #" + id + " -> "
                            + shorten(song.audioUrl, 90));
                    feedbackIfCurrent(seq, "§b[点歌] §a正在播放：§f" + label + trialNote);
                    hintIfConfigured(seq);
                    SongPlayer.play(song.audioUrl, config.volume,
                            err -> feedbackIfCurrent(seq, "§b[点歌] §c播放失败：" + err));
                }
            } catch (Throwable t) {
                feedbackIfCurrent(seq, "§b[点歌] §c出错：" + t);
            }
        });
    }

    /**
     * 从机器人文案里找歌曲 id：机器人格式是 {@code … | ¤混淆直链 id:3399839173}。
     *
     * <p>⚠ 不能用 {@code #}（会被服务器当十六进制颜色码吃掉）、也不能用 {@code &}（MC 颜色符），
     * 所以用 {@code id:} 前缀。另兼容老的 {@code #123456} 写法。
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
                        feedbackIfCurrent(seq, "§b[点歌] §e" + song.title + " §7- " + song.artistText()
                                + "：" + (song.note == null ? "暂时无法播放" : song.note));
                        return;
                    }
                    String trialNote = song.isTrial()
                            ? (song.trialSeconds > 0
                                ? " §7（试听 " + song.trialSeconds + "s，可在「点歌台」填自己的 Cookie）"
                                : " §7（试听片段）")
                            : "";
                    String label = song.title + " - " + song.artistText();
                    currentLabel = label;
                    feedbackIfCurrent(seq, "§b[点歌] §a正在播放：§f" + label + trialNote);
                    hintIfConfigured(seq);
                    SongPlayer.play(song.audioUrl, config.volume,
                            err -> feedbackIfCurrent(seq, "§b[点歌] §c播放失败：" + err));
                }
            } catch (Throwable t) {
                feedbackIfCurrent(seq, "§b[点歌] §c出错：" + t);
            }
        });
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

    private static boolean isCurrent(int seq) {
        return !stoppedByUser && PENDING.get() == seq;
    }

    private static synchronized void stopPlayback() {
        PENDING.incrementAndGet();
        stoppedByUser = true;
        lastMyRequestAt = 0L;
        lastKey = "";
        lastKeyAt = 0L;
        currentLabel = "";
        SongPlayer.stop();
    }

    private static void feedbackIfCurrent(int seq, String message) {
        feedback(seq, message);
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
            return true;   // 认不出自己是谁就别拦（宁可多放，也别放不出来）
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

    /** 日志用：太长就截断（避免整条直链刷进日志）。 */
    private static String shorten(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "…";
    }

    /**
     * 还原机器人发来的混淆直链。公屏的链接过滤会把 {@code http(s)://…} 整条替换成「[链接]」，
     * 所以机器人发的是：{@code ¤}<b>混淆直链</b>（{@code ¤}=http、{@code ¥}=https，且把
     * {@code . / :} 换成了 {@code · ¦ ∶}、去掉了 query）。这里还原成完整 URL。
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

    /** 从文本里找第一个看起来是音频直链的 URL（机器人万一没混淆时的兜底）。 */
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
        feedback(-1, msg);
    }

    private static void feedback(int seq, String msg) {
        Minecraft mc = Minecraft.getInstance();
        if (mc == null) {
            return;
        }
        // ChatComponent 只在客户端主线程改，避免从后台线程调用被吞/出错。
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
}
