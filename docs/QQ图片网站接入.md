# QQ 图片 / 消息接入网站（Python 版）

> 两个方案，按部署方式二选一：
>
> 1. **A 自己开**：机器人和网站同机，直接用机器人内置 API。
> 2. **B 转发开**：A 在内网，B 跑 `other_qq_pic_sent/`（Python 中继 + Python Web 后台），
>    B 去 A 收消息/图片，再对外提供网页和 API。
>
> 全部是 **Python**，不需要 PHP。

---

## 一、方案一：A 自己开（本地直接用）

```
网站 / 浏览器 ──HTTP──▶ A:8765（机器人内置服务）
```

### 1. 机器人配置

GUI → **QQ 转述 → 图片转本地链接**：

| 配置 | 填 |
|---|---|
| 启用网站 API | ✅ |
| **网站出站方式** | **此设备出站** |
| 监听地址 | `127.0.0.1` |
| 端口 | `8765` |
| 公网地址 | `https://pic.qingchengsep.top`（没有公网留空） |
| Token | 长随机串 |

保存 → **断开 QQ → 重新连接**。

### 2. 网站（Python）直接读

```python
import httpx

resp = httpx.get(
    "http://127.0.0.1:8765/api/qq/messages",
    params={"since": 0, "limit": 50},
    headers={"X-Token": "机器人里的 Token"},
    timeout=10,
)
data = resp.json()
for msg in data["messages"]:
    print(msg["group_name"], msg["sender"], msg["text"])
    for img in msg["images"]:
        print("图片:", img["abs_url"])
```

前端（同机或反代后）：

```js
const r = await fetch('/api/qq/messages?since=0&limit=50', {
  headers: { 'X-Token': token }   // 生产环境建议由后端代理，不要把 Token 放浏览器
});
```

图片：`<img src="http://127.0.0.1:8765/pic_http/xxx.jpg">`

### 3. 要公网

- 监听地址改 `0.0.0.0`（或只让反代访问 `127.0.0.1`）
- 本机 Nginx 反代 8765
- Cloudflare Tunnel 指向本机 Nginx 或 `http://127.0.0.1:8765`

---

## 二、方案二：A/B 分离（B 跑 `other_qq_pic_sent`）

```
A（内网 bot）192.168.1.23:8765
        │ 内网 HTTP + Token
        ▼
B（服务器）other_qq_pic_sent/
          ├─ relay.py    去 A 收消息/图片
          └─ webapp.py   网页 + 图片墙 + API（Python 后台，默认免登录）
        │
        ▼
Cloudflare Tunnel → https://pic.qingchengsep.top
```

### 1. A 配置

| 配置 | 填 |
|---|---|
| 启用网站 API | ✅ |
| **网站出站方式** | **内网其他设备出站**（自动把监听地址设为 `0.0.0.0`） |
| 监听地址 | `0.0.0.0` |
| 端口 | `8765` |
| 公网地址 | `https://pic.qingchengsep.top` |
| Token | 长随机串（B 要用） |

保存 → 重连 QQ。A 的防火墙只放行 B 的 IP 到 8765。

B 上验证：

```bash
curl http://192.168.1.23:8765/health
```

### 2. B 部署（Python 3.9+，无第三方依赖）

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
  "public_base": "https://pic.qingchengsep.top",
  "data_dir": "relay_data",
  "poll_seconds": 2,
  "web": {
    "listen_host": "127.0.0.1",
    "listen_port": 8080,
    "require_login": false,
    "password_hash": "",
    "title": "QQ 群图片"
  }
}
```

默认 `require_login: false`，**打开网页直接看，不需要密码**。

如果以后要加登录：

```bash
python3 hash_password.py '你的密码'
```

把输出填到 `web.password_hash`，并把 `require_login` 改成 `true`，重启 webapp。

> 开启登录后：Cloudflare HTTPS 用 `secure_cookie: true`；本地 `http://127.0.0.1`
> 调试时临时改 `false`，否则浏览器不会发送 Secure Cookie。

启动：

```bash
python3 webapp.py --config relay.json
```

验证：

```bash
curl http://127.0.0.1:18765/health     # 中继：upstream_ok=true
```

浏览器打开 `http://127.0.0.1:8080` → **直接就是图片墙**（默认免登录）。

### 3. Cloudflare Tunnel

`webapp.py` 已经是完整网站，隧道直接指它：

```yaml
ingress:
  - hostname: pic.qingchengsep.top
    service: http://127.0.0.1:8080
  - service: http_status:404
```

公网打开 `https://pic.qingchengsep.top` 即可。

> 可选 Nginx：把 `/` 全部反代到 `127.0.0.1:8080`；不需要单独配 `/pic_http/`，因为图片由 webapp 自己提供。

### 4. A/B 不在同一内网

用 frp / Tailscale / WireGuard 把 A:8765 映射到 B 的 `127.0.0.1:18764`：

```json
"upstream_base": "http://127.0.0.1:18764",
"listen_port": 18765
```

- `18764`：中继访问 A
- `18765`：中继内部 API
- `8080`：Web 页面（Cloudflare 指向它）

---

## 三、两个 API 的区别

