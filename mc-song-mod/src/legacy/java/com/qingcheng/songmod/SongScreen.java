package com.qingcheng.songmod;

import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

import java.util.List;

/**
 * 新版游戏内「点歌台」（1.21.x）。
 *
 * <p>三个页签：
 * <ul>
 *   <li><b>点歌</b>：输入歌名 → 点歌；<b>可拖动的进度条</b>、播放/暂停/停止、倍速档位、
 *       歌词开关（动作条 / 标题 / 聊天）；</li>
 *   <li><b>一起听</b>：列出服务器上正在听歌的玩家，点「加入」就跟听同一首歌；</li>
 *   <li><b>我的</b>：我点过的歌、音量、自己的网易云 Cookie（本地兜底用）。</li>
 * </ul>
 *
 * <p>打开方式：按键 K（可改）或聊天里发 {@code [点歌]台}。
 */
public class SongScreen extends Screen {

    // ---- 配色（ARGB）----
    private static final int PANEL_TOP = 0xF21B2130;
    private static final int PANEL_BOTTOM = 0xF20D1017;
    private static final int HEADER_TOP = 0xFF24313F;
    private static final int HEADER_BOTTOM = 0xFF141A24;
    private static final int ACCENT = 0xFF58A6FF;
    private static final int ACCENT2 = 0xFF7EE787;
    private static final int TEXT = 0xFFE6EDF3;
    private static final int DIM = 0xFF8B949E;
    private static final int ROW_BG = 0x66161C26;
    private static final int ROW_HOVER = 0x8848B4FF;
    private static final int ROW_BG2 = 0x661F2733;

    private static final int TAB_SONG = 0;
    private static final int TAB_LISTEN = 1;
    private static final int TAB_MINE = 2;

    private static final int MAX_LISTEN_ROWS = 6;

    private int tab = TAB_SONG;
    private long lastProgressTouch = 0L;

    private EditBox queryBox;
    private Button sendBtn;
    private Button[] speedBtns;
    private SongSlider progress;
    private Button playPauseBtn;
    private Button stopBtn;
    private Button lyricBtn;

    private Button refreshListenBtn;
    private Button[] joinBtns;

    private SongSlider volumeSlider;
    private EditBox cookieBox;
    private Button saveCookieBtn;

    private Button tabSongBtn;
    private Button tabListenBtn;
    private Button tabMineBtn;
    private Button closeBtn;

    public SongScreen() {
        super(Component.literal("点歌台"));
    }

    // ------------------------------------------------------------------
    // 布局
    // ------------------------------------------------------------------

    private int panelW() {
        return Math.min(360, Math.max(280, this.width - 20));
    }

    private int panelH() {
        return Math.min(268, Math.max(220, this.height - 20));
    }

    private int panelX() {
        return (this.width - panelW()) / 2;
    }

    private int panelY() {
        return (this.height - panelH()) / 2;
    }

