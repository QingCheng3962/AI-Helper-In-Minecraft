# other_qq_pic_sent（B 端，Python）

方案二（A/B 分离）的独立目录：B 机器上用 Python 收 A 的消息/图片，并直接提供网页。

```
A（bot，内网） ──HTTP──▶ B：other_qq_pic_sent
                         ├─ relay.py     去 A 拉消息/图片
                         ├─ webapp.py    网页 + 图片 + 只读 API（Python 后端）
                         ├─ relay_data/  SQLite + 图片（运行时生成）
                         └─ web/         HTML 模板 + CSS/JS
```

> **默认免登录**：打开网页就能看，不需要密码。
> 图片地址是 `https://pic.qingchengsep.top/图片名`（不带 `/pic_http/`）。
> 需要登录时把 `web.require_login` 改成 `true`，配置密码哈希即可。

---

## 一、目录

| 路径 | 作用 |
|---|---|
| `relay.py` | 轮询 A、下载图片、SQLite 存消息 |
| `webapp.py` | Python Web：公开图片墙 + `/api/messages`（可选登录） |
| `hash_password.py` | 需要登录时生成密码哈希（PBKDF2） |
| `relay.example.json` | 配置模板 |
| `web/templates/` | `index.html`、`login.html` |
| `web/static/` | `app.js`、`style.css` |
| `qq-relay.service` | systemd 模板 |

---

## 二、启动（免登录，最简单）

```bash
cd other_qq_pic_sent
cp relay.example.json relay.json
```

编辑 `relay.json`：

```json
{
  "upstream_base": "http://192.168.1.103:8765",
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

启动：

```bash
python3 webapp.py --config relay.json
```

Windows：

```powershell
python webapp.py --config relay.json
```

打开：

```
http://127.0.0.1:8080
```

直接就是图片墙，不需要登录。

---

## 三、需要登录时（可选）

`relay.json`：

```json
"web": {
  "require_login": true,
  "password_hash": "先运行 hash_password.py 生成的哈希"
}
```

生成哈希：

```bash
python3 hash_password.py '你的密码'
```

Windows：

```powershell
python hash_password.py "你的密码"
```

重启 `webapp.py` 后，打开网页会先跳登录页。

---

## 四、图片地址

B 对外：

```
https://pic.qingchengsep.top/20261025_143012_123_ab12.jpg
```

- 不带 `/pic_http/`（也可以用，兼容旧地址）
- 图片文件名保持 A 的原名（勾选「保留 QQ 图片原始文件名」时）
- 中继 API 的 `images[].url` 是 `/文件名`，`abs_url` 是完整公网地址

中继内部 API 仍兼容：

```
http://127.0.0.1:18765/pic_http/文件名
http://127.0.0.1:18765/文件名
```

---

## 五、端口与数据

| 端口 | 谁 | 说明 |
|---|---|---|
| `8765` | A | 机器人内置 API |
| `18765` | B | 中继内部 API（只监听 127.0.0.1） |
| `8080` | B | 网页；Cloudflare 指向它 |

数据：

```
relay_data/relay.db        # 消息（本地 id 递增）
relay_data/pic_http/       # 图片
```

- 备份这两个即可
- `relay_data/` 不在 `web/` 下
- `relay.json` 和 `relay_data/` 已加入 `.gitignore`

---

## 六、Cloudflare

```yaml
ingress:
  - hostname: pic.qingchengsep.top
    service: http://127.0.0.1:8080
  - service: http_status:404
```

HTTPS 由 Cloudflare 负责；webapp 监听 `127.0.0.1` 即可。

---

## 七、常驻运行

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

---

## 八、安全说明（默认公开访问）

默认 `require_login: false`，意味着：

- 任何知道 `https://pic.qingchengsep.top` 的人都能看图片和消息
- 适合公开群/展示用途
- 不想公开就设 `require_login: true` + 密码哈希

其他防护仍然有效：

| 风险 | 做法 |
|---|---|
| 木马/上传 | 没有上传接口，只读 |
| SQL 注入 | Web 端无 SQL；中继 SQLite 参数化 |
| 暴库 | 数据在 `relay_data/`（webroot 外），无数据库服务 |
| XSS | 前端只用 `textContent`；CSP 禁止内联脚本 |
| SSRF | 中继地址来自配置，不接受用户 URL |
| 路径穿越 | 图片名 basename + 目录校验 |
| 点击劫持 | `X-Frame-Options: DENY` |
| Token | 只在中继进程内，浏览器拿不到 |

---

## 九、常见问题

**Q：网页打不开？**
A：确认 `webapp.py` 在运行，监听 `127.0.0.1:8080`；Cloudflare 的 service 是 `http://127.0.0.1:8080`。

**Q：`/health` 里 `upstream_ok: false`？**
A：B 访问不到 A。先在 B 上 `curl http://A:8765/health`。

**Q：图片 404？**
A：等中继同步；检查 `relay_data/pic_http/` 是否有文件。

**Q：想改成登录访问？**
A：`web.require_login: true`，运行 `hash_password.py` 填 `password_hash`，重启。

**Q：只想给外部程序 API，不要网页？**
A：用 `relay.py` 的 `/api/qq/messages`（Token）；webapp 仍可同时提供网页。
