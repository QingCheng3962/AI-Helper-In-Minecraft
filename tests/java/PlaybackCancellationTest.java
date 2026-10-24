import com.qingcheng.songmod.SongPlayer;
import javazoom.jl.player.Player;
import java.io.*;
import java.net.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/** Runs the unmodified production SongPlayer class with controllable connections/decoder. */
public class PlaybackCancellationTest {
    static volatile Gate gate;
    static final List<String> errors = new CopyOnWriteArrayList<>();
    static final AtomicInteger streamsClosed = new AtomicInteger();
    static class Gate {
        final String stage;
        final boolean fail;
        final CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        boolean consumed;
        Gate(String stage, boolean fail) { this.stage = stage; this.fail = fail; }
        synchronized boolean claim(String stage, String path) {
            if (consumed || !this.stage.equals(stage) || !path.contains("slow")) return false;
            consumed = true; return true;
        }
        void waitForRelease() throws IOException {
            entered.countDown();
            try { require(release.await(3, TimeUnit.SECONDS), "connection not released"); }
            catch (InterruptedException e) { throw new IOException(e); }
            if (fail) throw new IOException("fake connection failure");
        }
    }
    static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
    static void await(CountDownLatch latch) throws Exception { require(latch.await(2, TimeUnit.SECONDS), "latch timeout"); }
    static Thread worker() {
        return Thread.getAllStackTraces().keySet().stream()
                .filter(t -> t.getName().equals("songmod-player") && t.isAlive()).findFirst().orElseThrow();
    }
    static void join(Thread thread) throws Exception { thread.join(2000); require(!thread.isAlive(), "worker leaked"); }
    static void reset() {
        SongPlayer.stop(); gate = null; Player.all.clear(); errors.clear(); streamsClosed.set(0);
        Player.constructorHook = null; Player.failPlay = false;
    }
    static void play(String name) { SongPlayer.play("http://offline.invalid/" + name, 1, errors::add); }
    static Player active() throws Exception {
        long limit = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        while (Player.all.isEmpty() && System.nanoTime() < limit) Thread.sleep(1);
        require(!Player.all.isEmpty(), "no decoder");
        Player p = Player.all.get(Player.all.size() - 1); await(p.started); return p;
    }
    public static void main(String[] args) throws Exception {
        URL.setURLStreamHandlerFactory(protocol -> "http".equals(protocol) ? new URLStreamHandler() {
            protected URLConnection openConnection(URL url) {
                return new HttpURLConnection(url) {
                    public void connect() { }
                    public void disconnect() { }
                    public boolean usingProxy() { return false; }
                    public int getResponseCode() throws IOException {
                        Gate g = gate;
                        if (g != null && g.claim("resolve", url.getPath())) g.waitForRelease();
                        return 200;
                    }
                    public InputStream getInputStream() throws IOException {
                        Gate g = gate;
                        if (g != null && g.claim("open", url.getPath())) g.waitForRelease();
                        return new ByteArrayInputStream(new byte[]{1}) {
                            public void close() { streamsClosed.incrementAndGet(); }
                        };
                    }
                };
            }
        } : null);

        reset(); gate = new Gate("resolve", false); play("slow"); await(gate.entered);
        Thread resolved = worker(); SongPlayer.stop(); gate.release.countDown(); join(resolved);
        require(Player.all.isEmpty() && !SongPlayer.isPlaying(), "stopped resolution published a player");

        reset(); gate = new Gate("open", false); play("slow"); await(gate.entered);
        Thread opened = worker(); SongPlayer.stop(); gate.release.countDown(); join(opened);
        require(Player.all.size() == 1 && Player.all.get(0).closed, "cancelled decoder not closed");
        require(Player.all.get(0).started.getCount() == 1 && streamsClosed.get() == 1, "cancelled stream played/leaked");

        reset(); Gate constructor = new Gate("constructor", false);
        Player.constructorHook = () -> { try { constructor.waitForRelease(); } catch (IOException e) { throw new RuntimeException(e); } };
        play("constructor"); await(constructor.entered); Thread constructed = worker();
        SongPlayer.stop(); constructor.release.countDown(); join(constructed);
        require(Player.all.get(0).closed && Player.all.get(0).started.getCount() == 1, "cancelled construction played");
        require(streamsClosed.get() == 1, "construction stream leaked");

        reset(); gate = new Gate("resolve", false); play("slow-old"); await(gate.entered);
        Thread old = worker(); play("fresh"); Player fresh = active();
        gate.release.countDown(); join(old);
        require(SongPlayer.isPlaying() && !fresh.closed, "old pending request corrupted fresh playback");
        Thread freshThread = worker(); SongPlayer.stop(); require(fresh.closed, "fresh player not stoppable");
        fresh.release.countDown(); join(freshThread);

        reset(); play("A"); Player a = active(); Thread aThread = worker();
        play("B");
        long limit = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        while (Player.all.size() < 2 && System.nanoTime() < limit) Thread.sleep(1);
        require(Player.all.size() == 2, "B not created"); Player b = Player.all.get(1); await(b.started);
        a.release.countDown(); join(aThread);
        require(a.closed && SongPlayer.isPlaying(), "old active completion lost fresh owner");
        Thread bThread = worker(); SongPlayer.stop(); require(b.closed, "stop lost fresh owner");
        b.release.countDown(); join(bThread);

        reset(); gate = new Gate("resolve", true); play("slow-failure"); await(gate.entered);
        Thread failed = worker(); SongPlayer.stop(); gate.release.countDown(); join(failed);
        require(errors.isEmpty(), "cancelled connection failure was reported");

        reset(); gate = new Gate("resolve", true); play("slow-current"); await(gate.entered);
        Thread currentFailure = worker(); gate.release.countDown(); join(currentFailure);
        require(errors.size() == 1, "current connection error not reported");

        reset(); Player.failPlay = true; play("decode-error");
        long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        while ((errors.isEmpty() || streamsClosed.get() == 0) && System.nanoTime() < end) Thread.sleep(1);
        require(errors.size() == 1 && streamsClosed.get() == 1 && Player.all.get(0).closed,
                "decode error leaked its stream/player");
        System.out.println("PASS: 8 production playback cancellation scenarios");
    }
}
