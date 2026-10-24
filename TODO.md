# TODO

书与笔（document → book and quill）功能待办。opencode/编辑器可直接识别本文件的 `- [ ]` 复选框。

## 进行中

- [x] #5：簽到交易失敗不發獎；唯讀、查詢／寫入／提交失敗與雙連線並發測試覆蓋。
- [x] #7：以伺服器封包及 NBT／Components 內容核對寫書；無關／本機更新不推進，簽名驗證作者與書名，創造兜底亦須內容回執。
- [x] #6：新增持久化獎勵狀態與固定快照，安全補送未送出項目；管道不明結果不重送，跨日先建立完整新庫再備份替換，避免遺失欠發紀錄。

- [x] 修复“设置行数却写超”：`docbook.paginate` 改为严格单遍排版，`wrap_line` 字宽保守估计（英文 ≤6、`@~`=7、空格 4、窄字符 3、中文/全角 9），保证每行 ≤114px、每页 ≤ 行数/字数上限
- [x] `bookwriter.js`：支持“编辑为成书（署名）”，标题模板 `{number}` 按第几本替换（只含数字）
- [x] `bookwriter.js`：写入进度记录（从 `startPage` 开始、`bookStart` 起始编号），每写完一本回调进度
- [x] `bookwriter.js`：支持停止/暂停（`shouldStop`），书用完时保留进度
- [x] `bot_engine.js`：`writeBook` 透传 `startPage/bookStart/signBook/titleTemplate`；新增 `bookControl`（stop/pause）+ `bookProgress` 事件
- [x] Python：`BotEngine` / `Controller` 桥接 `start_page/book_start/sign_book/title_template`、`bookControl`
- [x] Python：`book_progress.json` 进度读写；`continue_book()` / `rewrite_book()` / `pause_book()`
- [x] GUI：「编辑为成书（署名）」勾选 + 书名模板输入；按钮 停止/暂停/重新写入/从未写入处继续；进度显示
- [x] README：成书署名、编号、进度与停止/暂停/继续说明
- [x] 字数与行数冲突：分页只按「每页最大行数」，字数仅保留 1024 硬上限（移除 GUI「每页最大字数」输入框，进度/继续统一按行数排版）
- [x] 绝对不超 14 行：保守字宽（非 ASCII=10px、ASCII 取上界）+ 2px 安全边距（114→112）+ `pageLines` 硬锁 1..14（配置/GUI/`paginate` 三重），保证每页渲染 ≤14 行、成书不被截断；预览显示每页最宽像素
- [x] 「从第 X 册开始写入」按钮：起始页 =(X−1)×每本页数、`bookStart=X`（署名 `{number}` 从 X 起），越界提示
- [x] QQ 网关框架可切换（默认 SnowLuma）：`QqConfig.framework` + `launcherPath`；Controller 按框架启动（SnowLuma 自动找 launcher.bat/exe，NapCat 用内置）；自动写配置仅 NapCat；GUI 顶部「启动框架」下拉 + 「启动路径」；默认正向地址 ws://127.0.0.1:3001
- [x] SnowLuma 鉴权自动读取：从 `<launcherPath>/config/onebot_*.json` 的 `wsServers[0]` 自动取 host/port/accessToken（解决 WS 401），GUI 加载时自动回填、连接时兜底；手填优先
- [x] QQ 配置按框架拆分：`qq.snowluma` / `qq.napcat` 各自独立（launcherPath/mode/url/accessToken/listenHost/listenPort/autoConfigNewAccounts），旧扁平键自动迁移；`QqConfig.active()`
- [x] NapCat 也支持选文件夹加载（`_napcat_base_dir/_inner_dir/_config_dir/_cache_dir`，留空用项目自带 NapCat/）
- [x] QQ 转述页分模块重排：网关设置 / 双向转发 / 账号安全 / 图片转链接；切换框架自动切换该框架的路径·地址·Token
- [x] SnowLuma token 用真实 WS 握手逐个探测选用（解决 SnowLuma 重启后文件 token 失效导致的 401）；多账号候选排序（上次登录→最近修改）
- [x] 方向开关与网关解耦：新增 `qq.qqToMcEnabled`（QQ→MC 转发开关，关闭只停止发游戏、不断开 QQ、不影响 MC→QQ）；移除会误杀网关的顶部「启用 QQ 转述」勾选，网关由 连接/断开 控制；修 `QqConfig.validate` 重复段；启动探测公网 IP 改后台线程避免卡界面
- [x] 游戏内点歌（新目录 `mc-music-bridge/`，Paper/Spigot 插件 ZMusicBridge，Java/Gradle）：玩家 `[点歌]歌名`（触发词可配）或 `/zmplay 歌名`；插件按 ZMusic v5 `zmusic:packet` 协议自己当服务端，收到 `client.hello` 回 `server.hello`、点歌发 `server.play`（`ZMPK`+v1+JSON envelope，校验 UUID/`mode:replace`/`audio.type:url`/请求头黑名单）；歌曲搜索自写（网易云公开搜索 + `music.163.com/song/media/outer/url` 直链）；`broadcast`/冷却/自定义 Referer；**无需** ZMusic 官方插件/云 API/device_token；已用腾讯镜像的 Gradle Wrapper 构建通过（`dist/ZMusicBridge-1.0.0.jar`），协议编解码做过程序自测
- [x] 主 GUI「点歌」标签页（改为客户端 Mod 方案）：触发词 + 「让机器人点歌」（机器人发 `触发词+歌名` → 所有装了 Mod 的人一起听）；「机器人回复 Mod 下载链接」开关 + 链接 + 文案(`{url}`) + 冷却；说明服务端零改动；`config.json` 的 `song` 段（trigger/replyEnabled/modUrl/replyText/replyCooldownSeconds）
- [x] 控制器：`_maybe_song_reply` —— 看到聊天里的 `[点歌]` 就在公屏回一条 Mod 下载链接（按玩家冷却、跳过机器人自己、没填链接则日志告警），并吞掉这条消息不让 AI 再插嘴
- [x] 新目录 `mc-song-mod/`：**客户端点歌 Mod**（Fabric 1.21.11 / Java 21，Loom 1.19 + JDK25 构建）：监听聊天里的 `[点歌]歌名`（自己发的 + 服务器广播来的），本地用网易云公开接口搜歌 + 内嵌 JLayer 经 `javax.sound` 播放（系统音频，可配音量）；纯客户端触发 → 等于全服点歌且**服务端零改动**；无需 ZMusic 服务端插件/云 API
- [x] 外置登录/网络服务异常友好化：`bot_engine.js` 归类 5xx/非 JSON/超时（`serviceError` 事件）；控制器按「首次 + 每 60 秒一条」提示「外置登录/会话服务暂时不可用（对方问题）」，并进入**指数退避**重连（5s→10s→…→最高 900s），连上后（spawn）自动清除；顺带修 `spawn` 分支里未判空的 `ai_player.set_online(True)`
- [x] 外置登录请求统一 **20s 硬超时**：`3rdparty_auth.js` 的 OAuth/角色/MC token 请求改用 `AbortSignal.timeout(20000)`（原 `fetchRoles` 写的 `timeout:5000` node-fetch 不认=没超时）；`bot_engine.js` 在加载 mineflayer 前替换 `node-fetch` 出口，给 yggdrasil 的 `session/join` 也加 20s 超时（原来完全没超时、会无限挂起）；可用 `AAF_AUTH_TIMEOUT_MS` 覆盖；已用「不可达 IP」实测到点 abort
- [x] 超时后的 `AbortError`（`The user aborted a request.`）归类为 `timeout` → 同样走友好提示 + 退避，不再当普通红字报错
- [x] 点歌 Mod 实测修复两处：①网易云直链是 `302 → http://`，JDK `HttpURLConnection` 不跟随 https→http（实测拿到空 302）→ `SongPlayer` 改为手动逐跳解析 `Location`（http 失败回退 https）；②`openImpl()` 在解码首帧前被调用，`Decoder` 频率/声道为 0 → `0.0Hz/0channels` 打不开音频线路 → 改为像 JLayer 自带设备那样**首帧后再懒建 `SourceDataLine`**；用真实歌曲在本机跑通（`played=true` 7.7s、无报错）。另：Mod 里所有提示切回客户端主线程 + 后台任务 `catch(Throwable)`，不再静默失败
- [x] 点歌停止：Mod 配置 `stopWord`（默认 `停止`），发 `[点歌]停止` 即 `SongPlayer.stop()` 并提示「已停止播放」；机器人侧 `SongConfig.stopWord` 同步跳过（`[点歌]停止` 不再回下载链接）；GUI「点歌」页新增「停止词」输入框
- [x] P0 修复「只有起风了能放，别的歌不行」：根因是 `outer/url` 对 `fee=1` 的会员歌会跳 `music.163.com/404`；改用 `api/song/enhance/player/url`（**320k 直链**），并把默认 Cookie `os=pc; appver=2.9.7` 带上；搜索取**前 5 条候选**、优先挑**完整版**（识别 `freeTrialInfo` 试听片段）；拿不到时给出明确原因（需会员 / 暂不可用）
- [x] 网易云会员：Mod 新增 **扫码登录**（`[点歌]登录`，用免加密的 `login/qrcode/unikey` + `qrcode/client/login` 轮询，803 后从 `Set-Cookie` 取 `MUSIC_U` 自动写入 `neteaseCookie`）；`[点歌]登出` 清除；未登录时 VIP 歌提示「45s 试听」
- [x] 链接点歌：`[点歌]<网易云链接>` / `[点歌]<歌曲id>` 直接播放（`extractId` 支持 `id=`、纯数字、短链跟跳转；`api/song/detail` 取标题歌手，兼容 `ar/al` 字段）
- [x] Mod 播放成功后追加一行**链接提示**（`modUrl` + `hintText`，`{url}` 占位），样式同机器人那条
- [x] 机器人日志页新增**歌曲日志**（新 tag `song`，青色）：每次点歌/停止/登录都记一条，如 `[点歌] Steve 点了歌：起风了（已回复 Mod 下载链接）`
- [x] 多版本编译：源码改用 **Mojang 官方映射（mojmap）**（26.x 无 yarn，一份源码跨 1.21.1~26.2）；`build.gradle` 用 `-Pmcver=` 参数化并自动解析 loader / Fabric API；产物命名 `Song_<项目版本>_<游戏版本>.jar`；`build_all.ps1` 一键编 15 个版本到 `dist/`
- [x] **架构改为「机器人代播」**：方案从「每个玩家各自登录」改成「机器人在控制页登录会员 → 替全服放 VIP 歌」。新增 `aafm_py/netease.py`（搜索/取 320k 直链/识别试听/链接与 id 解析/扫码 unikey+轮询）；控制器 `_resolve_and_post` 用机器人 Cookie 解析后把「`[点歌]歌名 - 歌手 | 直链`」发到公屏；Mod 改为**优先播放机器人直链**，等不到（3.5s）再**本地兜底**；`song` 段新增 `postPlayLink` / `neteaseCookie`
- [x] 机器人 GUI「点歌」页新增**网易云扫码登录**（线程里走 unikey + 轮询，成功自动写入 `neteaseCookie` 并保存）+「机器人代播」开关 + Cookie 输入框 + 状态
- [x] 修复多版本构建：`build.gradle` 改为**内置 Fabric API 版本表**（15 个版本）+ 联网兜底重试，解决「找不到与 MC 1.21.1 匹配的 Fabric API 版本」
- [x] **修复「机器人自己发的播放消息被再次当成点歌」导致的无限循环**（服务器会把 URL 变成 `[链接]`，所以原来的 `| http` 判断失效）：①宽松自身名判断 `_is_self_name`（去掉 `[称号]`/括号再比）；②含 ` | ` 视作机器人自己的格式直接跳过；③刚发过的歌 30s 内去重（`_song_posted_at`）
- [x] 点歌改为**以机器人为准**：Mod 新增 `localFallback`（默认 **false**）；等不到机器人直链时不再自己搜歌（提示「机器人没有响应」），避免各端版本不一致 / VIP 只听 45s；需要本地兜底时在 `config/songmod.json` 打开
- [x] 修复 GUI「扫码登录」成功但不自动填充：`netease.QrLogin` 改用**持久 httpx.Client**，从 Cookie jar 取 `MUSIC_U`（比只解析响应头可靠）；成功后写入输入框 + 立即保存 `config.json`
- [x] 机器人播放文案做成好看的模板：`song.playText`（默认 **`{player} 点了一首 {music}`**，另支持 `{title}`/`{artist}`），发出时自动追加 ` | 直链`；GUI「点歌」页可改。例：`[点歌]Steve 点了一首 夏天的风 - 火羊瞌睡了 | http://…mp3`
- [x] Mod 精简为**只播机器人的直链**：删掉本地搜索/本地登录（`MusicSearch`/`NeteaseLogin`）与 `localFallback`；等不到机器人就提示「**机器人不在线（或没登录网易云），点歌失败**」，绝不做本地兜底
- [x] **机器人真播放队列** + 新 GUI 页「**点歌台**」：`netease` 补上歌曲时长；控制器按队列逐首发（当前放完再发下一条，时长取自接口，未知兜底 3 分钟）；页面可**点歌**（触发词可改）、看**正在播放**与**队列**、**只看自己的**（默认开，可切全部）、**清空队列并停止**；队列里**别人的歌**后面有「**我也要听**」按钮，点了把它再排进队列
- [x] 「点歌台」**改为做进 Mod（游戏内 GUI）**，py 里那个页面已删除：Mod 内按 **K**（可在「按键设置」改）打开「点歌台」——点歌 / 正在播放 / 我点过的 / 填自己的 Cookie / 停止；`KeyMapping` 用反射构造以兼容不同版本的构造签名
- [x] Mod **保底加回来**：`localFallback`（默认 true，有机器人在线**优先用机器人**，3.5s 等不到就本地搜歌）+ 恢复 `MusicSearch`；玩家的 Cookie 由自己抓（Mod 界面里填，不搞 Mod 内扫码）
- [x] 登录体验：GUI 直接把**二维码画出来**（`qrcode`+PIL，已加入 requirements），另有「在浏览器打开 / 复制链接」按钮；`QrLogin` 用持久 `httpx.Client` 从 Cookie jar 取 `MUSIC_U`
- [x] 多版本：**26.x 单独走了另一套**（Minecraft 26.x 未混淆 + 渲染 API 大改）——非重映射 Loom 插件、无 mappings、Java 25、`src/modern`（无界面）；1.21.x 用 mojmap + 重映射 Loom、`src/legacy`（含界面）；26.1.2 已编译通过
- [x] **VIP 歌真正的修复：改用 eapi 接口**。根因：旧接口 `api/song/enhance/player/url` **不认会员权限**（`fee=1` 的会员歌只给 null；游客才给 45s 试听）。改用 **eapi**（`interface3.music.163.com` + AES-128-ECB(`e82ckenh8dichen8`) + md5 摘要 `nobody{path}use{data}md5forencrypt` 的 payload）后，**实测孤勇者拿到 320k 完整直链**（`payed:1`, `fee:1`, 无试听）。机器人（Python，`cryptography`）与 Mod（Java 原生 AES）都已实现，有 Cookie 时优先走 eapi，失败再退回旧接口
- [x] 兜底等待 3.5s → **30s**（避免机器人还在启动/登录时被本地兜底抢先；期间收到机器人直链即以机器人为主）；`SongScreen` 重写（自算宽度居中避开 `drawCenteredString` 签名差异、整体 try/catch 防崩）；按键反射改用 `getDeclaredConstructors`；新增**聊天入口 `[点歌]台`**（按键打不开时的备用）+ `SongConfig.guiWord`
- [x] **K 键没反应的真正原因**：反射里写死了开发期类名 `Class.forName("net.minecraft.client.KeyMapping$Category")`——正式环境 MC 类名是**混淆后**的（`net.minecraft.class_304$…`），开发名不存在 → ClassNotFoundException → **按键根本没注册上**（开发环境能跑，正式环境不生效）。已改为**不看类名/字段名**的写法：遍历构造器只按「参数类型/个数」匹配（`String, InputConstants.Type, int, X`），第 4 参是 String 就用分类名，否则取该类型的一个**静态实例**（`Category` 的 MOVEMENT/MISC…）→ 全程 name-free，1.21.1~1.21.11 通用。注册后 `[点歌]台`/K 都能开界面，且可在「按键设置」里改键。
- [x] 客户端事件回调全部加 try/catch（`onChat` 外层包一层；`SongPlayer` 线程 catch `Throwable`）——任何异常都不再可能把客户端连接搞崩
- [x] **VIP 播放的真实结论**（用实测推翻之前的猜测）：**旧接口 `api/song/enhance/player/url` 带有效 Cookie 时本来就认会员** —— 实测「孤勇者/爱如火」都返回 320k 完整直链（`trial=0`）；不带 Cookie 才只有 30~45s 试听。之前那些「会员歌拿不到」的结论，是当时用的 Cookie 无效/过期（网上粘贴的串）造成的。所以机器人（httpx）**走旧接口**即可，eapi 仅作兜底（Python 侧 eapi 会被服务端判「参数错误」，Java 侧正常）
- [x] 机器人**防自触发/防刷屏**加固：带音频直链的消息一律不当作新点歌；payload 含播放模板固定文字（默认「点了一首」）的跳过；去重 key 归一化（去括号备注/`by …`）60s 内不重复；20 秒内超过 12 次点歌直接忽略。实测把机器人自己发的消息喂回去（且用不认识的名字绕过 `_is_self_name`）→ 正确忽略，队列不增长
- [x] 机器人 GUI「点歌」页新增 **「验证登录」按钮**：`netease.check_login(cookie)` 显示账号昵称 + `vipType`，并实测一首会员歌能不能拿到完整直链；通过就顺手存进 `config.json`
- [x] **公屏把直链替换成「[链接]」导致 Mod 收不到 URL（机器人有输出却唤起不了 Mod、30s 后走本地兜底）**：服务器聊天插件会把 `http(s)://…` 整条替换成 `[链接]`，Mod 自然解析不到直链。改为机器人发**混淆直链**：`[点歌]文案 | ¤m801·music·126·net¦…·mp3`（`¤`=http、`¥`=https，去掉协议头 + 去掉 `?vuutv=…`（实测去掉照样播）+ 把 `. / :` 换成 `· ¦ ∶`）→ 消息里**完全不出现** `http`/`://`/`.`，混淆过滤认不出来；Mod 侧 `extractObfuscatedUrl` 还原成完整直链再播。实测：223 字符（<256 上限）、还原后 HTTP 200 `audio/mpeg`、会员歌无「试听」标记；把机器人这条消息喂回机器人 → 仍被正确忽略（防循环）
- [x] 直链混淆的边界：文案+直链超过 250 字符时**自动拆两条** —— 先发一条**不带触发词**的纯文案（Mod 忽略、玩家看得见谁点了什么），再发 `[点歌]歌名 | ¤混淆直链`；实测两条都 ≤256 字符且直链完整可播
- [x] **快速响应、不排队等**：新增 `song.instantPlay`（默认开，GUI「点歌」页可关）—— 新点歌**立刻插播**，不再按歌长等上一首放完（以前等 3 分钟，客户端 Mod 会误以为机器人没响应、30s 后自己本地搜一遍 = 双重播放）。实测：当前歌还剩 202s 时，B 一点，1 秒内就切到 B 的歌
- [x] **同一首歌再点 = 直接重播，不重新搜索**：`_song_recent` 缓存（`歌名`/`歌名 - 歌手`/渲染文案/玩家当初的关键词 多种别名，8 分钟内有效）→ 换成新的点歌人重发原直链，搜索次数 **0**（实测）。防回声：同一首歌 20 秒内被"再点"超过 4 次直接忽略（万一是自己消息的回声，防死循环）
- [x] **`[点歌]help` → 机器人 `/msg` 私聊该玩家全部用法**（6 条）：歌名 / 停止 / 台 / help / 登录·登出 / 网易云链接或 id / 没装 Mod 怎么办。帮助里教的是**全角 `【点歌】`**写法（机器人两种写法都认：`【点歌】`/`〔点歌〕`/`［点歌］`），这样**客户端 Mod 不会把帮助消息当成一次点歌**（Mod 只认半角 `[点歌]`，否则每个看帮助的人都会 30s 后报「没找到」）。另：`[点歌]台` 机器人不再当成歌曲名去搜（那是 Mod 界面）。
- [x] **改成「谁点谁听」（不广播）**：机器人侧新增 `song.privatePlay`（默认开）—— 播放数据用 **`/msg` 私聊**发给点歌的人，公屏不再出现点歌消息（GUI「点歌」页可关）；名字不合法或私聊失败时退回公屏。Mod 侧新增 `onlyMySong`（默认 true，`config/songmod.json` 可改）—— `isForMe()`：**要么我 5 分钟内点过歌、要么文案里出现我的游戏名**才播，所以即使机器人是公屏发的（或服务器把私聊广播了），也只有点歌的人会真的响。实测：私聊模式下点歌的人收到两条 `/msg`（文案提示 + `[点歌]… | ¤直链 id:…`），公屏 `[点歌]` 消息 **0 条**；关掉开关 → 正好回到公屏模式。
- [x] **别人的消息不再触发我的本地兜底**（谁点谁听的关键）：Mod 现在区分「我自己发的」（`ClientSendMessageEvents.CHAT` → `mine=true`）和「服务器发来的」（`CHAT`/`GAME` → `mine=false`）。收到别人的 `[点歌]歌名`（公屏）时**直接忽略**，不再等 30 秒、不再本地搜歌把别人的歌放出来；`[点歌]停止` / `[点歌]台` 这类指令也**只认自己发的**。机器人侧 `clear_song_queue(who)` **只清该玩家自己的队列**（A 发停止不会关掉 B 已经在听的歌）。
- [x] **`/msg` 超长会被服务器踢（`DecoderException: serverbound/minecraft:chat_command`）—— 已修**：MC 的 256 上限算的是**整条命令**（`/msg ` + 玩家名 + 内容）。之前没算命令前缀，文案长一点（用户模板 `&a{player} &f点了一首 &b{music} &7&o| 验证地址`）就直接超限被踢。现在预留了 `/msg <名字> ` 的长度，超了就**拆成两条**（纯文案一条 + `[点歌]歌名 | ¤直链 id:…` 一条），还超就依次丢 `id:` 标记 / 缩歌名 / 退化成「只发 id（Mod 自己取链）」。实测（用户同款模板 + 长歌名）：最长 **218 字符**、超限 **0 条**。
- [x] **「机器人有输出、Mod 却还是没反应」的第二个（更根本的）原因：事件通道**。很多服务器的聊天插件会把玩家消息**改格式后当「系统消息」重播**（形如 `[称号]名字 » 消息`），这种**不走 `ClientReceiveMessageEvents.CHAT`、只走 `GAME`**；Mod 原来只监听 CHAT，于是机器人那条消息**根本没进来**（表现为干等 30 秒走本地兜底）。已改为**同时监听 `CHAT` + `GAME`**（Fabric 的 GAME 事件签名为 `(Message, boolean overlay)`，两个通道都收；自己给自己发的提示不会走这两个事件，不会自触发）。26.x 版同步修改
- [x] 再加一道**保险**：机器人直链后面附 `id:<歌曲id>`（如 `id:1330348068`），万一服务器把混淆直链也弄坏，Mod 用 `MusicSearch.byId` 自己取链（Mod 里填了 Cookie 就是完整版，没填就是试听）。实测消息 224 字符、仍然没有任何 `http`/`.`/`://`
- [x] **`#`/`&` 都不能用**：实测服务器把 `#3399839173` 当**十六进制颜色码**吃掉（`#339983` 变颜色、只剩 `9173`），`&` 是 MC 颜色符同理 —— 所以 id 标记改成 **`id:3399839173`**（Mod 同时兼容老写法 `#123…`，但机器人只发 `id:`）。当前公屏消息实测：**不含** `#`、`&`、`http`，224 字符
- [x] Mod 加**诊断日志**（排查「机器人发了、Mod 没动静」用）：带触发词的消息一律打 `[songmod] 收到点歌消息: …`，命中直链时打 `[songmod] 用机器人直链播放: …`；启动时打版本标记 `[songmod] 版本 v1.0.0+obflink2-20261017`


