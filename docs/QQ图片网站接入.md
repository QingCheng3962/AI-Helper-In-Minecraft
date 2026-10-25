# QQ 图片 / 消息接入网站（PHP）

> 适用：把 QQ 群里的**消息和图片**实时展示到自己的网站。
> 机器人端已经内置 HTTP API，网站只需要读取 JSON / SSE，不需要直接连 OneBot。
>
> 示例域名：`pic.qingchengsep.top`（可换成你自己的）。

---

## 一、它是怎么工作的

```
QQ 群
  → OneBot（NapCat / SnowLuma）
  → qq_engine.js：解析文本 + 图片候选（file_id / file / url / path）
  → Python Controller：
       · 图片下载后保存到 pic_http/
       · 消息写入内存环形缓冲（最近 500 条）
       · 若开启「图片转链接」，同时把链接发到 MC 公屏
  → 内置 HTTP 服务（默认端口 8765）：
       GET /pic_http/<文件名>       图片文件
       GET /api/qq/messages         消息列表（JSON）
       GET /api/qq/images           图片列表（JSON）
       GET /api/qq/stream           实时消息（SSE）
       GET /health                  健康检查
```

> 纯图片消息以前会被跳过（没有文本），现在会以 `[图片]` 正常进入 API 和 MC 转述。

---

## 二、机器人端配置

打开机器人 GUI → **QQ 转述 → 图片转本地链接**：

| 配置 | 建议值 | 说明 |
|---|---|---|
| 图片转链接 | 需要发 MC 就勾选 | 不勾也能只给网站用 |
| **启用网站 API** | ✅ 勾选 | 开启 `/api/qq/*` |
| 监听端口 | `8765` | 与反代一致 |
| 公网地址 | `https://pic.qingchengsep.top` | API 返回的 `abs_url` 用这个前缀 |
| **Token** | 一串足够长的随机字符串 | 网站请求时带上；不要公开 |

对应 `config.json`：

```json
{
  "qq": {
    "imageAsLink": true,
    "imageHttpHost": "127.0.0.1",
    "imageHttpPort": 8765,
    "imagePublicBase": "https://pic.qingchengsep.top",
    "webApiEnabled": true,
    "webApiToken": "换成你自己的长随机串"
  }
}
```

> **安全建议**：机器人只监听 `127.0.0.1`，由 Nginx/Caddy 反代到公网；不要把 8765 直接暴露。
> 如果必须局域网访问，再用 `0.0.0.0`，并确保 Token 已设置。

---

## 三、Nginx 反向代理

```nginx
server {
    listen 443 ssl http2;
    server_name pic.qingchengsep.top;

    ssl_certificate     /etc/nginx/ssl/pic.qingchengsep.top.pem;
    ssl_certificate_key /etc/nginx/ssl/pic.qingchengsep.top.key;

    # 图片
    location /pic_http/ {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # API（SSE 必须关闭缓冲）
    location /api/qq/ {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 35m;
        chunked_transfer_encoding off;
    }

    location = /health {
        proxy_pass http://127.0.0.1:8765;
    }
}
```

Caddy 版本：

```caddy
pic.qingchengsep.top {
    reverse_proxy 127.0.0.1:8765
    # SSE 自动不缓冲
}
```

---

## 四、API 说明

### 鉴权

三种任选其一（Token 未设置时不需要）：

```
X-Token: <token>
X-Api-Token: <token>
Authorization: Bearer <token>
?token=<token>
```

推荐服务端请求用 `X-Token`，不要放进前端 URL。

### 1. 增量拉取消息（推荐 PHP 轮询）

```
GET /api/qq/messages?since=0&limit=50
```

响应：

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

- `since`：上次拿到的 `last_id`，首次传 0
- 返回按 `id` 升序；把 `last_id` 存起来，下次继续
- 消息只保存在内存（最近 500 条），机器人重启后从 1 开始；图片文件仍在磁盘

### 2. 最近图片

```
GET /api/qq/images?limit=50
```

返回 `{ok, images:[{name,url,abs_url,event_id,time,group_id,group_name,sender}]}`，最新在前。

### 3. 实时消息（SSE）

```
GET /api/qq/stream?since=0
```

事件格式：

```
id: 13
event: message
data: {"id":13,...}

: ping
```

适合浏览器 `EventSource`；PHP 一般用轮询即可。

### 4. 健康检查

```
GET /health
→ {"ok":true,"events":12,"images":3,"last_id":12,"base_url":"https://...","token_required":true}
```

---

## 五、PHP 接入示例

### 5.1 原生 PHP：给前端返回 JSON

```php
<?php
// qq_messages.php
header('Content-Type: application/json; charset=utf-8');

$token  = '换成你的 webApiToken';
$since  = isset($_GET['since']) ? max(0, (int)$_GET['since']) : 0;
$limit  = isset($_GET['limit']) ? min(200, max(1, (int)$_GET['limit'])) : 50;
$base   = 'https://pic.qingchengsep.top';

$url = $base . '/api/qq/messages?since=' . $since . '&limit=' . $limit;
$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER     => ['X-Token: ' . $token],
    CURLOPT_TIMEOUT        => 10,
    CURLOPT_SSL_VERIFYPEER => true,
]);
$body = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

if ($body === false || $code !== 200) {
    http_response_code(502);
    echo json_encode(['ok' => false, 'msg' => 'QQ API 请求失败'], JSON_UNESCAPED_UNICODE);
    exit;
}

echo $body;
```