    @Override
    protected void init() {
        super.init();
        int px = panelX();
        int py = panelY();
        int pw = panelW();
        int ph = panelH();
        int contentW = pw - 32;

        // 页签
        int tabY = py + 6;
        int tabW = (pw - 60) / 3;
        tabSongBtn = addRenderableWidget(Button.builder(Component.literal("点歌"), b -> setTab(TAB_SONG))
                .bounds(px + 8, tabY, tabW, 18).build());
        tabListenBtn = addRenderableWidget(Button.builder(Component.literal("一起听"), b -> {
            setTab(TAB_LISTEN);
            SongModClient.requestListenList();
        }).bounds(px + 10 + tabW, tabY, tabW, 18).build());
        tabMineBtn = addRenderableWidget(Button.builder(Component.literal("我的"), b -> setTab(TAB_MINE))
                .bounds(px + 12 + tabW * 2, tabY, tabW, 18).build());
        closeBtn = addRenderableWidget(Button.builder(Component.literal("×"), b -> this.onClose())
                .bounds(px + pw - 22, tabY, 16, 18).build());

        // ---- 点歌页 ----
        queryBox = new EditBox(this.font, px + 16, py + 62, contentW - 74, 18,
                Component.literal("歌名 / 网易云链接 / 歌曲ID"));
        queryBox.setMaxLength(120);
        addRenderableWidget(queryBox);
        sendBtn = addRenderableWidget(Button.builder(Component.literal("点歌"), b -> sendRequest())
                .bounds(px + pw - 90, py + 62, 74, 18).build());

        speedBtns = new Button[SongSpeed.SPEEDS.length];
        int speedW = Math.min(52, (contentW - 40 - 4 * 6) / 5);
        for (int i = 0; i < SongSpeed.SPEEDS.length; i++) {
            final float value = SongSpeed.SPEEDS[i];
            speedBtns[i] = addRenderableWidget(Button.builder(
                            Component.literal(SongSpeed.label(value)),
                            b -> {
                                SongModClient.setSpeed(value);
                                SongPlayer.setSpeed(value);
                            })
                    .bounds(px + 40 + i * (speedW + 6), py + 86, speedW, 16).build());
        }

        progress = new SongSlider(px + 16, py + 112, contentW, 18, 0.0,
                (slider, value) -> {
                    if (!SongPlayer.isSeekable()) {
                        return;
                    }
                    lastProgressTouch = System.currentTimeMillis();
                    SongPlayer.seekTo((long) (value * SongPlayer.durationMs()));
                });
        addRenderableWidget(progress);

        playPauseBtn = addRenderableWidget(Button.builder(Component.literal("播放/暂停"),
                b -> SongModClient.togglePause()).bounds(px + 16, py + 142, 84, 18).build());
        stopBtn = addRenderableWidget(Button.builder(Component.literal("停止"),
                b -> SongModClient.requestStop()).bounds(px + 106, py + 142, 60, 18).build());
        lyricBtn = addRenderableWidget(Button.builder(Component.literal("歌词模式"), b -> {
            SongModClient.cycleLyricMode();
        }).bounds(px + 172, py + 142, 96, 18).build());

        // ---- 一起听 ----
        refreshListenBtn = addRenderableWidget(Button.builder(Component.literal("刷新名单"),
                b -> SongModClient.requestListenList()).bounds(px + pw - 96, py + 56, 80, 18).build());
        joinBtns = new Button[MAX_LISTEN_ROWS];
        for (int i = 0; i < MAX_LISTEN_ROWS; i++) {
            final int row = i;
            joinBtns[i] = addRenderableWidget(Button.builder(Component.literal("加入"), b -> joinRow(row))
                    .bounds(px + pw - 76, py + 80 + i * 27, 60, 18).build());
        }

        // ---- 我的 ----
        volumeSlider = new SongSlider(px + 16, py + ph - 96, contentW, 18,
                SongModClient.settings() == null ? 0.8 : SongModClient.settings().volume,
                (slider, value) -> SongModClient.setVolume((float) value));
        addRenderableWidget(volumeSlider);
        cookieBox = new EditBox(this.font, px + 16, py + ph - 62, contentW - 96, 18,
                Component.literal("自己的网易云 Cookie（可选，本地兜底用）"));
        cookieBox.setMaxLength(400);
        cookieBox.setValue(SongModClient.neteaseCookie());
        addRenderableWidget(cookieBox);
        saveCookieBtn = addRenderableWidget(Button.builder(Component.literal("保存 Cookie"), b -> {
            SongModClient.setNeteaseCookie(cookieBox.getValue());
            cookieBox.setValue(SongModClient.neteaseCookie());
        }).bounds(px + pw - 108, py + ph - 62, 92, 18).build());

        setTab(tab);
    }

    private void setTab(int next) {
        tab = next;
        boolean song = tab == TAB_SONG;
        boolean listen = tab == TAB_LISTEN;
        boolean mine = tab == TAB_MINE;
        if (queryBox != null) {
            queryBox.visible = song;
        }
        if (sendBtn != null) {
            sendBtn.visible = song;
        }
        if (speedBtns != null) {
            for (Button b : speedBtns) {
                if (b != null) {
                    b.visible = song;
                }
            }
        }
        if (progress != null) {
            progress.visible = song;
        }
        if (playPauseBtn != null) {
            playPauseBtn.visible = song;
        }
        if (stopBtn != null) {
            stopBtn.visible = song;
        }
        if (lyricBtn != null) {
            lyricBtn.visible = song;
        }
        if (refreshListenBtn != null) {
            refreshListenBtn.visible = listen;
        }
        if (joinBtns != null) {
            for (Button b : joinBtns) {
                if (b != null) {
                    b.visible = listen;
                }
            }
        }
        if (volumeSlider != null) {
            volumeSlider.visible = mine;
        }
        if (cookieBox != null) {
            cookieBox.visible = mine;
        }
        if (saveCookieBtn != null) {
            saveCookieBtn.visible = mine;
        }
    }