- [x] **修复「机器人回复自己说的话」死循环（实测被服务器以 Kicked for spamming 踢出）**：根因是「日志转发」功能把带触发词的日志 [点歌] 没找到「X」 发进了游戏公屏，服务器广播回来又被 _maybe_song_reply 当成新点歌 → 越滚越长（没找到「没找到「…」」）。三层防线：① **日志转发永不发送含触发词的行**（根治）；② _remember_sent/_is_own_echo——机器人发出的一切（send_chat、/msg、chatSent 事件、以及自己写的含触发词日志）都记 15 分钟，收到同样内容直接忽略；③ **熔断**——同一句话 8 秒内被点 ≥3 次即暂停点歌 60 秒并告警。实测：把机器人自己的消息（3 种前缀 × 3 个发送者）喂回去 → **0 次新搜索**；连点 5 次 → 熔断生效；带触发词的日志不再转到游戏 ✅

- [x] **「没找到」的真凶：搜索接口带会员 Cookie 时被网易云返回空结果**。实测同一个搜索：带 MUSIC_U →
esult.songs 为空（显示「没找到」）；**完全不带 Cookie → 正常返回**（起风了/孤勇者/甲乙丙丁 全部 OK，试听标记 trial=0）。根因应该是短时间内搜索请求过多，带会话的请求被风控喂空。修复：① search() **匿名优先**（第 3 次重试才带 Cookie 兜底）；② 每次搜索间隔 ≥0.8 秒 + **5 分钟结果缓存**（同一首歌反复点不再打接口）；③ _get_json 记录 LAST_ERROR（HTTP 状态/异常），
esolve() 在**请求失败**时抛 SearchUnavailable → GUI 显示「解析「X」失败：HTTP 403」而不是误报「没找到「X」」；④ 取播放地址失败时提示「可能被限流，稍等一下再试」。