| 地址 | 鉴权 | 给谁用 |
|---|---|---|
| `GET /api/messages`（webapp :8080） | 默认公开；开启登录后要会话 Cookie | 内置图片墙 / 自己的前端同源调用 |
| `GET /<文件名>`（webapp :8080） | 默认公开；开启登录后要会话 Cookie | 图片 / `.mp4` 视频（推荐地址） |
| `GET /api/qq/messages`（relay :18765） | `X-Token` | 其他程序 / 自己的后端 |
| `GET /api/qq/images`（relay :18765） | `X-Token` | 图片列表 |
| `GET /api/qq/stream`（relay :18765） | `X-Token` | SSE 实时 |
| `GET /health` | 无 | 健康检查 |

### relay 消息格式

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
          "url": "/20261025_143012_123_ab12.jpg",
          "abs_url": "https://pic.qingchengsep.top/20261025_143012_123_ab12.jpg"
        }
      ],
      "videos": [
        {
          "name": "20261025_143100_456_cd34.mp4",
          "url": "/20261025_143100_456_cd34.mp4",
          "abs_url": "https://pic.qingchengsep.top/20261025_143100_456_cd34.mp4"
        }
      ]
    }
  ]
}
```

- `images` / `videos` 都是媒体列表；视频一般来自 QQ 的 `video` 段（`.mp4` / `.m4v` / `.mov`）
- `[图片] 链接` / `[视频] 链接` 后面会自动留空格，避免正文被 MC 当成 URL 的一部分

- `since`：上次的 `last_id`，首次 0
- 中继用自己的本地 id，A 重启不会影响网站游标
- `since` 比当前 `last_id` 大时归零

---

## 四、自己的网站怎么接（Python 后端）

### 后端代理（推荐，Token 不下发浏览器）

```python
# Flask 示例
import httpx
from flask import Flask, jsonify, request

app = Flask(__name__)
RELAY = "http://127.0.0.1:18765"
TOKEN = "中继的 Token"

@app.get("/api/qq")
def qq_messages():
    since = max(0, int(request.args.get("since", 0)))
    r = httpx.get(
        RELAY + "/api/qq/messages",
        params={"since": since, "limit": 50},
        headers={"X-Token": TOKEN},
        timeout=10,
    )
    r.raise_for_status()
    return jsonify(r.json())
```

FastAPI 同理：

```python
@app.get("/api/qq")
async def qq(since: int = 0):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            RELAY + "/api/qq/messages",
            params={"since": since, "limit": 50},
            headers={"X-Token": TOKEN},
        )
    return r.json()
```

### 前端

```js
let lastId = 0;

async function poll() {
  const r = await fetch(`/api/qq?since=${lastId}`);
  const data = await r.json();
  if (!data.ok) return;
  for (const msg of data.messages) {
    lastId = msg.id;
    const div = document.createElement('div');
    div.textContent = `[${msg.group_name}] ${msg.sender}：${msg.text}`;
    document.querySelector('#list').appendChild(div);

    for (const img of msg.images) {
      const el = document.createElement('img');
      el.src = img.url;   // 同源 /文件名 或 img.abs_url
      el.loading = 'lazy';
      document.querySelector('#list').appendChild(el);
    }
  }
}
setInterval(poll, 3000);
poll();
```

> 如果自己的网站和 B 不同源，图片需要能通过你的域名访问：要么用中继的 `abs_url`
> （Cloudflare 域名），要么在你网站加一个 `/` 图片反代到 B:8080。

---

## 五、安全（`other_qq_pic_sent` 内置网页已实现）

> **默认 `require_login=false`：知道域名的人都能看**。适合公开群/展示；
> 不想公开就设 `require_login: true` + 密码哈希。

- 登录（可选）：PBKDF2 密码哈希 + 会话 Cookie（`HttpOnly` / `SameSite=Strict`）
- CSRF（开启登录后）：登录、退出都校验 token
- 登录限速（开启登录后）：每 IP 5 次失败 / 10 分钟
- XSS：前端只用 `textContent`，CSP 禁止内联脚本
- 越权（开启登录后）：页面/API/图片都要求会话
- 木马：没有上传接口，只读
- 暴库：Web 端无 SQL；中继 SQLite 参数化；数据在 `relay_data/`（webroot 外）
- SSRF：中继地址来自配置，不接受用户 URL
- 路径穿越：图片名 basename + 目录校验
- 点击劫持：`X-Frame-Options: DENY`
- Token：只在中继/后端使用，浏览器拿不到

---

## 六、图片命名

- 默认：`20261025_143012_123_ab12.jpg`
- 勾选「保留 QQ 图片原始文件名」：`原始名.jpg`，重名自动 `_2`、`_3`
- A、B、Cloudflare 都保持同一个文件名

---

## 七、常见问题

**Q：B 的 `/health` 显示 `upstream_ok: false`？**
A：B 访问不到 A。先在 B 上 `curl http://A:8765/health`，查 IP/端口/防火墙/Token。

**Q：开启了登录，提示未配置密码？**
A：运行 `hash_password.py`，把输出填到 `web.password_hash`，重启 webapp。

**Q：网页一直“连接失败”？**
A：看 webapp 日志的同步错误；确认 relay.json 的 `upstream_base`/`upstream_token`。

**Q：图片 404？**
A：等中继同步；检查 `relay_data/pic_http/`。

**Q：中继要不要装整个项目？**
A：只要 `other_qq_pic_sent/` 目录，Python 标准库即可。
