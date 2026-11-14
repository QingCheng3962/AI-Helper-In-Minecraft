package com.qingcheng.songmod;

import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

import java.util.List;

/**
 * 新版游戏内「点歌台」（Minecraft 26.x）。
 *
 * <p>布局按「宽松、不拥挤」重做（每个区块独占一行）。26.x 绘制走 extractRenderState 提取式渲染：
 * extractBackground 画面板（控件下面），extractRenderState 叠加文字（控件上面）。
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
    private static final int MAX_LISTEN_ROWS = 5;

    private int tab = TAB_SONG;
    private long lastProgressTouch = 0L;

    private EditBox queryBox;
    private Button sendBtn;
    private Button[] speedBtns;
    /** 倍速按钮从大到小显示（2.0 在最左），和常见播放器一致。 */
    private final float[] speedValues = new float[SongSpeed.SPEEDS.length];
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
        float[] speeds = SongSpeed.SPEEDS;
        for (int i = 0; i < speeds.length; i++) {
            speedValues[i] = speeds[speeds.length - 1 - i];   // 2.0 → 0.5
        }
    }

    // ------------------------------------------------------------------
    // 布局
    // ------------------------------------------------------------------
    private int panelW() {
        return Math.min(400, Math.max(320, this.width - 24));
    }

    private int panelH() {
        return Math.min(300, Math.max(250, this.height - 24));
    }

    private int panelX() {
        return (this.width - panelW()) / 2;
    }

    private int panelY() {
        return (this.height - panelH()) / 2;
    }

    private int contentW() {
        return panelW() - 32;
    }

    @Override
    protected void init() {
        super.init();
        int px = panelX();
        int py = panelY();
        int pw = panelW();
        int ph = panelH();
        int cw = contentW();

        // ---- 顶部：页签 ----
        int tabY = py + 28;
        int tabW = (pw - 70) / 3;
        tabSongBtn = addRenderableWidget(Button.builder(Component.literal("点歌"), b -> setTab(TAB_SONG))
                .bounds(px + 12, tabY, tabW, 18).build());
        tabListenBtn = addRenderableWidget(Button.builder(Component.literal("一起听"), b -> {
            setTab(TAB_LISTEN);
            SongModClient.requestListenList();
        }).bounds(px + 18 + tabW, tabY, tabW, 18).build());
        tabMineBtn = addRenderableWidget(Button.builder(Component.literal("我的"), b -> setTab(TAB_MINE))
                .bounds(px + 24 + tabW * 2, tabY, tabW, 18).build());
        closeBtn = addRenderableWidget(Button.builder(Component.literal("×"), b -> this.onClose())
                .bounds(px + pw - 26, py + 6, 18, 18).build());

        // ---- 点歌页 ----
        queryBox = new EditBox(this.font, px + 56, py + 56, cw - 140, 18,
                Component.literal("歌名 / 网易云链接 / 歌曲ID"));
        queryBox.setMaxLength(120);
        addRenderableWidget(queryBox);
        sendBtn = addRenderableWidget(Button.builder(Component.literal("点歌"), b -> sendRequest())
                .bounds(px + pw - 92, py + 56, 76, 18).build());

        speedBtns = new Button[speedValues.length];
        int speedW = Math.min(46, (cw - 40 - 4 * 6) / 5);
        for (int i = 0; i < speedValues.length; i++) {
            final float value = speedValues[i];
            speedBtns[i] = addRenderableWidget(Button.builder(
                            Component.literal(SongSpeed.label(value)),
                            b -> {
                                SongModClient.setSpeed(value);
                                SongPlayer.setSpeed(value);
                            })
                    .bounds(px + 56 + i * (speedW + 6), py + 80, speedW, 16).build());
        }

        progress = new SongSlider(px + 16, py + 108, cw, 18, 0.0,
                (slider, value) -> {
                    if (!SongPlayer.isSeekable()) {
                        return;
                    }
                    lastProgressTouch = System.currentTimeMillis();
                    SongPlayer.seekTo((long) (value * SongPlayer.durationMs()));
                });
        addRenderableWidget(progress);

        playPauseBtn = addRenderableWidget(Button.builder(Component.literal("暂停"),
                b -> SongModClient.togglePause()).bounds(px + 16, py + 136, 80, 18).build());
        stopBtn = addRenderableWidget(Button.builder(Component.literal("停止"),
                b -> SongModClient.requestStop()).bounds(px + 102, py + 136, 60, 18).build());
        lyricBtn = addRenderableWidget(Button.builder(Component.literal("歌词模式"), b -> {
            SongModClient.cycleLyricMode();
        }).bounds(px + 168, py + 136, 104, 18).build());

        // ---- 一起听页 ----
        refreshListenBtn = addRenderableWidget(Button.builder(Component.literal("刷新名单"),
                b -> SongModClient.requestListenList()).bounds(px + pw - 100, py + 56, 84, 18).build());
        joinBtns = new Button[MAX_LISTEN_ROWS];
        for (int i = 0; i < MAX_LISTEN_ROWS; i++) {
            final int row = i;
            joinBtns[i] = addRenderableWidget(Button.builder(Component.literal("一起听"), b -> joinRow(row))
                    .bounds(px + pw - 84, py + 104 + i * 28, 68, 18).build());
        }

        // ---- 我的页 ----
        volumeSlider = new SongSlider(px + 56, py + ph - 92, cw - 40, 18,
                SongModClient.settings() == null ? 0.8 : SongModClient.settings().volume,
                (slider, value) -> SongModClient.setVolume((float) value));
        addRenderableWidget(volumeSlider);
        cookieBox = new EditBox(this.font, px + 56, py + ph - 58, cw - 96, 18,
                Component.literal("自己的网易云 Cookie（可选）"));
        cookieBox.setMaxLength(400);
        cookieBox.setValue(SongModClient.neteaseCookie());
        addRenderableWidget(cookieBox);
        saveCookieBtn = addRenderableWidget(Button.builder(Component.literal("保存 Cookie"), b -> {
            SongModClient.setNeteaseCookie(cookieBox.getValue());
            cookieBox.setValue(SongModClient.neteaseCookie());
        }).bounds(px + pw - 104, py + ph - 59, 88, 18).build());

        setTab(tab);
    }

    private void setTab(int next) {
        tab = next;
        boolean song = tab == TAB_SONG;
        boolean listen = tab == TAB_LISTEN;
        boolean mine = tab == TAB_MINE;
        if (queryBox != null) queryBox.visible = song;
        if (sendBtn != null) sendBtn.visible = song;
        if (speedBtns != null) for (Button b : speedBtns) if (b != null) b.visible = song;
        if (progress != null) progress.visible = song;
        if (playPauseBtn != null) playPauseBtn.visible = song;
        if (stopBtn != null) stopBtn.visible = song;
        if (lyricBtn != null) lyricBtn.visible = song;
        if (refreshListenBtn != null) refreshListenBtn.visible = listen;
        if (joinBtns != null) for (Button b : joinBtns) if (b != null) b.visible = listen;
        if (volumeSlider != null) volumeSlider.visible = mine;
        if (cookieBox != null) cookieBox.visible = mine;
        if (saveCookieBtn != null) saveCookieBtn.visible = mine;
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
                boolean selected = Math.abs(speedValues[i] - cfg.speed) < 0.01f;
                speedBtns[i].setMessage(Component.literal(
                        (selected ? "§b" : "") + SongSpeed.label(speedValues[i])));
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
    }

    private String playingStateLabel() {
        String state = SongPlayer.state();
        if (SongPlayer.LOADING.equals(state)) return "  加载中…";
        if (SongPlayer.ERROR.equals(state)) return "  播放失败";
        if (SongPlayer.PAUSED.equals(state)) return "  已暂停";
        return "  还没有在放歌";
    }

    private void sendRequest() {
        if (queryBox == null) return;
        String query = queryBox.getValue() == null ? "" : queryBox.getValue().trim();
        if (query.isEmpty()) return;
        SongModClient.requestSong(query);
        queryBox.setValue("");
    }

    private void joinRow(int index) {
        List<SongProtocol.ListenEntry> entries = SongState.listeners;
        if (entries == null || index < 0 || index >= entries.size()) return;
        SongProtocol.ListenEntry entry = entries.get(index);
        SongConfig cfg = SongModClient.settings();
        SongModClient.requestJoin(entry.player, cfg == null ? 1f : cfg.speed);
        this.onClose();
    }

    // ------------------------------------------------------------------
    // 绘制
    // ------------------------------------------------------------------
    @Override
    public void extractBackground(GuiGraphicsExtractor gui, int mouseX, int mouseY, float delta) {
        super.extractBackground(gui, mouseX, mouseY, delta);
        try {
            drawPanel(gui, mouseX, mouseY);
        } catch (Throwable ignored) {
            // ignore
        }
    }

    @Override
    public void extractRenderState(GuiGraphicsExtractor gui, int mouseX, int mouseY, float delta) {
        super.extractRenderState(gui, mouseX, mouseY, delta);
        try {
            drawTexts(gui);
        } catch (Throwable ignored) {
            // 绘制出错也不能崩
        }
    }

    private void drawPanel(GuiGraphicsExtractor gui, int mouseX, int mouseY) {
        int px = panelX();
        int py = panelY();
        int pw = panelW();
        int ph = panelH();

        gui.fill(px - 4, py - 4, px + pw + 4, py + ph + 4, 0x2248B4FF);
        gui.fill(px - 2, py - 2, px + pw + 2, py + ph + 2, 0x88000000);
        gui.fillGradient(px, py, px + pw, py + ph, PANEL_TOP, PANEL_BOTTOM);
        gui.fillGradient(px, py, px + pw, py + 48, HEADER_TOP, HEADER_BOTTOM);
        gui.fill(px, py + 48, px + pw, py + 49, ACCENT);
        gui.fill(px, py, px + 3, py + ph, ACCENT);

        // 页签下划线
        int tabW = (pw - 70) / 3;
        int tabY = py + 28;
        int activeX = px + 12 + tab * (tabW + 6) + tab * 2;
        gui.fill(activeX, tabY + 18, activeX + tabW, tabY + 20, ACCENT);

        if (tab == TAB_SONG) {
            drawSongPage(gui, px, py, pw, ph);
        } else if (tab == TAB_LISTEN) {
            drawListenPage(gui, px, py, pw, ph, mouseX, mouseY);
        } else {
            drawMinePage(gui, px, py, pw, ph);
        }
    }

    private void drawSongPage(GuiGraphicsExtractor gui, int px, int py, int pw, int ph) {
        int cw = contentW();
        // 分区背板（只做轻微提示，不给每行都画，避免拥挤）
        gui.fill(px + 12, py + 52, px + pw - 12, py + 78, 0x22FFFFFF);
        gui.fill(px + 12, py + 102, px + pw - 12, py + 132, 0x33FFFFFF);
        gui.fill(px + 12, py + 160, px + pw - 12, py + ph - 20, 0x2210161F);
        gui.text(this.font, "点歌", px + 16, py + 61, DIM);
        gui.text(this.font, "倍速", px + 16, py + 84, DIM);
        gui.text(this.font, "正在播放", px + 16, py + 112, DIM);
    }

    private void drawListenPage(GuiGraphicsExtractor gui, int px, int py, int pw, int ph, int mouseX, int mouseY) {
        gui.fill(px + 12, py + 52, px + pw - 12, py + ph - 20, 0x2210161F);
        gui.text(this.font, "一起听 · 只显示正在播放的玩家", px + 16, py + 61, DIM);
        List<SongProtocol.ListenEntry> entries = SongState.listeners;
        int rows = entries == null ? 0 : Math.min(entries.size(), MAX_LISTEN_ROWS);
        if (rows == 0) {
            String status = SongState.listenersStatus == null || SongState.listenersStatus.isEmpty()
                    ? "现在没有人在听歌（或都放完了）" : SongState.listenersStatus;
            gui.centeredText(this.font, status, px + pw / 2, py + 130, DIM);
            return;
        }
        for (int i = 0; i < rows; i++) {
            int rowY = py + 104 + i * 28;
            boolean hover = mouseX >= px + 14 && mouseX <= px + pw - 14
                    && mouseY >= rowY - 4 && mouseY <= rowY + 21;
            gui.fill(px + 14, rowY - 4, px + pw - 14, rowY + 21,
                    hover ? ROW_HOVER : (i % 2 == 0 ? ROW_BG : ROW_BG2));
            SongProtocol.ListenEntry entry = entries.get(i);
            String elapsed = entry.elapsedSeconds >= 0
                    ? formatTime(entry.elapsedSeconds * 1000L) : "--:--";
            gui.text(this.font, "§b" + shorten(entry.player, 16), px + 20, rowY + 3, TEXT);
            gui.text(this.font, shorten(entry.label, 18), px + 96, rowY + 3, TEXT);
            gui.text(this.font, elapsed, px + pw - 160, rowY + 3, ACCENT2);
        }
    }

    private void drawMinePage(GuiGraphicsExtractor gui, int px, int py, int pw, int ph) {
        gui.fill(px + 12, py + 52, px + pw - 12, py + ph - 110, 0x2210161F);
        gui.text(this.font, "我点过的歌", px + 16, py + 61, DIM);
        List<String> my = SongState.myRequests();
        if (my.isEmpty()) {
            gui.centeredText(this.font, "还没有点过歌", px + pw / 2, py + 110, DIM);
        } else {
            int shown = Math.min(my.size(), 8);
            for (int i = 0; i < shown; i++) {
                gui.text(this.font, "§7· §f" + shorten(my.get(i), 34),
                        px + 20, py + 82 + i * 14, TEXT);
            }
        }
        gui.text(this.font, "音量", px + 16, py + ph - 84, DIM);
        gui.text(this.font, "Cookie（本地兜底用）", px + 16, py + ph - 54, DIM);
    }

    private void drawTexts(GuiGraphicsExtractor gui) {
        int px = panelX();
        int py = panelY();
        int pw = panelW();
        int ph = panelH();

        gui.text(this.font, "§b♪ §f点歌台", px + 14, py + 8, TEXT);
        String state = playingStateLabel().trim();
        String speed = SongSpeed.label(SongPlayer.speed());
        String status = "§7" + state + "  §8|  §7倍速 " + speed;
        int statusW = this.font.width(state + "  |  倍速 " + speed);
        gui.text(this.font, status, px + pw - 34 - statusW, py + 9, TEXT);

        if (tab == TAB_SONG) {
            String title = SongState.title == null || SongState.title.isEmpty()
                    ? "（空闲）" : SongState.title;
            gui.text(this.font, "§f" + shorten(title, 40), px + 16, py + 166, ACCENT2);
            String line = SongState.lyricLine;
            String next = SongState.nextLyricLine;
            if (line != null && !line.isEmpty()) {
                gui.centeredText(this.font, "§b♪ " + shorten(line, 44),
                        px + pw / 2, py + 186, TEXT);
                if (next != null && !next.isEmpty()) {
                    gui.centeredText(this.font, "§8" + shorten(next, 44),
                            px + pw / 2, py + 200, DIM);
                }
            } else if (SongState.lyricsStatus != null && !SongState.lyricsStatus.isEmpty()) {
                gui.centeredText(this.font, "§8" + shorten(SongState.lyricsStatus, 44),
                        px + pw / 2, py + 190, DIM);
            }
            gui.text(this.font, "§8歌名后加倍速例：起风了 1.5 · 按 K 打开本界面",
                    px + 16, py + ph - 16, DIM);
        } else if (tab == TAB_LISTEN) {
            gui.text(this.font, "§8名单来自机器人，只显示正在播放的玩家，约 "
                            + refreshSeconds() + " 秒可刷新一次",
                    px + 16, py + ph - 16, DIM);
        } else {
            gui.text(this.font, "§8这里只影响自己的客户端；Cookie 会写进 config/songmod.json",
                    px + 16, py + ph - 16, DIM);
        }
    }

    private int refreshSeconds() {
        SongConfig cfg = SongModClient.settings();
        return cfg == null ? 15 : cfg.listenRefreshSeconds;
    }

    private static String shorten(String text, int max) {
        if (text == null) return "";
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
