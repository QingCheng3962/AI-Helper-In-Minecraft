# AGENTS.md — 给自动化助手 / 协作者的规则

> 这个文件是**硬性约束**，做任何改动前先读一遍。人类贡献者也可以参考。

## 一、Git 工作流（最重要）

1. **默认只在本地 `git commit`，不要 `git push`。**
   - 只有用户**明确要求**（"推送" / "push" / "推到 GitHub"）时才执行 `git push`。
   - 用户没提推送 → 改完、提交完就停下，在回复里说明「已本地提交 `<hash>`，等你要求再推」。
2. **不要删除 / 重建已被 Release 使用的 tag。**
   - 需要让 tag 指向新提交时用：`git tag -f <tag>` + `git push -f origin <tag>`（强制更新，不会动 Release）。
   - 历史事故：`git tag -d` + 重新创建导致 GitHub 上已有的 v3.2.0 Release 消失/变草稿。
3. **构建产物（jar）不进仓库**：`mc-song-mod/dist/`、`mc-music-bridge/dist/` 已在 `.gitignore`，发布走 GitHub Releases。
4. 提交前扫一眼 `git status`：不要提交 `config.json`（含 API Key / Cookie）、`affinity.json`、`daily.db`、`db_backup/`、`logs/`、`*.rar`、`node_modules/`、`NapCat/`、`.gradle/`、`build/`。
5. 提交信息用中文、一句主题 + 可选要点，风格参考 `git log`。

## 二、项目结构（速查）

| 路径 | 内容 |
| --- | --- |
| `aafm_py/` | Python 端：`gui.py`（tkinter 多标签页）、`controller.py`（事件分发 / 点歌 / 好感度 / 命令 / 群发）、`ai_player.py`（LLM 对话）、`netease.py`（网易云）、`bilibili.py`（视频推荐抓取）、`affinity.py`（好感度存取）、`dailydb.py`（每日数据 SQLite：当天幸运值 / 签到 + 跨天备份重建）、`docbook.py` + `log_saver.py` |
| `bot_engine.js` / `qq_engine.js` | Node 端：mineflayer 无头引擎 / OneBot 11 QQ 转述 |
| `mc-song-mod/` | 客户端点歌 Mod（Fabric **1.21.1 ~ 26.2**）：`src/main`（共用）+ `src/legacy`（1.21.x，mojmap + 重映射 Loom，含界面）+ `src/modern`（26.x，无界面，Java 25） |
| `mc-music-bridge/` | 服务端插件（可选，ZMusic v5 协议） |
| `HELP.md` | 玩家版游戏内帮助（`[触发词]帮助` 私聊内容的完整版） |
| `README.md` / `README_EN.md` | 总说明 + 更新日志（新增功能记得补 vX.Y.Z 条目，中英各一份） |
| `TODO.md` | 逐条改动记录（踩过的坑也记在这里） |

## 三、构建 / 测试

```powershell
# 单版本构建（增量 ~10-20 秒）
$env:JAVA_HOME = "C:\Program Files\Java\latest\jdk-25"
$env:GRADLE_USER_HOME = "C:\Users\HI\AppData\Local\Temp\opencode\gradle-home"
cd mc-song-mod
gradle.bat build "-Pmcver=1.21.11"      # 26.x 用 -Pmcver=26.2

# 全量 15 个版本 → dist/
powershell -ExecutionPolicy Bypass -File build_all.ps1
```

- Python 改动至少过一遍 `python -m py_compile aafm_py/*.py`。
- **只改 `aafm_py/` 的 bug 修复不需要重打 jar**；改了 `mc-song-mod/src/**` 或 `fabric.mod.json` 才需要。
- jar 名格式：`Song_<项目版本>_<游戏版本>.jar`；`fabric.mod.json` 的 `minecraft` 依赖必须是 `~${minecraft_version}`（按版本注入，别写死）。
- **版本号用语义化（SemVer）**：项目当前 `v0.3.2`、Mod 当前 `0.1.0`（都在 `README*.md` / `mc-song-mod/gradle.properties` / `package.json` 里改）；**README 只写最新版本，历史版本写 `更新日志.md`**。旧 tag（`v1.0`/`v2.0`/`v3.0`/`v3.1`/`v3.2.0`）和已发的 Release **保留不动**，别再按旧编号发版。

## 四、几个人肉踩过的坑（别重犯）