    @Override
    public void tick() {
        super.tick();
        try {
            updateWidgets();
        } catch (Throwable ignored) {
            // 界面刷新出错不能崩客户端
        }
    }

    private void updateWidgets() {
        SongConfig cfg = SongModClient.settings();
        long duration = SongPlayer.durationMs();
        long position = SongPlayer.positionMs();
        if (progress != null) {
            boolean seekable = SongPlayer.isSeekable();
            progress.active = seekable;
            if (seekable && duration > 0) {
                if (System.currentTimeMillis() - lastProgressTouch > 600L) {
                    progress.setValueSilently(position / (double) duration);
                }
                progress.setLabel("  " + formatTime(position) + " / " + formatTime(duration)
                        + (SongPlayer.isPaused() ? "   (已暂停)" : ""));
            } else {
                progress.setValueSilently(0.0);
                progress.setLabel(playingStateLabel());
            }
        }
        if (playPauseBtn != null) {
            playPauseBtn.setMessage(Component.literal(SongPlayer.isPaused() ? "继续" : "暂停"));
        }
        if (lyricBtn != null) {
            lyricBtn.setMessage(Component.literal(SongModClient.lyricModeLabel()));
        }
        if (speedBtns != null && cfg != null) {
            for (int i = 0; i < speedBtns.length; i++) {
                if (speedBtns[i] == null) {
                    continue;
                }
                boolean selected = Math.abs(SongSpeed.SPEEDS[i] - cfg.speed) < 0.01f;
                speedBtns[i].setMessage(Component.literal(
                        (selected ? "§b" : "") + SongSpeed.label(SongSpeed.SPEEDS[i])));
            }
        }
        if (volumeSlider != null) {
            float vol = SongPlayer.volume();
            volumeSlider.setValueSilently(vol);
            volumeSlider.setLabel("音量 " + Math.round(vol * 100) + "%");
        }
        if (joinBtns != null) {
            List<SongProtocol.ListenEntry> entries = SongState.listeners;
            for (int i = 0; i < joinBtns.length; i++) {
                if (joinBtns[i] == null) {
                    continue;
                }
                boolean has = entries != null && i < entries.size();
                joinBtns[i].active = has;
                joinBtns[i].setMessage(Component.literal(has ? "一起听" : "-"));
            }
        }
        if (refreshListenBtn != null) {
            refreshListenBtn.active = true;
        }
    }

    private String playingStateLabel() {
        String state = SongPlayer.state();
        if (SongPlayer.LOADING.equals(state)) {
            return "  加载中…";
        }
        if (SongPlayer.ERROR.equals(state)) {
            return "  播放失败";
        }
        if (SongPlayer.PAUSED.equals(state)) {
            return "  已暂停";
        }
        return "  还没有在放歌";
    }

    private void sendRequest() {
        if (queryBox == null) {
            return;
        }
        String query = queryBox.getValue() == null ? "" : queryBox.getValue().trim();
        if (query.isEmpty()) {
            return;
        }
        SongModClient.requestSong(query);
        queryBox.setValue("");
    }

    private void joinRow(int index) {
        List<SongProtocol.ListenEntry> entries = SongState.listeners;
        if (entries == null || index < 0 || index >= entries.size()) {
            return;
        }
        SongProtocol.ListenEntry entry = entries.get(index);
        SongConfig cfg = SongModClient.settings();
        SongModClient.requestJoin(entry.player, cfg == null ? 1f : cfg.speed);
        this.onClose();
    }

    // ------------------------------------------------------------------
    // 绘制
    // ------------------------------------------------------------------

    @Override
    public void renderBackground(GuiGraphics gui, int mouseX, int mouseY, float delta) {
        super.renderBackground(gui, mouseX, mouseY, delta);
        try {
            drawPanel(gui, mouseX, mouseY);
        } catch (Throwable ignored) {
            // ignore
        }
    }

    @Override
    public void render(GuiGraphics gui, int mouseX, int mouseY, float delta) {
        super.render(gui, mouseX, mouseY, delta);
        try {
            drawTexts(gui, mouseX, mouseY);
        } catch (Throwable ignored) {
            // 绘制出错也不能崩
        }
    }

