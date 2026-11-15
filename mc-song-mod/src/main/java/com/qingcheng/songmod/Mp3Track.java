package com.qingcheng.songmod;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 一首 MP3 的完整数据 + 帧索引（时间轴）。
 *
 * <p>为什么整首下载：<b>拖动进度条</b>和<b>倍速</b>都需要随机访问 PCM/帧。流式播放
 * 只能顺序读，拖不动。整首下载到内存（一首歌通常 3~10 MB）就都简单了：
 * <ul>
 *   <li>跳转 = 换一个 {@code ByteArrayInputStream} 的起始偏移；</li>
 *   <li>时长 = 逐帧扫出来的精确值（VBR 也准）；</li>
 *   <li>进度 = 当前帧的累计毫秒。</li>
 * </ul>
 *
 * <p><b>不依赖 Minecraft 类</b>（方便单测）。
 */
public final class Mp3Track {

    private static final String UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";

    /** 单曲大小上限（防止被某个超大文件打爆内存）。 */
    private static final int MAX_BYTES = 40 << 20;

    /** 音频数据。 */
    public final byte[] data;
    /** 每一帧在 {@link #data} 里的起始偏移。 */
    public final int[] offsets;
    /** 每一帧的字节长度。 */
    public final int[] lengths;
    /** 每一帧的起始时间（毫秒）。 */
    public final long[] startsMs;
    /** 总时长（毫秒）。 */
    public final long durationMs;
    /** 采样率（Hz）。 */
    public final int sampleRate;

    private Mp3Track(byte[] data, int[] offsets, int[] lengths, long[] startsMs,
                     long durationMs, int sampleRate) {
        this.data = data;
        this.offsets = offsets;
        this.lengths = lengths;
        this.startsMs = startsMs;
        this.durationMs = durationMs;
        this.sampleRate = sampleRate;
    }

    /** 下载整首歌并建索引（失败抛 {@link IOException}）。 */
    public static Mp3Track download(String url) throws IOException {
        return fromBytes(downloadBytes(url));
    }

