package javazoom.jl.player;

import java.io.InputStream;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Controllable decoder fixture; no real audio hardware. */
public class Player {
    public static final List<Player> all = new CopyOnWriteArrayList<>();
    public static volatile Runnable constructorHook;
    public static volatile boolean failPlay;
    public final CountDownLatch started = new CountDownLatch(1), release = new CountDownLatch(1);
    public volatile boolean closed;
    public Player(InputStream in, Object device) {
        all.add(this);
        if (constructorHook != null) constructorHook.run();
    }
    public boolean play() throws Exception {
        started.countDown();
        if (failPlay) throw new Exception("fake decode failure");
        if (!release.await(3, TimeUnit.SECONDS)) throw new Exception("decoder not released");
        return true;
    }
    public void close() { closed = true; }
}