- [x] 机器人新增聊天命令（都认「提示词」而不是 `[点歌]`）：`[提示词]帮助`（私聊 8 行用法，文案里故意只用全角 `【点歌】`）、`[提示词]好感度 [玩家]`（分数/等级/下一档；数据 `affinity.json`）、`[提示词]今日幸运值`、`[提示词]视频推荐`、`[提示词]签到`。触发判断复用 `ai_player._compile_trigger` + `_split_top_level_commas`，读 `ai_config.triggerRegex`（`ai_player` 关着也能用），没有提示词才退回点歌触发词
- [x] 视频推荐走 `bilibili.recommend()`：大池子（30 条）里挑，**先避开刚推过的 id** → 每次冷却过后再点就是**换一批**；来源可选 番剧追番榜（默认，`season_type=1&day=3`）/ 首页热门 / 全站排行；**发公屏**、**全服共用**冷却（默认 30s，谁点都算）
- [x] GUI 新增「杂项」页：视频推荐（开关/来源/条数/冷却/抬头·每条·冷却中·失败文案）、今日幸运值（开关/上限/文案）、每日签到（开关/金币范围/连签加成/物品·数量/发钱命令/发物命令/成功·已签·未开文案）、**群发私聊**（一句话逐个 `/msg` 所有在线玩家，**玩家之间的间隔秒数**可调，跳过机器人自己，`_remember_sent` 防自触发）。`listPlayers` 由 `bot_engine.js` 提供（等 ≤1.2s 的 `players` 事件）
- [x] **每日数据落 SQLite（修「重启后今天的幸运值被重写」）**：新增 `aafm_py/dailydb.py`（`daily.db`）存**当天幸运值**（第一次查询随机定下来落库，重启/改上限都不再变）+ **当天签到**（`checkin` 表）+ 跨天累计 `player_stats`（连签天数/总次数）+ `meta.current_day`；跨天由 `ensure_day()` 做「备份 → 删除 → 重建」，旧库复制到 `db_backup/db_backup_<时间>.db`，`player_stats` 带到新库所以**连签不断**；`tick` 里每秒 `_maybe_daily_rotate()` 兜跨天
- [x] 每日签到：`[提示词]签到`（也认 打卡/checkin/每日签到）每天一次，金币在 `[最少, 最多]` 随机 + 连签加成，物品可选；奖励由**两行命令**发放（`{player} {value} {item} {count}`，不写 `/` 自动补，整条 ≤250 字符，发前 `_remember_sent`）；帮助里加了签到行。实测：首次 → `/eco give …` + `/give …` + 成功文案；同天再签 → 只回「今天已经签过啦」；重启进程后幸运值与签到状态都还在

