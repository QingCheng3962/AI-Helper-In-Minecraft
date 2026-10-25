# other_qq_pic_sent（Python 版）

方案二（A/B 分离）的独立目录：**B 机器上用 Python 收 A 的消息/图片，并直接带一个登录图片墙**。

```
A（bot，内网） ──HTTP──▶ B：other_qq_pic_sent
                         ├─ relay.py        去 A 拉消息/图片
                         ├─ webapp.py       登录页 + 图片墙 + 只读 API（Python 后端）
                         ├─ relay_data/     消息 SQLite + 图片（运行时生成）
                         └─ web/            HTML 模板 + CSS/JS
```

> 不需要 PHP、不需要 Nginx（可选）、不需要数据库服务；只依赖 Python 3.9+ 标准库。

---

## 一、目录说明

| 路径 | 作用 |
|---|---|
| `relay.py` | 内网同步：轮询 A、下载图片、SQLite 存消息 |
| `webapp.py` | Python Web 后端：登录、会话、CSRF、限速、图片墙、`/api/messages` |
| `hash_password.py` | 生成 PBKDF2 密码哈希 |
| `relay.example.json` | 配置模板（中继 + Web） |
| `web/templates/` | `login.html`、`index.html` |
| `web/static/` | `app.js`、`style.css` |
| `qq-relay.service` | systemd 模板（运行 webapp.py） |

---

## 二、快速开始

### 1. 配置

```bash
cp relay.example.json relay.json
```

编辑 `relay.json`：

```json
{
  "upstream_base": "http://192.168.1.23:8765",
  "upstream_token": "A 的 webApiToken",
  "listen_host": "127.0.0.1",
  "listen_port": 18765,
  "public_base": "https://pic.qingchengsep.top",
  "data_dir": "relay_data",
  "poll_seconds": 2,
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

### 2. 生成登录密码

```bash
python3 hash_password.py '你的密码'
```

把输出（`pbkdf2_sha256$...`）填到 `web.password_hash`。

> `web.secure_cookie`：正式 HTTPS 用 `true`；本地 `http://127.0.0.1` 调试时临时改 `false`。

### 3. 启动

```bash
python3 webapp.py --config relay.json
```

看到：

```
Web 后台：http://127.0.0.1:8080  （中继 API：http://127.0.0.1:18765）
```

### 4. 访问

- 本机：`http://127.0.0.1:8080`
- 公网：Cloudflare Tunnel `service: http://127.0.0.1:8080`
- 也可以在前面放 Nginx：`proxy_pass http://127.0.0.1:8080;`

登录后每 3 秒自动拉新消息，图片点开看大图。

---

## 三、端口与数据流

| 端口 | 谁 | 说明 |
|---|---|---|
| A:8765 | 机器人 | B 的中继去访问它 |
| B:18765 | relay.py | 给外部程序用的 Token API（只监听 127.0.0.1） |
| B:8080 | webapp.py | 登录页 + 图片墙；Cloudflare 指向它 |

```
Cloudflare → B:8080（webapp.py）→ relay_data/（本地 SQLite + 图片）
                ▲
                └── relay.py 后台线程每 2 秒从 A:8765 同步
```

- `relay_data/relay.db`：消息（本地 id 递增）
- `relay_data/pic_http/`：图片（文件名保持 A 的原名）
- 两个目录都不在 `web/` 下，浏览器无法直接列目录

---

## 四、安全设计（对应木马 / 暴库 / 越权）

| 风险 | 做法 |
|---|---|
| **木马/上传** | 没有任何上传接口；只读 GET；不能通过网站写文件 |
| **SQL 注入** | Web 端不执行用户 SQL；中继 SQLite 全部参数化查询 |
| **暴库** | 数据在 `relay_data/`（webroot 之外）；无数据库服务；无堆栈回显 |
| **越权** | 登录会话（随机 32 字节 token，内存存储）；`HttpOnly` + `SameSite=Strict`；登录后换 session |
| **CSRF** | 登录/退出都有 CSRF token；API 只读 |
| **暴力破解** | 每个 IP 5 次失败 / 10 分钟 |
| **XSS** | 前端只用 `textContent`；CSP 禁止内联脚本；图片只允许同源 `/pic_http/` |
| **SSRF** | 中继地址来自配置；Web 端不接受用户 URL |
| **路径穿越** | 图片文件名 `basename` + 目录校验 |
| **点击劫持** | `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` |
| **密码** | PBKDF2-SHA256（310,000 次），只存哈希 |
| **Token 泄露** | A 的 Token 只在中继进程内使用，浏览器拿不到 |

> 示例是**只读**的。以后如果加上传/发消息/管理后台，必须另做文件类型与大小校验、随机文件名、逐用户权限和审计日志。

---

## 五、常驻运行

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

## 六、Cloudflare

```yaml
ingress:
  - hostname: pic.qingchengsep.top
    service: http://127.0.0.1:8080
  - service: http_status:404
```

HTTPS 由 Cloudflare 负责；`webapp.py` 继续监听 `127.0.0.1` 即可。

---

## 七、常见问题

**Q：登录页 500 / 显示未配置密码？**
A：`web.password_hash` 为空。运行 `hash_password.py` 并填入，重启。

**Q：登录后一直“连接失败”？**
A：看 webapp 日志里的「同步失败」。先在 B 上 `curl http://A:8765/health`。

**Q：图片 404？**
A：确认 `relay_data/pic_http/` 有文件；等中继同步完成。

**Q：忘记密码？**
A：重新运行 `hash_password.py`，替换 `web.password_hash`，重启。

**Q：能不用内置网页，只拿 API？**
A：可以，用 `relay.py` 的 `/api/qq/messages`（Token），见 `../docs/QQ图片网站接入.md`。
