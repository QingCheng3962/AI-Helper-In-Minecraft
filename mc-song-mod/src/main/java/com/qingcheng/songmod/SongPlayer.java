package com.qingcheng.songmod;

import javazoom.jl.decoder.Bitstream;
import javazoom.jl.decoder.Decoder;
import javazoom.jl.decoder.Header;
import javazoom.jl.decoder.Obuffer;
import javazoom.jl.decoder.SampleBuffer;

import javax.sound.sampled.AudioFormat;
import javax.sound.sampled.AudioSystem;
import javax.sound.sampled.DataLine;
import javax.sound.sampled.FloatControl;
import javax.sound.sampled.SourceDataLine;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.function.Consumer;

/**
 * 播放引擎：<b>整首下载 + 帧索引</b>，所以支持
 * <ul>
 *   <li>拖动进度条（跳到任意毫秒）；</li>
 *   <li>倍速 0.5 / 0.75 / 1.0 / 1.5 / 2.0（PCM 线性重采样，音调会跟着变，和视频倍速一致）；</li>
 *   <li>暂停 / 继续 / 停止；</li>
 *   <li>精确进度（给进度条和歌词对轴用）。</li>
 * </ul>
 *
 * <p>万一文件不是 MP3（解析不出帧），自动退回 JLayer 流式播放：还能听，但拖不动、不能倍速。
 *
 * <p><b>不依赖 Minecraft 类</b>（方便单测）。
 */
public final class SongPlayer {

    /** 状态：空闲 / 加载中 / 播放中 / 暂停 / 出错。 */
    public static final String IDLE = "idle";
    public static final String LOADING = "loading";
    public static final String PLAYING = "playing";
    public static final String PAUSED = "paused";
    public static final String ERROR = "error";

    private static final Object LOCK = new Object();

    /** 每次 play/stop 递增；旧线程发现代次变了就自己退出。 */
    private static volatile long generation;

    private static volatile Session session;
    private static volatile String state = IDLE;
    private static volatile String lastError = "";

    private static volatile float volume = 0.8f;
    private static volatile float speed = SongSpeed.NORMAL;

    private SongPlayer() {
    }

    // ------------------------------------------------------------------
    // 对外控制
    // ------------------------------------------------------------------

    /** 播放一首歌（先整首下载，再开始出声）。 */
    public static void play(String url, float vol, Consumer<String> onError) {
        play(url, vol, 0L, onError);
    }

    /** 播放一首歌，并从 {@code startMs} 处开始（「一起听」跟播用）。 */
    public static void play(String url, float vol, long startMs, Consumer<String> onError) {
        if (url != null && !url.isEmpty()) {
            lastUrl = url;
        }
        float v = clamp01(vol);
        Session s;
        synchronized (LOCK) {
            generation++;
            closeSession(session);
            volume = v;
            s = new Session(generation);
            s.initialSeekMs = Math.max(0L, startMs);
            session = s;
            state = LOADING;
            lastError = "";
        }
        final Session owned = s;
        Thread t = new Thread(() -> run(owned, url, onError), "songmod-player");
        t.setDaemon(true);
        t.start();
    }

    /** 暂停（进度保留）。 */
    public static void pause() {
        Session s = session;
        if (s != null && s.track != null) {
            s.paused = true;
            state = PAUSED;
            stopLine(s);
        }
    }

    /** 继续。 */
    public static void resume() {
        Session s = session;
        if (s != null && s.paused) {
            s.paused = false;
            state = PLAYING;
            startLine(s);
        }
    }

    public static void togglePause() {
        Session s = session;
        if (s == null) {
            return;
        }
        if (s.paused) {
            resume();
        } else {
            pause();
        }
    }

    /** 停止并清理。 */
    public static void stop() {
        synchronized (LOCK) {
            generation++;
            closeSession(session);
            session = null;
            state = IDLE;
        }
    }

    /** 跳转到指定毫秒（自动夹在 0~时长之间）。 */
    public static void seekTo(long positionMs) {
        Session s = session;
        if (s == null || s.track == null) {
            return;
        }
        long target = Math.max(0L, Math.min(s.track.durationMs - 1, positionMs));
        s.seekMs = target;
        s.positionMs = target;
    }

    public static void setVolume(float vol) {
        volume = clamp01(vol);
        Session s = session;
        if (s != null && s.line != null) {
            applyVolume(s.line, volume);
        }
    }

    private static volatile String lastUrl = "";

