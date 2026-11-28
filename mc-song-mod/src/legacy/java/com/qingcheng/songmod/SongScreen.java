package com.qingcheng.songmod;

import net.minecraft.client.gui.GuiGraphics;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

/**
 * 点歌台（1.21.x）：单页界面。
 *
 * <p>只保留点歌页（我的/一起听页已删除）：歌名输入 / 点歌、倍速、进度条、暂停继续、停止、歌词。
 * 进度条用 {@link SongSlider}（继承 AbstractSliderButton，各版本鼠标处理由 MC 自己实现），
 * 拖动后直接 seek，并在 1.5 秒内不被自动刷新覆盖，必要时自动按目标进度重新起播。
 */
public class SongScreen extends Screen {

    private static final int PANEL_TOP = 0xF21B2130;
    private static final int PANEL_BOTTOM = 0xF20D1017;
    private static final int HEADER_TOP = 0xFF24313F;
    private static final int HEADER_BOTTOM = 0xFF141A24;
    private static final int ACCENT = 0xFF58A6FF;
    private static final int ACCENT2 = 0xFF7EE787;
    private static final int TEXT = 0xFFE6EDF3;
    private static final int DIM = 0xFF8B949E;

    private EditBox queryBox;
    private Button sendBtn;
    private Button[] speedBtns;
    private SongSlider progress;
    private Button playPauseBtn;
    private Button stopBtn;
    private Button lyricBtn;
    private Button closeBtn;

    private long lastProgressTouch = 0L;

    public SongScreen() {
        super(Component.literal("点歌台"));
    }

