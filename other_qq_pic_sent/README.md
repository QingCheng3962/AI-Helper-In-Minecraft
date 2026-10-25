# other_qq_pic_sent

方案二（A/B 分离）的**独立目录**：B 机器上运行的中继 + 安全示例前后端。

```
A（bot，内网） ──HTTP──▶ B：other_qq_pic_sent
                         ├─ relay.py       内网接收 + 对外转发
                         ├─ relay_data/    消息 SQLite + 图片（运行时生成，不要放进 webroot）
                         └─ web/           PHP 登录页 + 图片墙（只读）
```

> 方案一（A 自己开、网站同机）不需要这个目录，直接用机器人 GUI 里的
> 「启用网站 API」 + `127.0.0.1:8765`。

---

## 一、目录说明

| 路径 | 作用 |
|---|---|
| `relay.py` | Python 中继：轮询 A、下载图片、SQLite 存消息、对外提供 `/api/qq/*` 与 `/pic_http/*` |
| `relay.example.json` | 中继配置模板 |
| `qq-relay.service` | Linux systemd 模板 |
| `web/` | PHP 安全示例（登录 + 消息/图片展示） |
| `web/public/` | 唯一对外目录 |
| `web/src/` | 配置与会话逻辑（**不能**放进 webroot） |
| `web/hash.php` | 生成登录密码哈希 |
| `web/nginx.conf.example` | B 的 Nginx 模板 |

---

## 二、启动中继

1. 复制 `relay.example.json` 为 `relay.json`，修改：

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

2. 启动：

```bash
python3 relay.py --config relay.json
```

3. 验证：

```bash
curl http://127.0.0.1:18765/health
```

`upstream_ok: true` 才说明连上 A。

---

## 三、启动示例网站

要求：PHP 8.0+（推荐 8.2/8.3）、PHP-FPM、Nginx。

### 1. 生成密码哈希

```bash
php web/hash.php '你的登录密码'
```

### 2. 配置环境变量（PHP-FPM）

在 PHP-FPM pool（如 `/etc/php/8.2/fpm/pool.d/www.conf`）里：

```ini
env[QQ_RELAY_BASE] = http://127.0.0.1:18765
env[QQ_RELAY_TOKEN] = 给网站用的 Token
env[QQ_PIC_BASE] = https://pic.qingchengsep.top
env[QQ_PIC_PASSWORD_HASH] = 上一步输出的哈希
```

重启 PHP-FPM：

```bash
systemctl restart php8.2-fpm
```

> 不要把 Token/密码哈希写进 `config.php` 再提交到仓库。

### 3. Nginx

```bash
cp web/nginx.conf.example /etc/nginx/conf.d/qqpic.conf
# 改域名、目录、PHP-FPM socket
nginx -t && systemctl reload nginx
```

### 4. 验证

- 打开站点 → 应跳转登录页
- 未登录直接访问 `/api.php` → 303 到登录页（不会泄露数据）
- 登录后 → 每 3 秒轮询新消息
- `curl -I https://你的域名/` 应看到 `X-Frame-Options: DENY`、CSP 等

---

## 四、安全设计（对应你担心的几类问题）

| 风险 | 本示例的做法 |
|---|---|
| **木马/上传** | 没有任何上传接口；只读 GET；`web/public` 之外不放可执行文件；Nginx 禁止点文件/备份文件 |
| **SQL 注入** | Web 端不连数据库；中继的 SQLite 全部使用参数化查询 |
| **暴库** | 中继数据在 `relay_data/`（不在 webroot）；Web 端不存密码明文；登录限速 5 次/10 分钟 |
| **越权** | 会话登录（`HttpOnly` + `SameSite=Strict` + 登录后 `session_regenerate_id`）；`api.php` 强制登录；没有管理接口 |
| **CSRF** | 登录表单带 CSRF token；只读接口无副作用 |
| **XSS** | 前端全部用 `textContent`，不拼 HTML；CSP 禁止内联脚本；图片 URL 白名单校验 |
| **SSRF** | 中继地址来自配置，不接受用户传入 URL；cURL 只允许 HTTP/HTTPS、禁止跟随跳转 |
| **路径穿越** | 中继用 `basename` 白名单校验文件名；Nginx 禁止 `..`/点文件 |
| **点击劫持** | `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` |
| **Token 泄露** | Token 只在 PHP 服务端使用，浏览器 JS 拿不到 |
| **密码** | `password_hash()`（bcrypt/argon2），不存明文；哈希放环境变量 |

> 这是**只读展示**示例。如果你以后加“上传图片/发消息/管理后台”，必须额外做：
> 文件类型与大小校验、随机文件名、存到 webroot 之外、逐用户权限、审计日志。

---

## 五、文件与权限建议

```bash
# 中继数据目录：只给运行用户
chown -R qqrelay:qqrelay /opt/other_qq_pic_sent/relay_data
chmod 700 /opt/other_qq_pic_sent/relay_data

# Web 代码只读
chown -R www-data:www-data /var/www/other_qq_pic_sent/web
find /var/www/other_qq_pic_sent/web -type f -exec chmod 644 {} \;
```

- 中继和 PHP-FPM 可以用不同用户；中继只需要读 A、写 `relay_data/`。
- `relay_data/` 绝不能放在 `web/public` 下。
- 备份只需要 `relay_data/relay.db` 和 `relay_data/pic_http/`。

---

## 六、Cloudflare

推荐链路：

```
浏览器 → Cloudflare → cloudflared(B) → Nginx(B:80) → PHP/中继
```

- 隧道 `service: http://127.0.0.1:80`
- 或者不用 Nginx：`service: http://127.0.0.1:18765`（但就没有 PHP 登录页了，只暴露 API/图片）
- 需要更强鉴权时，可在 Cloudflare Zero Trust 加 Access 策略

---

## 七、常见问题

**Q：登录页提示“管理员尚未配置 QQ_PIC_PASSWORD_HASH”？**
A：环境变量没生效。确认 PHP-FPM pool 的 `env[...]` 已加并重启 FPM；用 `php -i | grep QQ_` 检查 CLI 是否能看到。

**Q：登录后一直“连接失败”？**
A：`QQ_RELAY_BASE`/`QQ_RELAY_TOKEN` 不对，或中继没启动。先在 B 上 `curl 127.0.0.1:18765/health`。

**Q：图片不显示？**
A：检查 `QQ_PIC_BASE` 与 Nginx `/pic_http/` 反代；或中继 `public_base` 没配。

**Q：忘记密码？**
A：重新生成哈希并更新 `QQ_PIC_PASSWORD_HASH`，重启 PHP-FPM。

**Q：中继日志在哪？**
A：systemd 用 `journalctl -u qq-relay -f`；手动运行直接看终端输出。
