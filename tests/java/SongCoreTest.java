import com.qingcheng.songmod.Mp3Track;
import com.qingcheng.songmod.SongLyrics;
import com.qingcheng.songmod.SongProtocol;
import com.qingcheng.songmod.SongSpeed;

import java.util.List;

/**
 * 客户端 Mod 纯逻辑回归（不依赖 Minecraft / 网络 / 音频设备）。
 *
 * <p>覆盖：倍速解析与剥离、聊天协议（混淆直链 / 歌曲 id / 一起听名单 / at: 起播位置）、
 * LRC 解析与按时间取行、MP3 帧索引（时长 / 按时间定位）。
 */
public final class SongCoreTest {

    private static int passed = 0;

    public static void main(String[] args) throws Exception {
        testSpeed();
        testSpeedSplit();
        testObfuscatedUrl();
        testSongId();
        testAtSeconds();
        testPlaybackParse();
        testListenJoin();
        testListenList();
        testLrcParse();
        testLrcNoTimestamps();
        testMp3FrameIndex();
        System.out.println("PASS: song core " + passed + " assertions");
    }

    private static void testSpeed() {
        eq("1.0", SongSpeed.key(1.0f), "key 1.0");
        eq("0.75", SongSpeed.key(0.75f), "key 0.75");
        eq("2.0x", SongSpeed.label(2.0f), "label 2.0");
        eq("1.5", Float.toString(SongSpeed.parseToken("1.5")), "parse 1.5");
        eq("1.5", Float.toString(SongSpeed.parseToken("1.5x")), "parse 1.5x");
        eq("-1.0", Float.toString(SongSpeed.parseToken("0.5?")), "invalid suffix -> -1");
        eq("1.0", Float.toString(SongSpeed.normalize(1.2f)), "normalize 1.2 -> 1.0");
        eq("2.0", Float.toString(SongSpeed.normalize(1.9f)), "normalize 1.9 -> 2.0");
    }

    private static void testSpeedSplit() {
        SongSpeed.Split split = SongSpeed.splitTrailing("起风了 1.5");
        eq("起风了", split.text, "split text");
        eq("1.5", Float.toString(split.speed), "split speed");
        // 纯数字内容不能被吃掉
        split = SongSpeed.splitTrailing("1.5");
        eq("1.5", split.text, "pure number keeps text");
        eq("false", Boolean.toString(split.hasSpeed()), "pure number no speed");
        // 普通歌名不受影响
        split = SongSpeed.splitTrailing("孤勇者");
        eq("孤勇者", split.text, "plain text");
        // 非档位数字不剥
        split = SongSpeed.splitTrailing("起风了 1920");
        eq("false", Boolean.toString(split.hasSpeed()), "unknown number not speed");
    }

    private static void testObfuscatedUrl() {
        String payload = "起风了 - 冯沁苑 | \u00a4m801\u00b7music\u00b7126\u00b7net\u00a6123456\u00b7mp3 id:1901371647";
        String url = SongProtocol.extractObfuscatedUrl(payload);
        eq("http://m801.music.126.net/123456.mp3", url, "obfuscated url");
        eq(null, SongProtocol.extractObfuscatedUrl("普通消息"), "plain no url");
    }

    private static void testSongId() {
        eq("1901371647", SongProtocol.extractSongId("xxx | \u00a4abc id:1901371647"),
                "extract id:");
        eq("123456", SongProtocol.extractSongId("xxx #123456"), "legacy #id");
        eq(null, SongProtocol.extractSongId("id:12"), "too short id ignored");
    }

    private static void testAtSeconds() {
        eq("125", Integer.toString(SongProtocol.extractAtSeconds("… | \u00a4x id:1 at:125")),
                "at:125");
        eq("-1", Integer.toString(SongProtocol.extractAtSeconds("… x")), "no at");
    }

    private static void testPlaybackParse() {
        SongProtocol.Playback playback = SongProtocol.parsePlayback(
                "点了一首：起风了 - 冯沁苑 | \u00a4m801\u00b7music\u00b7126\u00b7net\u00a6a\u00b7mp3 id:1901371647 at:30");
        check(playback != null, "playback parsed");
        eq("1901371647", playback.songId, "playback id");
        eq("30", Integer.toString(playback.atSeconds), "playback at");
        check(playback.hasUrl(), "playback url");
        eq("点了一首：起风了 - 冯沁苑", playback.label, "playback label");
    }

