package com.qingcheng.zmusicbridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.qingcheng.zmusicbridge.music.MusicSearch;
import com.qingcheng.zmusicbridge.music.SongInfo;
import com.qingcheng.zmusicbridge.protocol.PacketCodec;

import org.bukkit.ChatColor;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.configuration.ConfigurationSection;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.plugin.java.JavaPlugin;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * ZMusicBridge：让玩家用 {@code [点歌]歌名}（或 {@code /zmplay 歌名}）在游戏内点歌。
 *
 * <p>本插件扮演「服务端插件」的角色，按 ZMusic v5 的 {@code zmusic:packet} 协议
 * 直接向客户端 ZMusic Mod 下发 {@code server.hello} / {@code server.play}，
 * 因此<strong>不需要</strong> ZMusic 官方服务端插件、云 API 或 device_token。
 * 歌曲搜索是自己写的（网易云公开搜索接口 + 公开 https 音频直链）。
 */
public final class ZMusicBridgePlugin extends JavaPlugin implements Listener {

    private static final String PREFIX = ChatColor.AQUA + "[点歌] " + ChatColor.RESET;

    private final MusicSearch search = new MusicSearch();
    /** 已发送过 client.hello 的玩家（= 装了 ZMusic Mod 且完成握手）。 */
    private final Set<UUID> ready = ConcurrentHashMap.newKeySet();
    private final Map<UUID, Long> lastRequestAt = new ConcurrentHashMap<>();

    private String trigger = "[点歌]";
    private boolean broadcast = true;
    private long cooldownMs = 5000L;
    private JsonObject audioHeaders = new JsonObject();

    @Override
    public void onEnable() {
        saveDefaultConfig();
        loadSettings();

        getServer().getMessenger().registerOutgoingPluginChannel(this, PacketCodec.CHANNEL);
        getServer().getMessenger().registerIncomingPluginChannel(this, PacketCodec.CHANNEL,
                (channel, player, message) -> onPluginMessage(player, message));
        getServer().getPluginManager().registerEvents(this, this);

        getLogger().info("ZMusicBridge 已启用，触发词：" + trigger);
    }

    @Override
    public void onDisable() {
        getServer().getMessenger().unregisterIncomingPluginChannel(this, PacketCodec.CHANNEL);
        getServer().getMessenger().unregisterOutgoingPluginChannel(this, PacketCodec.CHANNEL);
    }

    private void loadSettings() {
        reloadConfig();
        trigger = getConfig().getString("trigger", "[点歌]");
        if (trigger == null || trigger.isEmpty()) {
            trigger = "[点歌]";
        }
        broadcast = getConfig().getBoolean("broadcast", true);
        cooldownMs = Math.max(0, getConfig().getInt("cooldown-seconds", 5)) * 1000L;

        JsonObject headers = new JsonObject();
        ConfigurationSection sec = getConfig().getConfigurationSection("audio-headers");
        if (sec != null) {
            for (String key : sec.getKeys(false)) {
                String value = sec.getString(key);
                if (value != null) {
                    headers.addProperty(key, value);
                }
            }
        }
        audioHeaders = headers;
    }

    // ------------------------------------------------------------------
    // 触发：聊天 / 命令
    // ------------------------------------------------------------------
    @SuppressWarnings("deprecation")
    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onChat(AsyncPlayerChatEvent event) {
        String message = event.getMessage();
        if (message == null || !message.startsWith(trigger)) {
            return;
        }
        String keyword = message.substring(trigger.length()).trim();
        if (keyword.isEmpty()) {
            return;
        }
        handleRequest(event.getPlayer(), keyword);
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        ready.remove(event.getPlayer().getUniqueId());
        lastRequestAt.remove(event.getPlayer().getUniqueId());
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        String name = command.getName().toLowerCase();
        if (name.equals("zmstop")) {
            stopFor(sender);
            return true;
        }
        // zmplay
        if (!(sender instanceof Player)) {
            sender.sendMessage(PREFIX + "请在游戏内使用。");
            return true;
        }
        if (args.length == 0) {
            sender.sendMessage(PREFIX + "用法：/" + label + " <歌名>，或在聊天里发 " + trigger + "歌名");
            return true;
        }
        handleRequest((Player) sender, String.join(" ", args));
        return true;
    }

    // ------------------------------------------------------------------
    // 点歌流程
    // ------------------------------------------------------------------
    private void handleRequest(Player player, String keyword) {
        long now = System.currentTimeMillis();
        Long last = lastRequestAt.get(player.getUniqueId());
        if (last != null && now - last < cooldownMs) {
            long wait = (cooldownMs - (now - last)) / 1000 + 1;
            player.sendMessage(PREFIX + ChatColor.YELLOW + "点歌太快啦，" + wait + " 秒后再试~");
            return;
        }
        lastRequestAt.put(player.getUniqueId(), now);
        player.sendMessage(PREFIX + "正在搜索：" + ChatColor.WHITE + keyword + ChatColor.RESET + " ...");

        getServer().getScheduler().runTaskAsynchronously(this, () -> {
            final SongInfo song = search.search(keyword);
            getServer().getScheduler().runTask(this, () -> {
                if (song == null) {
                    player.sendMessage(PREFIX + ChatColor.RED + "没找到「" + keyword + "」，换个关键词试试~");
                    return;
                }
                playSong(player, song);
            });
        });
    }