1. **服务器会改聊天**：公屏把 `http(s)://…` 换成 `[链接]`、把 `#xxxxxx` 当十六进制颜色码吃掉、`&` 是颜色符 → 直链要混淆（`¤`/`¥` + `· ¦ ∶`），id 标记用 `id:`，别用 `#`/`&`。
2. **服务器会把玩家聊天当系统消息重播** → Mod 必须同时监听 `ClientReceiveMessageEvents.CHAT` + `.GAME`。
3. **`/msg` 命令也算长度**：MC 单包 256 上限是整条命令（`/msg <名字> ` + 内容），文案长了会把机器人踢下线。
4. **网易云搜索不要带会员 Cookie**：带 `MUSIC_U` 会被返回空结果（误报「没找到」），搜索匿名、取直链才带 Cookie。
5. **机器人会被自己的话触发**：带触发词的日志不要转发到游戏；发出过的内容要记账（回声忽略）；同一句连点要熔断。
6. **26.2 聊天栏入口**从 `mc.gui.getChat()` 挪到了 `mc.gui.hud.getChat()` → 用反射两条路都试。
7. 玩家消息 / 指令只认**玩家自己发的**（Mod 里的 `mine` 标记），别人的点歌不能触发我的本地兜底，`[点歌]停止` 只停自己。
8. **提示词 ≠ 点歌触发词**：`帮助 / 好感度 / 视频推荐 / 今日幸运值` 认的是**「玩家配置 → 触发正则」里配的提示词**（`[DiaoHelper]`、`@DiaoHelper`…，正则，可能用逗号分隔多条），**不是** `[点歌]`。判断要复用 `ai_player._compile_trigger` / `_split_top_level_commas`（`[xxx]` 当字面量、其余当正则），别自己按逗号切。

## 五、默认行为约定

- 点歌默认**谁点谁听**（机器人 `/msg` 私聊 + Mod `onlyMySong`）。
- 帮助 / 好感度私聊或公屏回复都**不能包含半角 `[点歌]`**（会把 Mod 骗去点歌），统一用 `【点歌】`。
- 好感度默认：聊天 +1 / 5 分钟、点歌 +2 / 1 分钟、每日首次 +1；数据在 `affinity.json`。
- 视频推荐默认**发公屏**、来源**番剧追番榜**、**全服共用** 30 秒冷却；冷却过后再点要**换一批新的**（先避开刚推过的 id）。文案都在「杂项」页。
- 视频来源**五选一**（`config.misc.videoSource`：`anime` / `home` / `hot` / `rank` / `fav`），GUI 的 `_VIDEO_SOURCE_KEYS/_LABELS` 顺序必须和 `config.VIDEO_SOURCES` 一致。选 `fav` 时靠 `misc.biliCookie`（整条 Cookie 或只填 `SESSDATA` 值都行，`bilibili.cookie_header` 会归一化）+ `misc.biliFavMediaId`，`tick`/推荐线程里**不要**偷偷换成别的来源（用户要求只能二选一）。
- `bilibili.py` 用一个**共享 httpx.Client**（保留 cookie）：首页推荐必须先 `_warm()` 访问一次 `https://www.bilibili.com/` 拿 `buvid3`，否则直接干调会 `-412 request was banned`；该接口 `ps` 只能 12（`ps=30` 会 `-400 请求错误`），一次返回 30 条。收藏夹用 `x/v3/fav/resource/list`（`order=mtime`）**随机翻页** 2 页凑池子，失效条目（`attr` 位 / 标题含「已失效」）要跳过。
- 群发私聊（GUI「杂项」页）逐个 `/msg`，每个玩家之间有间隔（默认 0.5 秒），别调太小（会被当刷屏踢）；自触发防线照旧要记 `_remember_sent`。
- 「每日数据」一律走 `aafm_py/dailydb.py`（SQLite `daily.db`）：当天幸运值**第一次查询时随机定下来就落库**（别再用「日期+名字」现算，重启会变），签到记录也在这里。跨天由 `ensure_day()` 做「备份 → 删除 → 重建」，旧库进 `db_backup/db_backup_<时间>.db`，`player_stats`（连签/累计）要带到新库，不然连签会断。`tick` 里每秒调一次 `_maybe_daily_rotate()`。
- 每日签到（`[提示词]签到`）默认：金币在 `[最少, 最多]` 随机 + 连签加成，物品默认不给；发奖励靠配置里那**两行命令**（`{player} {value} {item} {count}`），命令不写 `/` 会自动补，长度按整条命令算（≤250）。
- 「今日幸运值 / 视频推荐 / 签到 / 帮助 / 好感度」都认**提示词**（`ai_config.triggerRegex`），不是 `[点歌]`。