    private static void testListenJoin() {
        SongProtocol.ListenJoin list = SongProtocol.parseListenJoin("一起听");
        check(list != null && list.listOnly, "listen list only");
        SongProtocol.ListenJoin join = SongProtocol.parseListenJoin("一起听 Steve_01 1.5");
        check(join != null && !join.listOnly, "listen join parsed");
        eq("Steve_01", join.player, "join player");
        eq("1.5", Float.toString(join.speed), "join speed");
        check(SongProtocol.parseListenJoin("起风了") == null, "not a listen command");
        // 中文歌名不能被当成玩家
        check(SongProtocol.parseListenJoin("一起听 好听的歌") == null, "chinese not player");
    }

    private static void testListenList() {
        List<SongProtocol.ListenEntry> entries = SongProtocol.parseListenList(
                "一起听名单|Steve~起风了-冯沁苑~125;Alex~孤勇者~8;bad name~x~1");
        eq("2", Integer.toString(entries.size()), "listen list size");
        eq("Steve", entries.get(0).player, "listen list player");
        eq("125", Integer.toString(entries.get(0).elapsedSeconds), "listen list elapsed");
        eq("Alex", entries.get(1).player, "listen second player");
    }

    private static void testLrcParse() {
        String lrc = "[ar:冯沁苑]\n[ti:起风了]\n[00:00.000] 作词 : 米果\n"
                + "[00:12.34]这一路上走走停停\n[00:16.500][01:00.000]顺着少年漂流的痕迹\n"
                + "\n[offset:500]";
        SongLyrics.Lyrics lyrics = SongLyrics.parse(lrc);
        check(!lyrics.isEmpty(), "lrc not empty");
        eq("作词 : 米果", lyrics.lineAt(0), "lrc first line");
        // offset:500 → 时间轴整体前移 500ms
        eq("这一路上走走停停", lyrics.lineAt(11900), "lrc offset line");
        eq("这一路上走走停停", lyrics.lineAt(13000), "lrc line range");
        eq("顺着少年漂流的痕迹", lyrics.lineAt(16000), "lrc second line");
        eq("作词 : 米果", lyrics.lineAt(1000), "first line still shown");
        // 同一行多个时间标签
        eq("顺着少年漂流的痕迹", lyrics.lineAt(59500), "multiple timestamps");
        check(lyrics.indexAt(12000) == 1, "indexAt 12000");
    }

    private static void testLrcNoTimestamps() {
        SongLyrics.Lyrics lyrics = SongLyrics.parse("纯文本歌词\n第二行");
        check(lyrics.isEmpty(), "no timestamps -> empty");
        eq("", lyrics.lineAt(5000), "empty lyrics line");
    }

    private static void testMp3FrameIndex() throws Exception {
        // 手工拼 40 个 MPEG1 Layer III 帧头（128kbps 44.1kHz），验证帧索引与时长
        int frames = 40;
        int frameLen = 144 * 128000 / 44100;   // = 417
        byte[] data = new byte[frameLen * frames];
        for (int i = 0; i < frames; i++) {
            int pos = i * frameLen;
            data[pos] = (byte) 0xFF;
            data[pos + 1] = (byte) 0xFB;       // MPEG1 Layer III
            data[pos + 2] = (byte) 0x90;       // 128kbps / 44100
            data[pos + 3] = (byte) 0x00;
        }
        Mp3Track track = Mp3Track.fromBytes(data);
        eq(Integer.toString(frames), Integer.toString(track.offsets.length), "mp3 frame count");
        // 每帧 1152 samples / 44.1kHz ≈ 26ms
        check(track.durationMs >= 1000 && track.durationMs <= 1100,
                "mp3 duration ~" + track.durationMs + "ms");
        eq("0", Integer.toString(track.frameAt(0)), "frameAt 0");
        int frameAt500 = track.frameAt(500);
        check(frameAt500 >= 18 && frameAt500 <= 20, "frameAt 500ms = " + frameAt500);
        eq(Integer.toString(track.offsets[5]), Integer.toString(track.offsetOf(5)), "offsetOf");
    }

    // --------------------------------------------------------------
    private static void check(boolean ok, String what) {
        if (!ok) {
            throw new AssertionError("FAIL: " + what);
        }
        passed++;
        System.out.println("PASS: " + what);
    }

    private static void eq(String expected, String actual, String what) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError("FAIL: " + what + " expected [" + expected + "] but was [" + actual + "]");
        }
        passed++;
        System.out.println("PASS: " + what);
    }

    private static void eq(int expected, int actual, String what) {
        eq(Integer.toString(expected), Integer.toString(actual), what);
    }
}