- [x] 视频推荐新增两个来源（**五选一**，GUI 下拉框）：`home` = **B 站首页推荐**（`x/web-interface/index/top/rcmd`，先访问一次 bilibili.com 拿 `buvid3` 再调才不会被 `-412 request was banned`；`ps` 只能 12，一次 30 条，实测连推 3 批 0 重合）；`fav` = **我的收藏夹**（`x/v3/fav/resource/list?order=mtime`，**随机翻页 2 页**凑池子，跳过失效条目）。实测：home 3/3 成功、fav 抽到 5 条可用视频；其余来源 1 个没坏
- [x] 收藏夹要登录：`misc.biliCookie`（整条 Cookie 或只填 SESSDATA 值，`cookie_header` 归一化）+ `misc.biliFavMediaId` + `biliFavName`（只用于显示，文案里 `{source}` 会显示成「收藏夹「名字」」）；GUI「杂项 → B 站账号」有 SESSDATA 输入（密码框）+ **「读取收藏夹」按钮**（后台线程调 `login_info`/`fav_folders`，把自建+收藏的夹子填进**可选下拉框**，也能直接填收藏夹 id）+ 状态提示；选 fav 但没配 Cookie 时**在冷却之前**就公屏提示，不白扣冷却。换夹子时「推过」记录按 `fav:<media_id>` 分开记