    private int panelW() {
        return Math.min(420, Math.max(340, this.width - 24));
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
        int cw = contentW();

        queryBox = new EditBox(this.font, px + 56, py + 42, cw - 140, 18,
                Component.literal("歌名 / 网易云链接 / 歌曲ID"));
        queryBox.setMaxLength(120);
        addRenderableWidget(queryBox);
        sendBtn = addRenderableWidget(Button.builder(Component.literal("点歌"), b -> sendRequest())
                .bounds(px + pw - 92, py + 42, 76, 18).build());

        float[] speeds = SongSpeed.SPEEDS;
        speedBtns = new Button[speeds.length];
        int speedW = Math.min(50, (cw - 40 - 4 * 6) / 5);
        for (int i = 0; i < speeds.length; i++) {
            final float value = speeds[i];
            speedBtns[i] = addRenderableWidget(Button.builder(
                            Component.literal(SongSpeed.label(value)),
                            b -> {
                                SongModClient.setSpeed(value);
                                SongPlayer.setSpeed(value);
                            })
                    .bounds(px + 56 + i * (speedW + 6), py + 66, speedW, 16).build());
        }

        progress = new SongSlider(px + 16, py + 100, cw, 18, 0.0,
                (slider, value) -> {
                    lastProgressTouch = System.currentTimeMillis();
                    long duration = SongPlayer.durationMs();
                    if (duration <= 0L) {
                        return;
                    }
                    double ratio = Math.max(0.0, Math.min(1.0, value));
                    SongModClient.requestSeek((long) (ratio * duration));
                });
        addRenderableWidget(progress);

        playPauseBtn = addRenderableWidget(Button.builder(Component.literal("暂停"),
                b -> SongModClient.togglePause()).bounds(px + 16, py + 126, 84, 18).build());
        stopBtn = addRenderableWidget(Button.builder(Component.literal("停止"),
                b -> SongModClient.requestStop()).bounds(px + 106, py + 126, 60, 18).build());
        lyricBtn = addRenderableWidget(Button.builder(Component.literal("歌词模式"), b -> {
            SongModClient.cycleLyricMode();
        }).bounds(px + 172, py + 126, 104, 18).build());
        closeBtn = addRenderableWidget(Button.builder(Component.literal("×"), b -> this.onClose())
                .bounds(px + pw - 26, py + 6, 18, 18).build());
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

    @Override
    public void tick() {
        super.tick();
        try {
            updateWidgets();
            SongModClient.tickSeekFallback();
        } catch (Throwable ignored) {
            // ignore
        }
    }

    private void updateWidgets() {
        SongConfig cfg = SongModClient.settings();
        long duration = SongPlayer.durationMs();
        long position = SongPlayer.positionMs();
        if (progress != null) {
            boolean seekable = duration > 0;
            progress.active = seekable;
            if (seekable) {
                if (System.currentTimeMillis() - lastProgressTouch > 2500L) {
                    progress.setValueSilently(position / (double) duration);
                }
                progress.setLabel("  " + formatTime(position) + " / " + formatTime(duration)
                        + (SongPlayer.isPaused() ? "   (已暂停)" : ""));
            } else {
                progress.setValueSilently(0.0);
                progress.setLabel(SongPlayer.isLoading()
                        ? "  加载中…（下载完就能拖）" : "  还没有在放歌");
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
    }

    // ------------------------------------------------------------------
    // 绘制
    // ------------------------------------------------------------------
    @Override
    public void renderBackground(GuiGraphics gui, int mouseX, int mouseY, float delta) {
        super.renderBackground(gui, mouseX, mouseY, delta);
        try {
            drawPanel(gui);
        } catch (Throwable ignored) {
            // ignore
        }
    }

    @Override
    public void render(GuiGraphics gui, int mouseX, int mouseY, float delta) {
        super.render(gui, mouseX, mouseY, delta);
        try {
            drawTexts(gui);
        } catch (Throwable ignored) {
            // ignore
        }
    }

    private void drawPanel(GuiGraphics gui) {
        int px = panelX();
        int py = panelY();
        int pw = panelW();
        int ph = panelH();
        gui.fill(px - 4, py - 4, px + pw + 4, py + ph + 4, 0x2248B4FF);
        gui.fill(px - 2, py - 2, px + pw + 2, py + ph + 2, 0x88000000);
        gui.fillGradient(px, py, px + pw, py + ph, PANEL_TOP, PANEL_BOTTOM);
        gui.fillGradient(px, py, px + pw, py + 34, HEADER_TOP, HEADER_BOTTOM);
        gui.fill(px, py + 34, px + pw, py + 35, ACCENT);
        gui.fill(px, py, px + 3, py + ph, ACCENT);
        gui.fill(px + 12, py + 38, px + pw - 12, py + 62, 0x22FFFFFF);
        gui.fill(px + 12, py + 92, px + pw - 12, py + 122, 0x33FFFFFF);
        gui.fill(px + 12, py + 148, px + pw - 12, py + ph - 22, 0x2210161F);
    }

    private void drawTexts(GuiGraphics gui) {
        int px = panelX();
        int py = panelY();
        int pw = panelW();
        int ph = panelH();

        gui.drawString(this.font, "§b♪ §f点歌台", px + 14, py + 8, TEXT);
        String state = SongPlayer.state();
        String speed = SongSpeed.label(SongPlayer.speed());
        String label = stateLabel(state) + "  |  倍速 " + speed;
        gui.drawString(this.font, "§7" + label, px + pw - 34 - this.font.width(label), py + 9, TEXT);

        gui.drawString(this.font, "点歌", px + 16, py + 47, DIM);
        gui.drawString(this.font, "倍速", px + 16, py + 70, DIM);

        String title = SongState.title == null || SongState.title.isEmpty()
                ? "（空闲）" : SongState.title;
        gui.drawString(this.font, "§f" + shorten(title, 44), px + 16, py + 152, ACCENT2);
        String line = SongState.lyricLine;
        String next = SongState.nextLyricLine;
        if (line != null && !line.isEmpty()) {
            gui.drawCenteredString(this.font, "§b♪ " + shorten(line, 46),
                    px + pw / 2, py + 172, TEXT);
            if (next != null && !next.isEmpty()) {
                gui.drawCenteredString(this.font, "§8" + shorten(next, 46),
                        px + pw / 2, py + 186, DIM);
            }
        } else if (SongState.lyricsStatus != null && !SongState.lyricsStatus.isEmpty()) {
            gui.drawCenteredString(this.font, "§8" + shorten(SongState.lyricsStatus, 46),
                    px + pw / 2, py + 176, DIM);
        }
        gui.drawString(this.font, "§8歌名后加倍速例：起风了 1.5 · 聊天 [点歌]歌名 也可点歌 · K 打开",
                px + 16, py + ph - 16, DIM);
    }

    private static String stateLabel(String state) {
        if (SongPlayer.LOADING.equals(state)) return "加载中";
        if (SongPlayer.ERROR.equals(state)) return "播放失败";
        if (SongPlayer.PAUSED.equals(state)) return "已暂停";
        if (SongPlayer.PLAYING.equals(state)) return "播放中";
        return "空闲";
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
