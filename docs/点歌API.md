# 点歌通道：不用在聊天里发消息的点歌方式

聊天点歌（`[点歌]歌名`）有个硬伤：消息会出现在公屏（或至少走聊天协议），
服务器可能改写、还会被别人看到。下面这些通道可以**完全不发聊天消息**：

## 方案 A：点歌 HTTP API（已实现，推荐）

机器人的图片服务（`aafm_py/photo_server.py`）现在同时提供点歌 API，**复用它的端口和 Token**：

| 配置项 | 说明 |
| --- | --- |
| `song.apiEnabled = true` | 开启点歌 API（开启后自动启动 HTTP 服务） |
| `qq.imageHttpHost` / `qq.imageHttpPort` | 监听地址/端口（默认 0.0.0.0:8765） |
| `qq.webApiToken` | 访问 Token（**强烈建议设置**；任何能访问端口的人都能点歌） |

接口（`X-Token: <token>` 或 `Authorization: Bearer <token>`，也支持 `?token=`）：

### 点歌

```bash
curl -X POST http://127.0.0.1:8765/api/song/request \
  -H "X-Token: YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"player":"网页","query":"起风了","speed":"1.5"}'
```

返回：`{"ok":true,"query":"起风了 1.5","player":"网页"}`（机器人没连服务器时 `ok:false`）。

### 当前播放 / 队列

```bash
curl -H "X-Token: YOUR_TOKEN" http://127.0.0.1:8765/api/song/state
```

返回当前歌曲、点歌人、队列、是否连接。

### 一起听名单（只列**真的还在播**的人）

```bash
curl -H "X-Token: YOUR_TOKEN" http://127.0.0.1:8765/api/song/listeners
```

### 安全

- 只把端口开在 **127.0.0.1** + 反向代理（Nginx/Caddy/Cloudflare Tunnel）加 HTTPS，别裸奔公网；
- Token 足够长、别写进公开仓库；`config.json` 已 gitignore；
- 想给网站/群友用，建议放在 `https://你的域名/api/song/*` 后面再套一层自己的登录。

## 方案 B：Minecraft 命令（服务端装插件）

项目里已有可选服务端插件 `mc-music-bridge/`（ZMusicBridge）：

- 玩家在游戏里输入 **`/zmplay 歌名`**（服务端命令，不走聊天文本，公屏不会出现点歌内容）；
- 插件用 ZMusic v5 协议把歌推给装了 ZMusic 的客户端，**全服都能听**；
- 适合"能装插件的服务器"，缺点是玩家需要装 ZMusic 客户端。

> 客户端 Mod 无法自己注册服务器命令（命令由服务端处理），所以纯客户端方案只能靠聊天或下面的网络通道。

## 方案 C：Mod 直接连机器人（下一步可做）

给 Mod 加一个 HTTP 客户端（`SongApiClient`，纯 Java 标准库）：

- Mod 设置里填 **API 地址 + Token**（例如 `https://你的域名`）；
- 点歌 / 刷新一起听名单 / 加进度同步都直接请求上面的 API —— **公屏和聊天完全零消息**；
- 请求失败自动退回聊天点歌（保持兼容）。

工作量：Mod 侧 +150 行左右（HTTP + JSON 解析）+ 1 次打包；服务端已经就绪。

## 方案 D：Mod 本地兜底（已有）

机器人不在线时 Mod 自己搜网易云播放（`localFallback`）：不经过任何消息，
但只能自己听、VIP 歌受限制，也统计不到"一起听"。

## 结论

| 方案 | 公屏有消息 | 需要装插件 | 需要网络可达 | 状态 |
| --- | --- | --- | --- | --- |
| A. HTTP API | 否 | 否 | 是（或本机/内网） | ✅ 已实现 |
| B. `/zmplay` 命令 | 否 | 是（ZMusicBridge） | 否 | ✅ 已有插件 |
| C. Mod 直连 API | 否 | 否 | 是 | 待实现（服务端已就绪） |
| D. Mod 本地兜底 | 否 | 否 | 否（只影响自己） | ✅ 已有 |

先用 A：`song.apiEnabled=true` + `qq.webApiToken` 设一个 Token，然后浏览器/脚本/网站都能点歌。