    private void playSong(Player requester, SongInfo song) {
        List<Player> targets = new ArrayList<>();
        if (broadcast) {
            for (Player online : getServer().getOnlinePlayers()) {
                if (ready.contains(online.getUniqueId())) {
                    targets.add(online);
                }
            }
        } else if (ready.contains(requester.getUniqueId())) {
            targets.add(requester);
        }

        if (targets.isEmpty()) {
            requester.sendMessage(PREFIX + ChatColor.YELLOW
                    + "没有检测到装了 ZMusic Mod 的玩家。请安装 ZMusic 客户端 Mod 后重新进入服务器~");
            return;
        }
        int sent = 0;
        for (Player target : targets) {
            if (sendPlay(target, song)) {
                sent++;
            }
        }
        if (sent == 0) {
            requester.sendMessage(PREFIX + ChatColor.RED
                    + "播放数据发送失败（可能超过协议大小上限），换首歌试试~");
            return;
        }
        String who = song.title + ChatColor.GRAY + " - " + song.artistText() + ChatColor.RESET;
        if (broadcast) {
            getServer().broadcastMessage(PREFIX + (sent > 1 ? "全服点歌：" : "点歌：") + who);
        } else {
            requester.sendMessage(PREFIX + "点歌：" + who);
        }
        getLogger().info(requester.getName() + " 点歌: " + song.title + " - " + song.artistText()
                + " (" + song.source + ":" + song.id + ") -> " + sent + " 人");
    }

    /**
     * 编码并发送一个协议包；超长等编码失败只记日志，不向服务器线程抛异常（#32）。
     */
    private boolean sendPacket(Player player, String type, JsonObject data) {
        try {
            player.sendPluginMessage(this, PacketCodec.CHANNEL, PacketCodec.encode(type, data));
            return true;
        } catch (IllegalArgumentException e) {
            getLogger().warning("发送 " + type + " 给 " + player.getName() + " 失败：" + e.getMessage());
            return false;
        }
    }

    private boolean sendPlay(Player target, SongInfo song) {
        JsonObject songObj = new JsonObject();
        songObj.addProperty("id", song.id);
        songObj.addProperty("source", song.source);
        songObj.addProperty("title", song.title);
        JsonArray artists = new JsonArray();
        for (String artist : song.artists) {
            artists.add(artist);
        }
        songObj.add("artists", artists);
        if (song.album != null && !song.album.isEmpty()) {
            songObj.addProperty("album", song.album);
        }

        JsonObject audio = new JsonObject();
        audio.addProperty("type", "url");
        audio.addProperty("url", song.audioUrl);
        if (audioHeaders.size() > 0) {
            audio.add("headers", audioHeaders.deepCopy());
        }

        JsonObject data = new JsonObject();
        data.addProperty("requestId", UUID.randomUUID().toString());
        data.addProperty("mode", "replace");
        data.add("song", songObj);
        data.add("audio", audio);

        return sendPacket(target, "server.play", data);
    }

    private void stopFor(CommandSender sender) {
        JsonObject data = new JsonObject();
        data.addProperty("requestId", UUID.randomUUID().toString());
        data.addProperty("reason", "command");
        int sent = 0;
        for (Player online : getServer().getOnlinePlayers()) {
            if (!ready.contains(online.getUniqueId())) {
                continue;
            }
            if (!broadcast && sender instanceof Player
                    && !online.getUniqueId().equals(((Player) sender).getUniqueId())) {
                continue;
            }
            if (sendPacket(online, "server.stop", data)) {
                sent++;
            }
        }
        sender.sendMessage(PREFIX + "已停止播放（" + sent + " 人）。");
    }

    // ------------------------------------------------------------------
    // 来自客户端 Mod 的握手 / 回报
    // ------------------------------------------------------------------
    private void onPluginMessage(Player player, byte[] message) {
        JsonObject envelope = PacketCodec.decode(message);
        if (envelope == null) {
            return;
        }
        // 结构合法不等于字段类型合法：type 必须是字符串，否则安全忽略（#28）。
        JsonElement typeElement = envelope.get("type");
        if (typeElement == null || !typeElement.isJsonPrimitive()
                || !typeElement.getAsJsonPrimitive().isString()) {
            return;
        }
        String type = typeElement.getAsString();
        if ("client.hello".equals(type)) {
            ready.add(player.getUniqueId());
            sendHello(player);
            getLogger().info("ZMusic Mod 握手完成：" + player.getName());
        } else if ("client.error".equals(type)) {
            JsonObject data = envelope.has("data") && envelope.get("data").isJsonObject()
                    ? envelope.getAsJsonObject("data") : new JsonObject();
            getLogger().warning("ZMusic 客户端报错 (" + player.getName() + "): " + data);
        }
    }

    private void sendHello(Player player) {
        JsonObject data = new JsonObject();
        data.addProperty("protocolVersion", PacketCodec.VERSION);
        data.addProperty("serverVersion", getDescription().getVersion());
        data.addProperty("platform", "bukkit");
        JsonArray capabilities = new JsonArray();
        for (String cap : new String[] {"play.url", "stop", "status", "progress"}) {
            capabilities.add(cap);
        }
        data.add("capabilities", capabilities);
        JsonObject limits = new JsonObject();
        limits.addProperty("maxPacketBytes", 32760);
        limits.addProperty("maxJsonBytes", PacketCodec.MAX_JSON_BYTES);
        limits.addProperty("maxLyricsBytes", 262144);
        data.add("limits", limits);

        sendPacket(player, "server.hello", data);
    }
}