    public static void setSpeed(float value) {
        speed = SongSpeed.normalize(value);
    }

    // ------------------------------------------------------------------
    // 对外查询（界面/歌词用）
    // ------------------------------------------------------------------

    public static String state() {
        return state;
    }

    public static String lastError() {
        return lastError;
    }

    public static boolean isPlaying() {
        return PLAYING.equals(state);
    }

    public static boolean isPaused() {
        return PAUSED.equals(state);
    }

    public static boolean isLoading() {
        return LOADING.equals(state);
    }

    /** 当前进度（毫秒）。 */
    public static long positionMs() {
        Session s = session;
        return s == null ? 0L : Math.max(0L, s.positionMs);
    }

    /** 总时长（毫秒）；流式兜底或还没加载好时返回 0。 */
    /** 最近一次播放的直链（进度条跳转失败时，客户端会用它按目标进度重新起播）。 */
    public static String currentUrl() {
        return lastUrl;
    }

    public static long durationMs() {
        Session s = session;
        return s == null || s.track == null ? 0L : s.track.durationMs;
    }

    /** 是否可拖动（整首下载成功才有总时长）。 */
    public static boolean isSeekable() {
        Session s = session;
        return s != null && s.track != null;
    }

    public static float speed() {
        return speed;
    }

    public static float volume() {
        return volume;
    }

    // ------------------------------------------------------------------
    // 播放线程
    // ------------------------------------------------------------------

    private static void run(Session s, String url, Consumer<String> onError) {
        byte[] bytes;
        try {
            bytes = Mp3Track.downloadBytes(url);
        } catch (Throwable e) {
            fail(s, onError, e);
            return;
        }
        if (s.id != generation) {
            return;
        }
        Mp3Track track = null;
        try {
            track = Mp3Track.fromBytes(bytes);
        } catch (IOException ignored) {
            track = null;
        }
        if (track == null) {
            // 解析不出 MP3：退回流式播放（能听，但拖不动、不能倍速）
            streamFallback(s, bytes, onError);
            return;
        }
        s.track = track;
        s.positionMs = 0L;
        // 下载期间用户可能已经拖过进度条：优先用用户拖的，其次才是点歌时的起始位置
        if (s.seekMs >= 0L) {
            s.positionMs = s.seekMs;
        } else if (s.initialSeekMs > 0L) {
            s.seekMs = Math.max(0L, Math.min(track.durationMs - 1, s.initialSeekMs));
            s.positionMs = s.seekMs;
        }
        synchronized (LOCK) {
            if (s.id != generation) {
                return;
            }
            state = PLAYING;
        }
        decodeLoop(s, track, onError);
    }

