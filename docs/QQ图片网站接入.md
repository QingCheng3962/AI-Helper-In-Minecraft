# QQ 图片 / 消息接入网站（两个方案）

> 方案一：机器人、网站都在同一台机器，**直接用机器人内置 API**。
> 方案二：机器人（A）在内网，网站（B）在另一台机器，**B 上跑一个 Python 中继**，
> 由中继去 A 收消息和图片，再对外提供给网站 / Cloudflare。

---

## 一、先选方案

| | 方案一：本地直接用 | 方案二：A/B + Python 中继 |
|---|---|---|
| 适用 | 网站和机器人同一台机器 | 机器人内网、网站在另一台服务器 |
| 额外程序 | 不需要 | B 上跑 `other_qq_pic_sent/relay.py` |
| 公网入口 | 机器人 8765 或本机 Nginx | 中继端口（默认 18765） |
| 图片存储 | A 的 `pic_http/` | A 的 `pic_http/` + B 的 `relay_data/pic_http/` |
| 消息历史 | A 内存最近 500 条 | B 的 SQLite，重启不丢 |
| 适合 Cloudflare | 可以 | 推荐（B 直接接隧道） |

---

## 二、方案一：本地 Python 直接用

```
网站 PHP ──HTTP──▶ 127.0.0.1:8765（机器人内置服务）
```

### 1. 机器人配置

GUI → **QQ 转述 → 图片转本地链接**：

| 配置 | 填什么 |
|---|---|
| 图片转链接 | 需要发 MC 就勾 |
| **启用网站 API** | ✅ |
| **网站出站方式** | **此设备出站** |
| 监听地址 | `127.0.0.1` |
| 端口 | `8765` |
| 公网地址 | `https://pic.qingchengsep.top`（没有公网就留空） |
| **Token** | 长随机串 |
| 保留 QQ 图片原始文件名 | 可选 |

保存 → **断开 QQ → 重新连接**。

### 2. 网站调用

PHP 直接请求本机：

```php
$base  = 'http://127.0.0.1:8765';
$token = '机器人里填的 Token';
$since = (int)($_GET['since'] ?? 0);

$ch = curl_init("{$base}/api/qq/messages?since={$since}&limit=50");
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER     => ["X-Token: {$token}"],
    CURLOPT_TIMEOUT        => 10,
]);
echo curl_exec($ch);
curl_close($ch);
```

图片直接引用：

```html
<img src="http://127.0.0.1:8765/pic_http/xxx.jpg">
```

### 3. 要公网域名时

- 机器人监听改 `0.0.0.0`（或只让反代访问 `127.0.0.1`）
- 本机 Nginx 反代 8765
- cloudflared 指向本机 Nginx 或 `http://127.0.0.1:8765`

没有第二台机器时，方案一到这一步就结束了。

---

## 三、方案二：A/B 分离 + Python 中继

```
A（内网机器人） 192.168.1.23:8765
        │ HTTP（Token）
        ▼
B（服务器） other_qq_pic_sent/relay.py  127.0.0.1:18765
        │
        ▼
Cloudflare Tunnel → https://pic.qingchengsep.top
        │
        ▼
网站 PHP
```

中继负责：

- 每 2 秒从 A 拉 `/api/qq/messages`
- 把图片下载到 B 的 `relay_data/pic_http/`（文件名不变）
- 消息写入 B 的 `relay_data/relay.db`
- 对外提供与 A 相同格式的 API 和图片
- A 重启导致消息 id 归零时自动去重，网站看到的本地 id 始终递增

### 1. A 机器（bot）

GUI → **QQ 转述 → 图片转本地链接**：

| 配置 | 填什么 |
|---|---|
| 图片转链接 | 按需 |
| **启用网站 API** | ✅ |
| **网站出站方式** | **内网其他设备出站**（会自动把监听地址设为 `0.0.0.0`） |
| 监听地址 | `0.0.0.0`（让 B 能访问） |
| 端口 | `8765` |
| 公网地址 | `https://pic.qingchengsep.top` |
| **Token** | 长随机串（中继要用） |

保存 → 重连 QQ。

A 的防火墙只放行 B 的 IP：

```powershell
New-NetFirewallRule -DisplayName "QQPic from B" `
  -Direction Inbound -Protocol TCP -LocalPort 8765 `
  -RemoteAddress 192.168.1.50 -Action Allow
```

B 上先确认能到 A：

```bash
curl http://192.168.1.23:8765/health
```

### 2. B 机器：部署中继

只需要两个文件：