    private void drawPanel(GuiGraphics gui, int mouseX, int mouseY) {
        int px = panelX();
        int py = panelY();
        int pw = panelW();
        int ph = panelH();

        // 外发光 + 阴影
        gui.fill(px - 4, py - 4, px + pw + 4, py + ph + 4, 0x2248B4FF);
        gui.fill(px - 2, py - 2, px + pw + 2, py + ph + 2, 0x88000000);
        // 主体渐变
        gui.fillGradient(px, py, px + pw, py + ph, PANEL_TOP, PANEL_BOTTOM);
        // 顶部条
        gui.fillGradient(px, py, px + pw, py + 26, HEADER_TOP, HEADER_BOTTOM);
        // 强调线
        gui.fill(px, py + 26, px + pw, py + 27, ACCENT);
        gui.fill(px, py, px + 3, py + ph, ACCENT);
        // 四角高光
        gui.fill(px, py + ph - 2, px + pw, py + ph, 0x33000000);

        // 页签高亮
        int tabW = (pw - 60) / 3;
        int tabY = py + 6;
        int activeX = px + 8 + tabW * tab + (tab > 0 ? 2 * tab : 0);
        if (tab == TAB_SONG) {
            activeX = px + 8;
        } else if (tab == TAB_LISTEN) {
            activeX = px + 10 + tabW;
        } else {
            activeX = px + 12 + tabW * 2;
        }
        gui.fill(activeX, tabY + 18, activeX + tabW, tabY + 20, ACCENT);

        if (tab == TAB_SONG) {
            drawSongPage(gui, px, py, pw, ph);
        } else if (tab == TAB_LISTEN) {
            drawListenPage(gui, px, py, pw, ph, mouseX, mouseY);
        } else {
            drawMinePage(gui, px, py, pw, ph);
        }
    }

    private void drawSongPage(GuiGraphics gui, int px, int py, int pw, int ph) {
        int contentW = pw - 32;
        // 分区背景
        gui.fill(px + 10, py + 54, px + pw - 10, py + 82, 0x33FFFFFF);
        gui.fill(px + 10, py + 104, px + pw - 10, py + 134, 0x3310161F);
        gui.fill(px + 10, py + 166, px + pw - 10, py + ph - 10, 0x3310161F);
        // 分区标签
        gui.drawString(this.font, "点歌", px + 16, py + 44, DIM);
        gui.drawString(this.font, "倍速", px + 16, py + 90, DIM);
        gui.drawString(this.font, "歌词", px + 16, py + 196, DIM);
        // 播放进度区
        gui.drawString(this.font, "正在播放", px + 16, py + 46 - 2, DIM);
    }

    private void drawListenPage(GuiGraphics gui, int px, int py, int pw, int ph, int mouseX, int mouseY) {
        gui.fill(px + 10, py + 52, px + pw - 10, py + ph - 10, 0x3310161F);
        gui.drawString(this.font, "一起听 · 服务器上正在听歌的玩家", px + 16, py + 40, DIM);
        List<SongProtocol.ListenEntry> entries = SongState.listeners;
        int rows = entries == null ? 0 : Math.min(entries.size(), MAX_LISTEN_ROWS);
        if (rows == 0) {
            String status = SongState.listenersStatus == null || SongState.listenersStatus.isEmpty()
                    ? "还没有数据，点右上角「刷新名单」" : SongState.listenersStatus;
            gui.drawCenteredString(this.font, status, px + pw / 2, py + 110, DIM);
            return;
        }
        for (int i = 0; i < rows; i++) {
            int rowY = py + 80 + i * 27;
            boolean hover = mouseX >= px + 12 && mouseX <= px + pw - 12
                    && mouseY >= rowY - 3 && mouseY <= rowY + 20;
            gui.fill(px + 12, rowY - 3, px + pw - 12, rowY + 20,
                    hover ? ROW_HOVER : (i % 2 == 0 ? ROW_BG : ROW_BG2));
            SongProtocol.ListenEntry entry = entries.get(i);
            String elapsed = entry.elapsedSeconds >= 0
                    ? formatTime(entry.elapsedSeconds * 1000L) : "--:--";
            gui.drawString(this.font, "§b" + shorten(entry.player, 16), px + 18, rowY + 4, TEXT);
            gui.drawString(this.font, shorten(entry.label, 22), px + 84, rowY + 4, TEXT);
            gui.drawString(this.font, elapsed, px + pw - 150, rowY + 4, ACCENT2);
        }
        gui.drawString(this.font, "点「一起听」会从对方当前进度附近开始播放", px + 16, py + ph - 30, DIM);
    }

