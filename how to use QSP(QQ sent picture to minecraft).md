# How to use QSP（QQ sent picture to Minecraft / 网站）

> QSP = **QQ 群里的图片，经机器人转发到 Minecraft 公屏 / 网站**。
>
> 本文覆盖两种用法：
>
> 1. **只发到 Minecraft**：QQ 群发图 → 机器人把 `[图片] + 链接` 发到 MC 公屏。
> 2. **同时给网站看**：QQ 群发图 → 机器人保存图片 → 网站/网页读取（方案 A 本机直连，方案 B 用 `other_qq_pic_sent/` 中继）。

---

## 目录

1. [QSP 是什么](#1-qsp-是什么)
2. [前置条件](#2-前置条件)
3. [方案选择](#3-方案选择)
4. [方案 A：A 自己开（单机）](#4-方案-aa-自己开单机)
5. [方案 B：A/B 分离（B 跑 other_qq_pic_sent）](#5-方案-bab-分离b-跑-other_qq_pic_sent)
6. [Minecraft 侧怎么用](#6-minecraft-侧怎么用)
7. [网站/网页怎么用](#7-网站网页怎么用)
8. [API 参考](#8-api-参考)
9. [图片命名](#9-图片命名)
10. [安全与运维](#10-安全与运维)
11. [常见问题](#11-常见问题)
12. [命令速查](#12-命令速查)

---

## 1. QSP 是什么

```
QQ 群消息（文字 / 图片）
        │ OneBot 11（NapCat / SnowLuma）
        ▼
A：Minecraft 机器人（Python GUI + Node 引擎）
        │
        ├─ 图片下载 → pic_http/ 保存
        ├─ 公屏发：[群名][昵称]：[图片] https://你的域名/pic_http/xxx.jpg
        │
        └─ 网站 API（可选）
                │
                ▼
        B：other_qq_pic_sent（可选）
          relay.py   去 A 收消息/图片
          webapp.py  Python 登录页 + 图片墙
                │
                ▼
        Cloudflare Tunnel → https://pic.qingchengsep.top
```

一句话：

- **QSP → MC**：机器人把 QQ 图片变成 MC 公屏上可点的链接。
- **QSP → 网站**：机器人把图片存下来，网站通过 API 或内置网页展示。

---

## 2. 前置条件

| 项目 | 要求 |
|---|---|
| Minecraft 机器人 | 能正常连接服务器、QQ 转述已连通 |
| QQ 网关 | NapCat / SnowLuma（OneBot 11）已连接 |
| 图片公网地址 | 手机/MC 玩家能访问的域名或 IP（推荐 Cloudflare 域名） |
| Python（方案 B） | 3.9+，无第三方依赖 |
| 网站（可选） | 自己的 Python 后端，或直接用 B 的内置网页 |

> 如果只用 QSP → MC，不需要 B 机器，也不需要网站。

---

## 3. 方案选择

| | 方案 A：A 自己开 | 方案 B：A/B 分离 |
|---|---|---|
| 适用 | 机器人、网站同一台机器 | 机器人内网、网站在另一台服务器 |
| 额外程序 | 无 | `other_qq_pic_sent/webapp.py` |
|  Website 入口 | A:8765 | B:8080（Cloudflare 指向它） |
| 登录页 | 无（只有 API） | 有（Python 登录 + 图片墙） |
| 图片存储 | A 的 `pic_http/` | A + B 的 `relay_data/pic_http/` |
| 消息历史 | A 内存最近 500 条 | B 的 SQLite，重启不丢 |

---

## 4. 方案 A：A 自己开（单机）

### 4.1 机器人 GUI 配置

打开机器人 → **QQ 转述** → 下方 **图片转本地链接**：

| 配置项 | 推荐值 | 说明 |
|---|---|---|
| 图片转链接 | ✅ | 不开的话 MC 公屏只有 `[图片]`，没有链接 |
| 启用网站 API | ✅（需要网站时） | 开启 `/api/qq/*` |
| 网站出站方式 | **此设备出站** | A 直接给网站/CF |
| 监听地址 | `127.0.0.1` | 网站同机；如需局域网访问改 `0.0.0.0` |
| 端口 | `8765` | 默认 |
| 公网地址 | `https://pic.qingchengsep.top` | **必须填**，否则 MC 里看到 `127.0.0.1` |
| Token | 长随机串 | 网站 API 用 |
| 保留 QQ 图片原始文件名 | 可选 | 勾选后用 QQ 原名 |

操作顺序：

1. 填好上面的配置
2. 点 **保存**
3. **断开 QQ**
4. **重新连接 QQ**
5. 看日志：出现「图片服务已启动」即成功

### 4.2 验证

A 本机：

```bash
curl http://127.0.0.1:8765/health
```

返回 `{"ok":true,...}`。

浏览器打开：

```
http://127.0.0.1:8765/pic_http/
```

会 404（正常，不提供目录列表）；只要某张图片能访问即可。

### 4.3 网站读取

```python
import httpx

r = httpx.get(
    "http://127.0.0.1:8765/api/qq/messages",
    params={"since": 0, "limit": 50},
    headers={"X-Token": "GUI 里填的 Token"},
    timeout=10,
)
print(r.json())
```

### 4.4 公网访问

- 机器人监听 `0.0.0.0` 或只给反代 `127.0.0.1`
- Nginx 反代 8765
- Cloudflare Tunnel `service: http://127.0.0.1:8765`（或指向 Nginx）

---

## 5. 方案 B：A/B 分离（B 跑 other_qq_pic_sent）

### 5.1 结构

```
A（内网 bot）192.168.1.23:8765
        │ 内网 HTTP + Token
        ▼
B（服务器）other_qq_pic_sent/
        ├─ relay.py      去 A 收消息/图片
        ├─ webapp.py     Python 登录页 + 图片墙 + API
        └─ relay_data/   SQLite + 图片
        │
        ▼
Cloudflare Tunnel → https://pic.qingchengsep.top
```

端口：

| 端口 | 位置 | 用途 |
|---|---|---|
| `8765` | A | 机器人内置 API |
| `18765` | B | 中继内部 API（只监听 127.0.0.1） |
| `8080` | B | 网页（Cloudflare 指向它） |

### 5.2 A 配置

GUI → QQ 转述 → 图片转本地链接：

| 配置项 | 填什么 |
|---|---|
| 图片转链接 | 按需 |
| 启用网站 API | ✅ |
| **网站出站方式** | **内网其他设备出站** |
| 监听地址 | `0.0.0.0`（选中 relay 会自动切） |
| 端口 | `8765` |
| 公网地址 | `https://pic.qingchengsep.top` |
| Token | 长随机串（B 要用） |

保存 → 断开 QQ → 重新连接。

A 防火墙只放行 B：

```powershell
New-NetFirewallRule -DisplayName "QSP from B" `
  -Direction Inbound -Protocol TCP -LocalPort 8765 `
  -RemoteAddress 192.168.1.50 -Action Allow
```

B 上验证：

```bash
curl http://192.168.1.23:8765/health
```

### 5.3 B 配置 relay.json

```bash
cd other_qq_pic_sent
cp relay.example.json relay.json
```

编辑 `relay.json`：

```json
{
  "upstream_base": "http://192.168.1.23:8765",
  "upstream_token": "A 的 Token",
  "listen_host": "127.0.0.1",
  "listen_port": 18765,
  "token": "给外部 API 用的 Token（可选）",
  "public_base": "https://pic.qingchengsep.top",
  "data_dir": "relay_data",
  "poll_seconds": 2,
  "request_timeout": 15,
  "web": {
    "listen_host": "127.0.0.1",
    "listen_port": 8080,
    "password_hash": "",
    "session_ttl": 86400,
    "secure_cookie": true,
    "title": "QQ 群图片"
  }
}
```

### 5.4 生成登录密码

Linux：

```bash
python3 hash_password.py '你的密码'
```

Windows：

```powershell
python hash_password.py "你的密码"
```

把输出（`pbkdf2_sha256$...`）填到 `web.password_hash`。

`web.secure_cookie`：

- 正式 HTTPS（Cloudflare）：`true`
- 本地 `http://127.0.0.1` 调试：临时改 `false`

### 5.5 启动

```bash
python3 webapp.py --config relay.json
```

Windows：

```powershell
python webapp.py --config relay.json
```

看到：

```
Web 后台：http://127.0.0.1:8080  （中继 API：http://127.0.0.1:18765）
```

### 5.6 验证

```bash
curl http://127.0.0.1:18765/health
```

返回里要有 `"upstream_ok": true`。

浏览器打开 `http://127.0.0.1:8080` → 登录 → 看图片墙。

### 5.7 Cloudflare Tunnel

```yaml
tunnel: 你的隧道ID
credentials-file: C:\Users\你\.cloudflared\你的隧道ID.json

ingress:
  - hostname: pic.qingchengsep.top
    service: http://127.0.0.1:8080
  - service: http_status:404
```

```bash
cloudflared tunnel route dns qqpic pic.qingchengsep.top
cloudflared tunnel run qqpic
```

公网打开：

```
https://pic.qingchengsep.top
```

### 5.8 常驻运行

Linux：

```bash
cp qq-relay.service /etc/systemd/system/qq-relay.service
# 改 WorkingDirectory / User / 配置路径
systemctl daemon-reload
systemctl enable --now qq-relay
journalctl -u qq-relay -f
```

Windows 任务计划：

- 程序：`python.exe`
- 参数：`other_qq_pic_sent\webapp.py --config other_qq_pic_sent\relay.json`
- 起始目录：项目根目录
- 勾选“不管用户是否登录都运行”

### 5.9 A/B 不在同一内网

frp / Tailscale / WireGuard：

```
A:8765  →  B:127.0.0.1:18764
```

`relay.json`：

```json
"upstream_base": "http://127.0.0.1:18764",
"listen_port": 18765
```

端口别混：

- `18764`：中继访问 A
- `18765`：中继内部 API
- `8080`：网页（CF 指向它）

---

## 6. Minecraft 侧怎么用

### 6.1 玩家看到什么

QQ 群有人发图后，MC 公屏会出现类似：

```
[测试群][Alice]：[图片] https://pic.qingchengsep.top/pic_http/20261025_143012_123_ab12.jpg
```

- 有些服务器会把链接变成可点的 `[链接]`
- 玩家点一下即可在浏览器看大图

### 6.2 必须满足

1. 机器人已连接 MC 服务器
2. QQ 转述已连接
3. QQ 转述页「群号」包含该群（留空=全部群）
4. 「图片转链接」已勾选
5. `公网地址` 是玩家能访问的地址（不能是 `127.0.0.1`）
6. 玩家网络能访问 `pic.qingchengsep.top`

### 6.3 链接被截断怎么办

MC 单条聊天有长度限制（默认 `maxLength=220`，最高 256）。图片链接太长时可能被截断。

处理：

- 在 `config.json` 的 `qq.maxLength` 调到 `256`（或接近上限）
- 或让玩家从网站/内置图片墙看，不依赖 MC 公屏链接

### 6.4 只有图片没有文字

纯图片消息现在会以 `[图片]` 正常转发，不再被丢弃。

---

## 7. 网站/网页怎么用

### 7.1 方案 A：自己的网站直接读 API

见 [4.3](#43-网站读取)。

### 7.2 方案 B：用内置 Python 网页

1. 浏览器打开 `https://pic.qingchengsep.top`
2. 输入密码登录
3. 每 3 秒自动刷新
4. 图片点开看大图
5. 右上角退出

改密码：

```bash
python3 hash_password.py '新密码'
```

替换 `web.password_hash`，重启 `webapp.py`。

### 7.3 方案 B：自己的网站读中继 API

B 上中继 API（只监听 127.0.0.1:18765）：

```python
import httpx

r = httpx.get(
    "http://127.0.0.1:18765/api/qq/messages",
    params={"since": 0, "limit": 50},
    headers={"X-Token": "relay.json 里的 token"},
    timeout=10,
)
print(r.json())
```

Flask：

```python
@app.get("/api/qq")
def qq():
    since = max(0, int(request.args.get("since", 0)))
    r = httpx.get(
        "http://127.0.0.1:18765/api/qq/messages",
        params={"since": since, "limit": 50},
        headers={"X-Token": TOKEN},
        timeout=10,
    )
    r.raise_for_status()
    return jsonify(r.json())
```

FastAPI：

```python
@app.get("/api/qq")
async def qq(since: int = 0):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "http://127.0.0.1:18765/api/qq/messages",
            params={"since": since, "limit": 50},
            headers={"X-Token": TOKEN},
        )
    return r.json()
```

> 中继的 `/pic_http/<文件名>` 本身不带 Token。用自己的网站展示时，建议：
> - 在网站域名下加 `/pic_http/` 反代到 B:8080（B 会检查登录会话），或
> - 只在自己的登录页/后端里转发图片。

---

## 8. API 参考

### 8.1 中继 API（B:18765，Token）

```
GET /api/qq/messages?since=0&limit=50
GET /api/qq/images?limit=50
GET /api/qq/stream?since=0        （SSE）
GET /health
GET /pic_http/<文件名>
```

鉴权：

```
X-Token: <token>
X-Api-Token: <token>
Authorization: Bearer <token>
?token=<token>
```

消息响应：

```json
{
  "ok": true,
  "last_id": 12,
  "messages": [
    {
      "id": 12,
      "time": 1792912345.123,
      "group_id": "123456789",
      "group_name": "测试群",
      "sender": "Alice",
      "text": "[图片] 晚上好",
      "images": [
        {
          "name": "20261025_143012_123_ab12.jpg",
          "url": "/pic_http/20261025_143012_123_ab12.jpg",
          "abs_url": "https://pic.qingchengsep.top/pic_http/20261025_143012_123_ab12.jpg"
        }
      ]
    }
  ]
}
```

规则：

- `since`：上次的 `last_id`，首次 0
- B 的 `id` 是本地自增，A 重启不影响
- `since` 比当前 `last_id` 大时归零

### 8.2 内置网页 API（B:8080，登录会话）

```
GET /api/messages?since=0&limit=50
```

需要登录 Cookie（浏览器自动带），只给内置页面使用。

---

## 9. 图片命名

- 默认：`20261025_143012_123_ab12.jpg`
- 勾选「保留 QQ 图片原始文件名」：`原始名.jpg`，重名自动 `_2`、`_3`
- 扩展名按真实图片格式修正
- A、B、Cloudflare 全程保持同一个文件名

---

## 10. 安全与运维

### 10.1 安全

| 项目 | 做法 |
|---|---|
| A 的 8765 | 只放行 B 的 IP |
| B 的 8080/18765 | 只监听 127.0.0.1，公网只走 Cloudflare |
| Token | 至少 32 位随机 |
| 密码 | PBKDF2 哈希，不存明文 |
| 会话 | HttpOnly + SameSite=Strict |
| CSRF | 登录/退出校验 |
| 限速 | 每 IP 5 次失败 / 10 分钟 |
| XSS | 前端只用 `textContent` |
| 上传 | 没有上传接口，只读 |
| 数据库 | Web 端无 SQL；中继 SQLite 参数化 |

### 10.2 备份

B：

```
relay_data/relay.db
relay_data/pic_http/
```

备份这两个即可。

### 10.3 清理图片

A：

```
pic_http/
```

B：

```
relay_data/pic_http/
```

建议保留 7 天，按需清理。

### 10.4 重启影响

- A 重启：消息 id 从 1 开始；B 会指纹去重，网站不受影响
- B 重启：SQLite 和图片还在，不丢
- 删除 `relay_data/`：重新从 A 同步（A 只留最近 500 条）

---

## 11. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| MC 公屏只有 `[图片]`，没有链接 | 「图片转链接」没勾 |
| 链接是 `127.0.0.1` | `公网地址` 没填或填错 |
| 玩家点链接打不开 | 域名/CF 隧道/防火墙不通 |
| 链接被截断 | `qq.maxLength` 调到 256；或让玩家看网站 |
| B `/health` 的 `upstream_ok=false` | B 访问不到 A：IP/端口/防火墙/Token |
| 登录页提示未配置密码 | `web.password_hash` 为空，运行 `hash_password.py` |
| 登录后一直“连接失败” | 看 webapp 日志同步错误；检查 `upstream_base` |
| 图片 404 | 中继还没同步完；检查 `relay_data/pic_http/` |
| 登录后又跳回登录 | `secure_cookie=true` 但用 HTTP 访问；本地调试改 false |
| CF 502 | 隧道 service 端口不对 / webapp 没启动 |

---

## 12. 命令速查

### A

```
GUI → QQ 转述 → 图片转本地链接
  图片转链接        ✅（要 MC 公屏链接）
  启用网站 API      ✅（要网站）
  网站出站方式      此设备 / 内网其他设备
  监听地址          127.0.0.1 / 0.0.0.0
  端口              8765
  公网地址          https://pic.qingchengsep.top
  Token             长随机串
保存 → 断开 QQ → 重新连接
```

### B

```bash
cd other_qq_pic_sent
cp relay.example.json relay.json
python3 hash_password.py '你的密码'
# 编辑 relay.json 填 upstream_base / upstream_token / web.password_hash
python3 webapp.py --config relay.json
```

### 验证

```bash
curl http://A:8765/health
curl http://127.0.0.1:18765/health
curl http://127.0.0.1:8080/health
```

### Cloudflare

```yaml
ingress:
  - hostname: pic.qingchengsep.top
    service: http://127.0.0.1:8080
  - service: http_status:404
```

---

**QSP 最简理解：**

> QQ 群发图 → 机器人存图并生成链接 → MC 公屏能点、网站能看。
>
> 单机用方案 A；机器人内网、网站另开用方案 B（`other_qq_pic_sent`，全 Python）。