    /** 主解码循环：从当前帧开始，逐帧解码 → 重采样 → 写音频设备。 */
    private static void decodeLoop(Session s, Mp3Track track, Consumer<String> onError) {
        int frame = 0;
        int resync = 0;
        Decoder decoder = new Decoder();
        ByteArrayInputStream in = new ByteArrayInputStream(track.data,
                track.offsetOf(frame), track.data.length - track.offsetOf(frame));
        Bitstream bitstream = new Bitstream(in);
        try {
            while (s.id == generation && !s.stop) {
                // ① 处理「拖动进度条」
                if (s.seekMs >= 0) {
                    long target = s.seekMs;
                    s.seekMs = -1;
                    frame = track.frameAt(target);
                    bitstream.close();
                    in = new ByteArrayInputStream(track.data,
                            track.offsetOf(frame), track.data.length - track.offsetOf(frame));
                    bitstream = new Bitstream(in);
                    flushLine(s);
                    s.positionMs = track.startOf(frame);
                }
                // ② 暂停：不读帧、停掉音频线路（进度留在原处）
                if (s.paused) {
                    sleepQuietly(20);
                    continue;
                }
                startLine(s);
                Header header;
                try {
                    header = bitstream.readFrame();
                } catch (Throwable e) {
                    header = null;
                }
                if (header == null) {
                    break;                     // 放完了
                }
                Obuffer decoded = null;
                try {
                    decoded = decoder.decodeFrame(header, bitstream);
                    resync = 0;
                } catch (Throwable decodeError) {
                    // 拖动进度条后偶尔会落在坏帧/半帧上：丢掉这一帧、从下一帧重新同步。
                    resync++;
                    try {
                        bitstream.closeFrame();
                    } catch (Throwable ignored) {
                        // ignore
                    }
                    if (resync > 5) {
                        throw decodeError;
                    }
                    frame++;
                    if (frame >= track.offsets.length) {
                        break;
                    }
                    try {
                        bitstream.close();
                    } catch (Throwable ignored) {
                        // ignore
                    }
                    int offset = track.offsetOf(frame);
                    in = new ByteArrayInputStream(track.data, Math.min(offset, track.data.length),
                            Math.max(0, track.data.length - offset));
                    bitstream = new Bitstream(in);
                    s.positionMs = track.startOf(frame);
                    continue;
                } finally {
                    try {
                        bitstream.closeFrame();
                    } catch (Throwable ignored) {
                        // ignore
                    }
                }
                if (!(decoded instanceof SampleBuffer)) {
                    continue;
                }
                SampleBuffer buffer = (SampleBuffer) decoded;
                short[] samples = buffer.getBuffer();
                int len = Math.min(buffer.getBufferLength(), samples == null ? 0 : samples.length);
                int channels = Math.max(1, buffer.getChannelCount());
                int frequency = buffer.getSampleFrequency();
                if (frequency <= 0) {
                    frequency = track.sampleRate;
                }
                if (samples == null || len <= 0) {
                    continue;
                }
                ensureLine(s, frequency, channels);
                short[] pcm = resample(samples, len, channels, speed);
                writeLine(s, pcm);
                frame++;
                s.positionMs = track.startOf(frame);
            }
            // 自然播放结束：把进度停在末尾
            if (s.id == generation && !s.stop) {
                s.positionMs = track.durationMs;
            }
        } catch (Throwable e) {
            fail(s, onError, e);
        } finally {
            try {
                bitstream.close();
            } catch (Throwable ignored) {
                // ignore
            }
            cleanup(s);
        }
    }

    /** 不是 MP3（或解析失败）时的兜底：JLayer 流式播放，没有拖动/倍速。 */
    private static void streamFallback(Session s, byte[] bytes, Consumer<String> onError) {
        synchronized (LOCK) {
            if (s.id != generation) {
                return;
            }
            state = PLAYING;
        }
        try {
            javazoom.jl.player.Player player =
                    new javazoom.jl.player.Player(new ByteArrayInputStream(bytes),
                            new VolumeAudioDevice(volume));
            s.fallbackPlayer = player;
            player.play();
        } catch (Throwable e) {
            fail(s, onError, e);
        } finally {
            cleanup(s);
        }
    }

    private static void fail(Session s, Consumer<String> onError, Throwable e) {
        boolean current;
        synchronized (LOCK) {
            current = s.id == generation;
            if (current) {
                String message = e == null ? "未知错误"
                        : (e.getMessage() == null ? e.toString() : e.getMessage());
                lastError = message;
                state = ERROR;
            }
        }
        if (current && onError != null) {
            String message = e == null ? "未知错误"
                    : (e.getMessage() == null ? e.toString() : e.getMessage());
            onError.accept(message);
        }
    }

    // ------------------------------------------------------------------
    // 音频设备
    // ------------------------------------------------------------------

    private static void ensureLine(Session s, int frequency, int channels) throws Exception {
        SourceDataLine line = s.line;
        if (line != null && line.isOpen()
                && s.sampleRate == frequency && s.channels == channels) {
            return;
        }
        closeLine(s);
        AudioFormat format = new AudioFormat(frequency, 16, channels, true, false);
        DataLine.Info info = new DataLine.Info(SourceDataLine.class, format);
        SourceDataLine created;
        if (AudioSystem.isLineSupported(info)) {
            created = (SourceDataLine) AudioSystem.getLine(info);
        } else {
            created = AudioSystem.getSourceDataLine(format);
        }
        created.open(format, 1 << 16);
        s.sampleRate = frequency;
        s.channels = channels;
        applyVolume(created, volume);
        if (!s.paused && !s.stop) {
            created.start();
        }
        s.line = created;
    }

    private static void writeLine(Session s, short[] pcm) {
        SourceDataLine line = s.line;
        if (line == null || pcm.length == 0) {
            return;
        }
        byte[] bytes = toByteArray(pcm);
        try {
            line.write(bytes, 0, bytes.length);
        } catch (Throwable ignored) {
            // 设备被关掉（停止/切歌）时 write 会抛，这里直接忽略
        }
    }