    private void drawMinePage(GuiGraphics gui, int px, int py, int pw, int ph) {
        gui.fill(px + 10, py + 52, px + pw - 10, py + ph - 108, 0x3310161F);
        gui.drawString(this.font, "我点过的歌", px + 16, py + 40, DIM);
        List<String> my = SongState.myRequests();
        if (my.isEmpty()) {
            gui.drawCenteredString(this.font, "还没有点过歌", px + pw / 2, py + 100, DIM);
        } else {
            int shown = Math.min(my.size(), 8);
            for (int i = 0; i < shown; i++) {
                gui.drawString(this.font, "§7· §f" + shorten(my.get(i), 34),
                        px + 20, py + 60 + i * 12, TEXT);
            }
        }
        gui.drawString(this.font, "音量", px + 16, py + ph - 106, DIM);
        gui.drawString(this.font, "Cookie 只填自己的（机器人不在线时才用于本地兜底）",
                px + 16, py + ph - 74, DIM);
    }

    private void drawTexts(GuiGraphics gui, int mouseX, int mouseY) {
        int px = panelX();
        int py = panelY();
        int pw = panelW();
        int ph = panelH();

        // 标题
        gui.drawString(this.font, "§b♪ §f点歌台", px + 14, py + 8, TEXT);
        // 状态
        String state = playingStateLabel().trim();
        String speed = SongSpeed.label(SongPlayer.speed());
        String status = "§7" + state + "  §8|  §7倍速 " + speed;
        int statusW = this.font.width(state + "  |  倍速 " + speed);
        gui.drawString(this.font, status, px + pw - 30 - statusW, py + 9, TEXT);

        if (tab == TAB_SONG) {
            // 当前歌名
            String title = SongState.title == null || SongState.title.isEmpty()
                    ? "（空闲）" : SongState.title;
            gui.drawString(this.font, "§f" + shorten(title, 34), px + 16, py + 176, ACCENT2);
            // 歌词
            String line = SongState.lyricLine;
            String next = SongState.nextLyricLine;
            if (line != null && !line.isEmpty()) {
                gui.drawCenteredString(this.font, "§b♪ " + shorten(line, 40),
                        px + pw / 2, py + 196, TEXT);
                if (next != null && !next.isEmpty()) {
                    gui.drawCenteredString(this.font, "§8" + shorten(next, 40),
                            px + pw / 2, py + 208, DIM);
                }
            } else if (SongState.lyricsStatus != null && !SongState.lyricsStatus.isEmpty()) {
                gui.drawCenteredString(this.font, "§8" + Shorten(SongState.lyricsStatus, 40),
                        px + pw / 2, py + 200, DIM);
            }
            gui.drawString(this.font, "§8回车/点「点歌」发送；歌名后加倍速例：起风了 1.5",
                    px + 16, py + ph - 26, DIM);
            gui.drawString(this.font, "§8「一起听」页可跟别人听同一首；K 打开本界面",
                    px + 16, py + ph - 38, DIM);
        } else if (tab == TAB_LISTEN) {
            gui.drawString(this.font, "§8名单来自机器人，约 " + refreshSeconds() + " 秒刷新一次",
                    px + 16, py + ph - 30, DIM);
        } else {
            gui.drawString(this.font, "§8这里只影响自己的客户端；Cookie 会写进 config/songmod.json",
                    px + 16, py + ph - 40, DIM);
        }
    }

    private int refreshSeconds() {
        SongConfig cfg = SongModClient.settings();
        return cfg == null ? 15 : cfg.listenRefreshSeconds;
    }

    private static String Shorten(String text, int max) {
        return shorten(text, max);
    }

    private static String shorten(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "…";
    }

    private static String formatTime(long ms) {
        long total = Math.max(0L, ms) / 1000L;
        return String.format("%d:%02d", total / 60, total % 60);
    }

    @Override
    public boolean isPauseScreen() {
        return false;
    }
}