## 2026-10-25 审计 Issues 修复（#14–#33）

- [x] #14 `SongPlayer`：旧播放线程 finally 只在 `player == mine` 时清引用，不再把新歌的播放器清掉（否则新歌停不了）
- [x] #15 `onlyMySong`：记录自己最近一次点歌的**关键词**；只有「文案含我名字」或「关键词匹配」才播，并在收到后清掉，5 分钟内不再接受别人的歌
- [x] #16 群发私聊 `refresh_player_list`：`eng.running` 是属性，原来写成 `eng.running()` 会抛异常 → 改用属性
- [x] #17 `BotEngine`：读取线程固定自己对应的进程，旧线程结束时不再覆盖新进程的 `_running`
- [x] #18 AI 配置保存后立即 `reload_ai_player()`；`AiPlayer.reload()` 增加配置代次、关闭旧 Provider，迟到的旧回复直接丢弃
- [x] #19 `/msg` 与公屏发送按 **UTF-16 码元** 计长度（`_mc_len` / `_mc_clip`），含 emoji 也不会超 256 被踢
- [x] #20 `daily.db` 跨天：备份失败（或任何异常）不推进日期、不删旧库
- [x] #21 幸运值：已落库的值直接返回，不再被当前上限二次裁剪
- [x] #22 CSV 写入：插列后把表头写回 `grid[0]`，权威描述列 / 别名列不再变成无表头空列
- [x] #23 视频推荐换批：新候选优先，数量不够时用旧候选补，不再整批退回旧池子
- [x] #24 `AiPlayer.tick()`：过期去重清理移到功能开关判断之前，AI/定时回复关闭时也不会无限增长
- [x] #25 `AutoEat`：health 监听器只在构造时注册一次，`toggle()` / `setAutoeat` 不再重复注册
- [x] #26 `quiz.js`：延迟发送回调捕获当时的 session，旧题的答案不会发到新题
- [x] #27 `run_with_api.py`：路由精确匹配、请求体必须是 JSON 对象（≤64KB）、玩家名/金额严格校验
- [x] #28 `ZMusicBridge`：`type` 必须是字符串，`null`/对象/数组安全忽略
- [x] #29 `build_all.ps1`：路径从脚本位置推导、JDK 缺失时明确报错、版本从 `gradle.properties` 读取、按退出码 + 本次产物判定
- [x] #30 `package-lock.json` 根版本/授权与 `package.json` 对齐；README 改为 Node 22+；说明历史 `0.3.0.x` 只是展示编号、非 SemVer
- [x] #31 `docbook.paginate`：硬切分改用 UTF-16 感知的 `_wrap_line`，小预算下 emoji 不超页
- [x] #32 `PacketCodec.encode`：最终帧超过 32760 字节抛 `IllegalArgumentException`，插件侧捕获并提示，不再静默产出超限包
- [x] 回归测试：`tests/test_fix_regressions.py`（8 项）；PR #35/#36/#37 已在独立 worktree 验证：Python 27/50/73 项、Node 40/47/47 项、Java 取消测试 12+12+8 项全部通过