```
other_qq_pic_sent/relay.py
other_qq_pic_sent/relay.example.json   → 复制成 qq_relay.json 再改
```

B 需要 **Python 3.9+**，无第三方依赖。

`qq_relay.json`：

```json
{
  "upstream_base": "http://192.168.1.23:8765",
  "upstream_token": "A 的 webApiToken",
  "listen_host": "127.0.0.1",
  "listen_port": 18765,
  "token": "给网站用的 Token",
  "public_base": "https://pic.qingchengsep.top",
  "data_dir": "relay_data",
  "poll_seconds": 2
}
```

启动：

```bash
python3 other_qq_pic_sent/relay.py --config other_qq_pic_sent/relay.json
```

Windows：

```powershell
python other_qq_pic_sent\relay.py --config other_qq_pic_sent\relay.json
```

看到：

```
中继已启动：http://127.0.0.1:18765 -> 上游 http://192.168.1.23:8765
```

### 3. 可选：B 上的安全示例网站

`other_qq_pic_sent/web/` 已经带了一套只读示例（PHP 8+）：

- 登录页（会话 + CSRF + 登录限速）
- 图片墙（`textContent` 防 XSS，图片 URL 白名单）
- 服务端代理中继（Token 不出现在浏览器）

部署步骤见 `other_qq_pic_sent/README.md`；最简：

```bash
php other_qq_pic_sent/web/hash.php '你的密码'
# 把输出填到 PHP-FPM 的 QQ_PIC_PASSWORD_HASH
# 再配置 QQ_RELAY_BASE / QQ_RELAY_TOKEN / QQ_PIC_BASE
```

### 3. B 本机验证

```bash
curl http://127.0.0.1:18765/health
```

应返回 `"upstream_ok": true`，否则先查 A 的地址/防火墙/Token。

再验 API：

```bash
curl -H "X-Token: 给网站的Token" \
  http://127.0.0.1:18765/api/qq/messages
```

### 4. Cloudflare Tunnel

中继本身就是完整 HTTP 服务，**不需要 Nginx**，隧道直接指它：

```yaml
tunnel: 你的隧道ID
credentials-file: C:\Users\你\.cloudflared\你的隧道ID.json

ingress:
  - hostname: pic.qingchengsep.top
    service: http://127.0.0.1:18765
  - service: http_status:404
```

然后：

```bash
cloudflared tunnel route dns <隧道名> pic.qingchengsep.top
cloudflared tunnel run <隧道名>
```

公网验证：

```
https://pic.qingchengsep.top/health
```

### 5. 常驻运行

Linux：

```bash
cp other_qq_pic_sent/qq-relay.service /etc/systemd/system/qq-relay.service
# 改里面的 WorkingDirectory / User / 配置路径
systemctl daemon-reload
systemctl enable --now qq-relay
journalctl -u qq-relay -f
```

Windows：任务计划程序，程序填 `python.exe`，参数：

```
other_qq_pic_sent\relay.py --config other_qq_pic_sent\relay.json
```

起始目录填项目根目录，勾选“不管用户是否登录都运行”。

### 6. A/B 不在同一内网

用 frp / Tailscale / WireGuard 把 A 的 8765 映射到 B 的本地端口，例如：

```
A:8765  --frp-->  B:127.0.0.1:18764
```

中继配置改成：

```json
"upstream_base": "http://127.0.0.1:18764",
"listen_port": 18765
```

注意两个端口不要重复：

- `18764`：给中继去访问 A（frp 映射）
- `18765`：中继对外监听

### 7. 如果 B 已经有 Nginx

可以让 Nginx 反代中继，隧道指向 B 的 80：

```nginx
upstream qq_relay {
    server 127.0.0.1:18765;
}
server {
    listen 80;
    server_name pic.qingchengsep.top;

    location /pic_http/ { proxy_pass http://qq_relay; }
    location /api/qq/ {
        proxy_pass http://qq_relay;
        proxy_buffering off;
        proxy_read_timeout 35m;
    }
    location = /health { proxy_pass http://qq_relay; }
}
```

此时隧道 `service` 填 `http://127.0.0.1:80`。模板见 `docs/deploy/nginx-pic.conf`。

---

## 四、API（两个方案完全一样）

鉴权任选：

```
X-Token: <token>
X-Api-Token: <token>
Authorization: Bearer <token>
?token=<token>
```

### 1. 消息

```
GET /api/qq/messages?since=0&limit=50
```

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

- `since`：上次的 `last_id`，首次 0
- 方案二里这是 **B 的本地 id**，A 重启不会影响网站游标
- `since` 比当前 `last_id` 大（换库/重置）时，把 `since` 归零

