// Fixture body appended to the actual legacy/modern business methods by run_song_java.py.
    static final Config config = new Config();
    static class Config {
        String trigger = "[点歌]", stopWord = "停止", guiWord = "台", loginWord = "登录",
                logoutWord = "登出", neteaseCookie = "", modUrl = "", hintText = "";
        boolean onlyMySong = false, localFallback = true;
        float volume = 1;
    }
    static final java.util.concurrent.atomic.AtomicInteger PENDING = new java.util.concurrent.atomic.AtomicInteger();
    static String lastKey = "", currentLabel = "", lastMyQuery = "";
    static long lastKeyAt, lastMyRequestAt;
    static volatile boolean stoppedByUser;
    static final long WAIT_MS = 30000;
    static final ManualQueue SCHED = new ManualQueue(), POOL = new ManualQueue();
    static class ManualQueue {
        final java.util.Deque<Runnable> tasks = new java.util.ArrayDeque<>();
        synchronized void schedule(Runnable r, long delay, java.util.concurrent.TimeUnit unit) { tasks.add(r); }
        synchronized void submit(Runnable r) { tasks.add(r); }
        synchronized Runnable take() { return tasks.remove(); }
        void runAll() { while (!tasks.isEmpty()) take().run(); }
    }
    static class Component {
        final String text;
        Component(String text) { this.text = text; }
        static Component literal(String text) { return new Component(text); }
        String getString() { return text; }
    }
    public static class Chat {
        static final java.util.List<String> messages = new java.util.ArrayList<>();
        public void addMessage(Component c) { messages.add(c.text); }
        public void addClientSystemMessage(Component c) { messages.add(c.text); }
    }
    public static class Gui {
        public Chat getChat() { return new Chat(); }
    }
    static class Minecraft {
        static final Minecraft instance = new Minecraft();
        final Gui gui = new Gui();
        static Minecraft getInstance() { return instance; }
        void execute(Runnable r) { synchronized (ui) { ui.add(r); } }
        final java.util.Deque<Runnable> ui = new java.util.ArrayDeque<>();
        void drain() { while (!ui.isEmpty()) ui.remove().run(); }
    }
    static class SongPlayer {
        static final java.util.List<String> plays = new java.util.ArrayList<>();
        static void play(String url, float volume, java.util.function.Consumer<String> onError) { plays.add(url); }
        static void stop() { }
    }
    static class MusicSearch {
        static int calls;
        static java.util.concurrent.CountDownLatch entered, release;
        static boolean fail;
        static class Song {
            String audioUrl = "https://invalid.test/local.mp3", title = "Local", note = "";
            int trialSeconds;
            boolean isTrial() { return false; }
            String artistText() { return "Artist"; }
        }
        static Song search(String query, String cookie) {
            calls++;
            if (entered != null) {
                entered.countDown();
                try { require(release.await(3, java.util.concurrent.TimeUnit.SECONDS), "release search"); }
                catch (InterruptedException e) { throw new RuntimeException(e); }
            }
            if (fail) throw new RuntimeException("fake network failure");
            return new Song();
        }
        static Song byId(String id, String cookie) { return search(id, cookie); }
    }
    static String myName() { return "Alice"; }
    static void openScreen() { }
    static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
    static void chat(String payload, boolean mine) { handleChat("[点歌]" + payload, mine); }
    static void reset() {
        stopPlayback(); SCHED.tasks.clear(); POOL.tasks.clear();
        SongPlayer.plays.clear(); Chat.messages.clear(); Minecraft.instance.ui.clear();
        MusicSearch.calls = 0; MusicSearch.entered = MusicSearch.release = null; MusicSearch.fail = false;
        lastMyRequestAt = 0; lastMyQuery = "";
    }
    static void gate() {
        MusicSearch.entered = new java.util.concurrent.CountDownLatch(1);
        MusicSearch.release = new java.util.concurrent.CountDownLatch(1);
    }
    static Thread runBlocked() throws Exception {
        Thread thread = new Thread(POOL.take()); thread.start();
        require(MusicSearch.entered.await(2, java.util.concurrent.TimeUnit.SECONDS), "search not entered");
        return thread;
    }
    static void finish(Thread thread) throws Exception {
        MusicSearch.release.countDown(); thread.join(2000); require(!thread.isAlive(), "worker leaked");
    }
    public static void main(String[] args) throws Exception {
        reset(); chat("Same", true); chat("停止", true); SCHED.runAll(); POOL.runAll();
        require(MusicSearch.calls == 0 && SongPlayer.plays.isEmpty(), "stopped delayed fallback ran");

        reset(); chat("Queued", true); SCHED.runAll(); chat("停止", true); POOL.runAll();
        require(MusicSearch.calls == 0, "cancelled queued search started HTTP");

        reset(); gate(); chat("Running", true); SCHED.runAll(); Thread running = runBlocked();
        chat("停止", true); finish(running); Minecraft.instance.drain();
        require(SongPlayer.plays.isEmpty() && currentLabel.isEmpty(), "running search revived playback");
        require(Chat.messages.stream().noneMatch(s -> s.contains("正在播放")), "stale success message");

        reset(); gate(); chat("Seed", true); chat("result | id:123456", false);
        Thread byId = runBlocked(); chat("停止", true); finish(byId);
        require(SongPlayer.plays.isEmpty(), "running by-id lookup revived playback");

        reset(); chat("Same", true); chat("停止", true); chat("Same", true);
        SCHED.runAll(); POOL.runAll();
        require(MusicSearch.calls == 1 && SongPlayer.plays.size() == 1, "fresh same query did not work");

        reset(); gate(); chat("Old local", true); SCHED.runAll(); Thread old = runBlocked();
        chat("Robot | https://invalid.test/robot.mp3", false); finish(old);
        require(SongPlayer.plays.equals(java.util.List.of("https://invalid.test/robot.mp3")), "old local replaced robot result");

        reset(); chat("Mine", true); chat("停止", false); SCHED.runAll(); POOL.runAll();
        require(SongPlayer.plays.size() == 1, "another player's stop cancelled mine");

        reset(); chat("Seed", true); chat("停止", true);
        chat("Alice | https://invalid.test/late.mp3", false); chat("Alice | id:123456", false);
        POOL.runAll(); require(SongPlayer.plays.isEmpty() && MusicSearch.calls == 0, "late robot response ignored stop");

        reset(); gate(); MusicSearch.fail = true; chat("Failure", true); SCHED.runAll();
        Thread failed = runBlocked(); chat("停止", true); finish(failed); Minecraft.instance.drain();
        require(Chat.messages.stream().noneMatch(s -> s.contains("fake network failure")), "cancelled error displayed");

        reset(); chat("Message", true); SCHED.runAll(); POOL.runAll(); chat("停止", true);
        Minecraft.instance.drain();
        require(Chat.messages.stream().noneMatch(s -> s.contains("正在播放")), "already queued success displayed after stop");

        reset(); chat("https://invalid.test/own.mp3", true);
        require(SongPlayer.plays.equals(java.util.List.of("https://invalid.test/own.mp3")), "fresh own direct URL was blocked");

        reset(); chat("id:123456", true); POOL.runAll();
        require(MusicSearch.calls == 1 && SongPlayer.plays.size() == 1, "fresh own ID was blocked");
        System.out.println("PASS: 12 client cancellation scenarios");
    }