前端轮询：

```js
let lastId = 0;

async function poll() {
  const res = await fetch(`/qq_messages.php?since=${lastId}&limit=50`);
  const data = await res.json();
  if (!data.ok) return;
  for (const msg of data.messages) {
    lastId = Math.max(lastId, msg.id);
    renderMessage(msg);
  }
}

function renderMessage(msg) {
  const box = document.createElement('div');
  box.className = 'qq-message';

  const head = document.createElement('div');
  head.textContent = `[${msg.group_name}] ${msg.sender}：${msg.text}`;
  box.appendChild(head);

  for (const img of msg.images || []) {
    const el = document.createElement('img');
    el.src = img.abs_url;      // 或 img.url（同域时）
    el.loading = 'lazy';
    el.style.maxWidth = '240px';
    box.appendChild(el);
  }
  document.querySelector('#qq-list').appendChild(box);
}

setInterval(poll, 3000);
poll();
```

### 5.2 ThinkPHP 6/8 控制器

```php
<?php
namespace app\controller;

use think\facade\Http;
use think\Response;

class Qq
{
    private string $apiBase = 'https://pic.qingchengsep.top';
    private string $token   = '换成你的 webApiToken';

    public function messages(): Response
    {
        $since = max(0, (int)request()->get('since', 0));
        $limit = min(200, max(1, (int)request()->get('limit', 50)));

        $resp = Http::withHeaders(['X-Token' => $this->token])
            ->timeout(10)
            ->get($this->apiBase . '/api/qq/messages', [
                'since' => $since,
                'limit' => $limit,
            ]);

        if (!$resp->isSuccess()) {
            return json(['ok' => false, 'msg' => 'QQ API 请求失败'], 502);
        }
        return json($resp->json());
    }
}
```

路由：

```php
Route::get('qq/messages', 'Qq/messages');
```

### 5.3 Laravel 10/11

```php
<?php
namespace App\Http\Controllers;

use Illuminate\Http\Client\Response;
use Illuminate\Support\Facades\Http;

class QqController extends Controller
{
    public function messages(): Response|array
    {
        $since = max(0, (int)request('since', 0));
        $limit = min(200, max(1, (int)request('limit', 50)));

        $resp = Http::withToken(config('services.qq.token'))
            ->timeout(10)
            ->get(config('services.qq.base') . '/api/qq/messages', [
                'since' => $since,
                'limit' => $limit,
            ]);

        if ($resp->failed()) {
            return response()->json(['ok' => false, 'msg' => 'QQ API 请求失败'], 502);
        }
        return response()->json($resp->json());
    }
}
```

`config/services.php`：

```php
'qq' => [
    'base'  => env('QQ_API_BASE', 'https://pic.qingchengsep.top'),
    'token' => env('QQ_API_TOKEN'),
],
```

`.env`：

```
QQ_API_BASE=https://pic.qingchengsep.top
QQ_API_TOKEN=换成你的 webApiToken
```

---

## 六、把图片放进网页的两种方式

1. **直接引用绝对地址**：`<img src="<?= $img['abs_url'] ?>">`
   要求 `imagePublicBase` 配成 `https://pic.qingchengsep.top`，且反代 `/pic_http/` 可访问。
2. **由 PHP 代理**（图片接口不公开时）：PHP 读取 `/pic_http/<name>` 再输出，需自行加登录校验和缓存。

---

## 七、运维与安全

- **Token**：至少 32 位随机串；泄露后立即改 `config.json` 并重启 QQ 转述。
- **监听地址**：反代方案用 `127.0.0.1`；`0.0.0.0` 会暴露到局域网/公网。
- **图片清理**：`pic_http/` 会持续增长，建议定时删除 7 天前的文件：
  ```bash
  find /path/to/AI-Helper-In-Minecraft/pic_http -type f -mtime +7 -delete
  ```
  （Windows 可用计划任务 + PowerShell）
- **消息不落库**：只存内存最近 500 条；需要长期保存请在 PHP 侧写入自己的数据库。
- **群过滤**：机器人「群号」填了谁就只转述/记录谁；留空 = 全部群。
- **隐私**：图片文件名不可猜但并非鉴权；群图片可能包含个人信息，公网展示前请确认群成员知情。

---

## 八、常见问题

**Q：API 返回空？**
A：确认已勾选「启用网站 API」、QQ 已连接、群号过滤正确；纯图片消息现在也会以 `[图片]` 出现。

**Q：图片 404？**
A：确认 `imagePublicBase` 与反代域名一致，且 `/pic_http/` 已反代到 8765；文件名区分大小写。

**Q：SSE 一直不推？**
A：Nginx 必须 `proxy_buffering off`，`proxy_read_timeout` 要大于心跳周期；PHP 轮询更简单。

**Q：重启机器人后 `since` 很大但没有新消息？**
A：内存 `id` 会从 1 重新开始。PHP 侧检测到 `last_id < since` 时把 `since` 归零，或改用自己的数据库游标。