### 2. 图片列表

```
GET /api/qq/images?limit=50
```

### 3. SSE 实时

```
GET /api/qq/stream?since=0
```

```
id: 13
event: message
data: {...}

: ping
```

### 4. 健康检查

```
GET /health
```

方案二额外返回：

```json
{
  "ok": true,
  "upstream_ok": true,
  "upstream_base": "http://192.168.1.23:8765",
  "last_sync": 1792912345.0,
  "last_error": "",
  ...
}
```

---

## 五、PHP 接入

### 原生 PHP

```php
<?php
header('Content-Type: application/json; charset=utf-8');

$base  = 'https://pic.qingchengsep.top';   // 方案一可改 http://127.0.0.1:8765
$token = '你的 Token';
$since = max(0, (int)($_GET['since'] ?? 0));

$ch = curl_init($base . '/api/qq/messages?since=' . $since . '&limit=50');
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER     => ['X-Token: ' . $token],
    CURLOPT_TIMEOUT        => 10,
]);
$body = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

if ($body === false || $code !== 200) {
    http_response_code(502);
    echo json_encode(['ok' => false], JSON_UNESCAPED_UNICODE);
    exit;
}
echo $body;
```

前端轮询：

```html
<div id="list"></div>
<script>
let lastId = 0;
async function load() {
  const r = await fetch('/qq.php?since=' + lastId);
  const data = await r.json();
  if (!data.ok) return;
  for (const msg of data.messages) {
    lastId = msg.id;
    const div = document.createElement('div');
    div.textContent = `[${msg.group_name}] ${msg.sender}：${msg.text}`;
    document.querySelector('#list').appendChild(div);
    for (const img of msg.images) {
      const el = document.createElement('img');
      el.src = img.abs_url;
      el.style.maxWidth = '240px';
      document.querySelector('#list').appendChild(el);
    }
  }
}
setInterval(load, 3000);
load();
</script>
```

### ThinkPHP 6/8

```php
use think\facade\Http;

public function messages()
{
    $resp = Http::withHeaders(['X-Token' => config('qq.token')])
        ->timeout(10)
        ->get(config('qq.base') . '/api/qq/messages', [
            'since' => (int)request()->get('since', 0),
            'limit' => 50,
        ]);
    return json($resp->json());
}
```

### Laravel 10/11

```php
use Illuminate\Support\Facades\Http;

$resp = Http::withToken(config('services.qq.token'))
    ->get(config('services.qq.base') . '/api/qq/messages', [
        'since' => request('since', 0),
        'limit' => 50,
    ]);
return response()->json($resp->json());
```

---

## 六、图片命名

- 默认：`20261025_143012_123_ab12.jpg`
- 勾选「保留 QQ 图片原始文件名」：`原始名.jpg`，重名自动 `_2`、`_3`
- 扩展名按真实图片格式修正
- A 生成什么名字，中继、Cloudflare、网站 URL 都保持什么名字

---

## 七、运维与安全

- **Token**：A 一个、B 中继一个，都可以不同；至少 32 位随机
- **监听地址**：中继默认 `127.0.0.1`，只让 CF/Nginx 访问；不要直接暴露 18765
- **A 的 8765**：只放行 B 的 IP
- **图片清理**：
  - A：`pic_http/`
  - B：`relay_data/pic_http/`
  - 建议都保留 7 天
- **消息历史**：方案二在 `relay_data/relay.db`，备份这个文件即可
- **隐私**：知道图片 URL 就能访问；需要私密就加 Cloudflare Access 或 Nginx 鉴权

---

## 八、常见问题

**Q：B 的 `/health` 显示 `upstream_ok: false`？**
A：B 访问不到 A。先在 B 上 `curl http://A:8765/health`，查 IP/端口/防火墙/Token。

**Q：A 重启后网站 `since` 很大，没有新消息？**
A：方案二不会：中继用自己的本地 id。方案一需要 PHP 检测 `last_id < since` 时把 `since` 归零。

**Q：图片 404？**
A：方案一查 A 的 `/pic_http/` 反代；方案二查 `relay_data/pic_http/` 是否已同步，以及 `public_base` 是否填写。

**Q：中继重启会不会重复消息？**
A：不会，SQLite 里有指纹去重；已同步的图片也不会重新下载。

**Q：中继要不要装整个项目？**
A：只要 `other_qq_pic_sent/relay.py` 和一份 JSON 配置即可，Python 标准库，无第三方依赖。