    /** 只下载音频字节（解析失败时上层还能退回流式播放）。 */
    public static byte[] downloadBytes(String url) throws IOException {
        URL finalUrl = resolveFinalUrl(url);
        InputStream in = openStream(finalUrl);
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream(1 << 20);
            byte[] buf = new byte[1 << 16];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > MAX_BYTES) {
                    throw new IOException("音频文件太大（超过 " + (MAX_BYTES >> 20) + " MB）");
                }
            }
            return out.toByteArray();
        } finally {
            try {
                in.close();
            } catch (IOException ignored) {
                // ignore
            }
        }
    }

    /** 手动跟随 3xx 跳转（HTTP / HTTPS 都跟），返回最终地址。 */
    public static URL resolveFinalUrl(String url) throws IOException {
        URL current = new URL(url);
        for (int i = 0; i < 6; i++) {
            HttpURLConnection conn = (HttpURLConnection) current.openConnection();
            conn.setInstanceFollowRedirects(false);
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(8000);
            conn.setReadTimeout(15000);
            conn.setRequestProperty("User-Agent", UA);
            conn.setRequestProperty("Referer", "https://music.163.com/");
            int code = conn.getResponseCode();
            if (code >= 300 && code < 400) {
                String location = conn.getHeaderField("Location");
                conn.disconnect();
                if (location == null || location.isEmpty()) {
                    break;
                }
                current = new URL(current, location);
                continue;
            }
            conn.disconnect();
            break;
        }
        return current;
    }

    private static InputStream openStream(URL url) throws IOException {
        try {
            return doOpen(url);
        } catch (IOException primary) {
            if ("http".equalsIgnoreCase(url.getProtocol())) {
                // 有的 CDN 也支持 https，http 打不开时回退 https 再试一次。
                try {
                    URL https = new URL("https", url.getHost(), url.getPort(), url.getFile());
                    return doOpen(https);
                } catch (IOException ignored) {
                    throw primary;
                }
            }
            throw primary;
        }
    }

    private static InputStream doOpen(URL url) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setInstanceFollowRedirects(true);
        conn.setConnectTimeout(8000);
        conn.setReadTimeout(15000);
        conn.setRequestProperty("User-Agent", UA);
        conn.setRequestProperty("Referer", "https://music.163.com/");
        conn.setRequestProperty("Accept", "*/*");
        int code = conn.getResponseCode();
        if (code >= 400) {
            conn.disconnect();
            throw new IOException("HTTP " + code + "（" + url.getHost() + "）");
        }
        return conn.getInputStream();
    }

    /** 从字节数组建索引；不是 MP3 时抛 {@link IOException}。 */
    public static Mp3Track fromBytes(byte[] data) throws IOException {
        if (data == null || data.length < 512) {
            throw new IOException("音频数据为空或太短");
        }
        int start = skipId3v2(data);
        int cap = Math.max(16, data.length / 64);
        int[] offsets = new int[cap];
        int[] lengths = new int[cap];
        long[] starts = new long[cap];
        int count = 0;
        int pos = start;
        long samples = 0L;
        int sampleRate = 44100;
        long lastDuration = 0L;

        while (pos + 4 <= data.length && count < cap) {
            int header = readHeader(data, pos);
            if (header == -1) {
                // 丢同步：往后找（最多找 8 KB，防止在垃圾数据里死循环）
                int next = findSync(data, pos + 1, Math.min(data.length - 4, pos + 8192));
                if (next < 0) {
                    break;
                }
                pos = next;
                continue;
            }
            int frameLen = frameLength(header, data, pos);
            int sr = sampleRate(header);
            int samplesPerFrame = samplesPerFrame(header, sr);
            if (frameLen <= 4 || pos + frameLen > data.length) {
                break;
            }
            if (count == 0) {
                sampleRate = sr;
            }
            offsets[count] = pos;
            lengths[count] = frameLen;
            starts[count] = (long) Math.round(samples * 1000.0 / sampleRate);
            count++;
            samples += samplesPerFrame;
            lastDuration = (long) Math.round(samplesPerFrame * 1000.0 / sampleRate);
            pos += frameLen;
        }
        if (count < 16) {
            throw new IOException("不是有效的 MP3（只找到 " + count + " 帧）");
        }
        long durationMs = Math.max(0L, starts[count - 1] + lastDuration);
        return new Mp3Track(data, copy(offsets, count), copy(lengths, count),
                copyLong(starts, count), durationMs, sampleRate);
    }

    /** 时间 → 帧下标（返回不晚于该时间的最后一帧）。 */
    public int frameAt(long positionMs) {
        if (positionMs <= 0) {
            return 0;
        }
        int lo = 0;
        int hi = startsMs.length - 1;
        int found = 0;
        while (lo <= hi) {
            int mid = (lo + hi) >>> 1;
            if (startsMs[mid] <= positionMs) {
                found = mid;
                lo = mid + 1;
            } else {
                hi = mid - 1;
            }
        }
        return found;
    }

    /** 某一帧的起始时间。 */
    public long startOf(int frame) {
        if (frame <= 0) {
            return 0L;
        }
        return frame >= startsMs.length ? durationMs : startsMs[frame];
    }

    /** 帧下标 → 新的流起点（用于跳转）。 */
    public int offsetOf(int frame) {
        if (frame <= 0) {
            return 0;
        }
        return frame >= offsets.length ? data.length : offsets[frame];
    }

    // ------------------------------------------------------------------
    // MP3 帧头解析（只处理 Layer III）
    // ------------------------------------------------------------------

    private static int skipId3v2(byte[] data) {
        if (data.length > 10 && data[0] == 'I' && data[1] == 'D' && data[2] == '3') {
            int size = ((data[6] & 0x7f) << 21) | ((data[7] & 0x7f) << 14)
                    | ((data[8] & 0x7f) << 7) | (data[9] & 0x7f);
            int total = 10 + size;
            boolean footer = (data[5] & 0x10) != 0;
            if (footer) {
                total += 10;
            }
            if (total > 0 && total < data.length) {
                return total;
            }
        }
        return 0;
    }

    /** 解析 32 位帧头；不是合法 Layer III 帧头时返回 -1。 */
    static int readHeader(byte[] data, int pos) {
        if (pos + 4 > data.length) {
            return -1;
        }
        int b0 = data[pos] & 0xff;
        int b1 = data[pos + 1] & 0xff;
        int b2 = data[pos + 2] & 0xff;
        int b3 = data[pos + 3] & 0xff;
        if (b0 != 0xff || (b1 & 0xe0) != 0xe0) {
            return -1;
        }
        int version = (b1 >> 3) & 0x03;   // 0=2.5, 2=2, 3=1
        int layer = (b1 >> 1) & 0x03;     // 1=III
        if (version == 1 || layer != 1) {
            return -1;                     // 保留版本 / 非 Layer III
        }
        int bitrateIndex = (b2 >> 4) & 0x0f;
        int srIndex = (b2 >> 2) & 0x03;
        if (bitrateIndex == 0 || bitrateIndex == 15 || srIndex == 3) {
            return -1;
        }
        return (b0 << 24) | (b1 << 16) | (b2 << 8) | b3;
    }

    private static int findSync(byte[] data, int from, int to) {
        for (int i = Math.max(0, from); i + 4 <= to; i++) {
            if (readHeader(data, i) != -1) {
                return i;
            }
        }
        return -1;
    }

    private static int versionOf(int header) {
        return ((header >> 19) & 0x03);
    }

    private static int bitrateIndex(int header) {
        return ((header >> 12) & 0x0f);
    }

    private static int sampleRateIndex(int header) {
        return ((header >> 10) & 0x03);
    }

    private static boolean padding(int header) {
        return ((header >> 9) & 0x01) != 0;
    }

    private static final int[][] BITRATES_KBPS = {
            // MPEG1 Layer III
            {0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0},
            // MPEG2 / 2.5 Layer III
            {0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0},
    };

    private static final int[][] SAMPLE_RATES = {
            {11025, 12000, 8000},    // MPEG2.5
            null,                    // 保留
            {22050, 24000, 16000},   // MPEG2
            {44100, 48000, 32000},   // MPEG1
    };

    static int frameLength(int header, byte[] data, int pos) {
        int version = versionOf(header);
        int bitrate = BITRATES_KBPS[version == 3 ? 0 : 1][bitrateIndex(header)];
        int sr = sampleRate(header);
        if (bitrate <= 0 || sr <= 0) {
            return -1;
        }
        int pad = padding(header) ? 1 : 0;
        if (version == 3) {
            return (144 * bitrate * 1000 / sr) + pad;
        }
        return (72 * bitrate * 1000 / sr) + pad;
    }

    static int sampleRate(int header) {
        int version = versionOf(header);
        int idx = sampleRateIndex(header);
        int[] table = SAMPLE_RATES[version];
        if (table == null || idx >= table.length) {
            return -1;
        }
        return table[idx];
    }

    static int samplesPerFrame(int header, int sampleRate) {
        // Layer III：MPEG1 = 1152 samples，MPEG2/2.5 = 576
        return versionOf(header) == 3 ? 1152 : 576;
    }

    private static int[] copy(int[] src, int len) {
        int[] out = new int[len];
        System.arraycopy(src, 0, out, 0, len);
        return out;
    }

    private static long[] copyLong(long[] src, int len) {
        long[] out = new long[len];
        System.arraycopy(src, 0, out, 0, len);
        return out;
    }
}