    private static short[] resample(short[] samples, int len, int channels, float rate) {
        if (samples == null || len <= 0) {
            return new short[0];
        }
        len = Math.min(len, samples.length);
        channels = Math.max(1, channels);
        if (rate <= 0f || Math.abs(rate - 1f) < 0.001f) {
            short[] copy = new short[len];
            System.arraycopy(samples, 0, copy, 0, len);
            return copy;
        }
        int frames = len / channels;
        if (frames <= 0) {
            return new short[0];
        }
        int outFrames = Math.max(1, Math.round(frames * rate));
        short[] out = new short[outFrames * channels];
        for (int f = 0; f < outFrames; f++) {
            float src = f / rate;
            int i0 = (int) src;
            if (i0 < 0) {
                i0 = 0;
            }
            if (i0 >= frames) {
                i0 = frames - 1;
            }
            int i1 = Math.min(frames - 1, i0 + 1);
            float frac = src - i0;
            if (frac < 0f) {
                frac = 0f;
            }
            for (int c = 0; c < channels; c++) {
                int idx0 = i0 * channels + c;
                int idx1 = i1 * channels + c;
                if (idx0 >= samples.length) {
                    idx0 = samples.length - 1;
                }
                if (idx1 >= samples.length) {
                    idx1 = samples.length - 1;
                }
                short a = samples[idx0];
                short b = samples[idx1];
                int idx = f * channels + c;
                if (idx >= out.length) {
                    break;
                }
                out[idx] = (short) (a + (b - a) * frac);
            }
        }
        return out;
    }

    private static byte[] toByteArray(short[] samples) {
        byte[] out = new byte[samples.length * 2];
        int idx = 0;
        for (short sample : samples) {
            out[idx++] = (byte) (sample & 0xff);
            out[idx++] = (byte) ((sample >>> 8) & 0xff);
        }
        return out;
    }

    private static void applyVolume(SourceDataLine line, float vol) {
        try {
            FloatControl gain = (FloatControl) line.getControl(FloatControl.Type.MASTER_GAIN);
            float v = Math.max(0.0001f, clamp01(vol));
            float db = (float) (20.0 * Math.log10(v));
            db = Math.max(gain.getMinimum(), Math.min(gain.getMaximum(), db));
            gain.setValue(db);
        } catch (Exception ignored) {
            // 设备没有主音量控制
        }
    }

    private static void startLine(Session s) {
        SourceDataLine line = s.line;
        if (line != null && line.isOpen() && !line.isRunning()) {
            try {
                line.start();
            } catch (Throwable ignored) {
                // ignore
            }
        }
    }

    private static void stopLine(Session s) {
        SourceDataLine line = s.line;
        if (line != null && line.isOpen()) {
            try {
                line.stop();
                line.flush();
            } catch (Throwable ignored) {
                // ignore
            }
        }
    }

    private static void flushLine(Session s) {
        SourceDataLine line = s.line;
        if (line != null && line.isOpen()) {
            try {
                line.flush();
            } catch (Throwable ignored) {
                // ignore
            }
        }
    }

    private static void closeLine(Session s) {
        SourceDataLine line = s.line;
        s.line = null;
        if (line != null) {
            try {
                line.stop();
                line.close();
            } catch (Throwable ignored) {
                // ignore
            }
        }
    }

    private static void closeSession(Session s) {
        if (s == null) {
            return;
        }
        s.stop = true;
        closeLine(s);
        javazoom.jl.player.Player p = s.fallbackPlayer;
        if (p != null) {
            try {
                p.close();
            } catch (Throwable ignored) {
                // ignore
            }
        }
    }

    private static void cleanup(Session s) {
        closeSession(s);
        synchronized (LOCK) {
            if (s.id == generation && state != ERROR) {
                state = IDLE;
            }
        }
    }

    private static void sleepQuietly(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static float clamp01(float value) {
        if (Float.isNaN(value)) {
            return 1f;
        }
        return Math.max(0f, Math.min(1f, value));
    }

    /** 一次播放会话（每次 play 一个）。 */
    private static final class Session {
        final long id;
        volatile Mp3Track track;
        volatile javazoom.jl.player.Player fallbackPlayer;
        volatile SourceDataLine line;
        volatile boolean paused;
        volatile boolean stop;
        volatile long seekMs = -1L;
        volatile long initialSeekMs = 0L;
        volatile long positionMs;
        volatile int sampleRate;
        volatile int channels;

        Session(long id) {
            this.id = id;
        }
    }
}
