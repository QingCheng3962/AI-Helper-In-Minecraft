# AI Helper In Minecraft

一个"无客户端"的 Minecraft AI 玩家：不需要打开游戏本体，用 GUI 控制 mineflayer 引擎进入服务器，由大模型驱动它在游戏内与人聊天互动。

- 入口为 Python tkinter GUI（三个页面：设置 / 玩家配置 / 游戏输出），后台由 Node.js（mineflayer）负责连接与协议。
- 支持 **离线 / LittleSkin / 正版(微软账号)** 三种登录方式。
- 登录失效时自动重新登录（LittleSkin 自动打开浏览器 OAuth，完成后自动重连）。
- 附带可选辅助功能：Quiz 自动答题、AutoEat 自动进食（自旧 Cubex 机器人项目保留）。

> 本项目仅供学习与个人使用。使用前请阅读文末[免责声明](#免责声明)。

## 功能特性

- 三种登录方式
  - 离线模式：仅填玩家名即可进入离线服务器。
  - LittleSkin：支持自定义皮肤站站点地址 / OAuth Client ID / Secret / 回调端口（默认 littleskin.cn），`3rdparty_auth.js` 完成 OAuth 登录并把 yggdrasil 会话写入 `auth_profile.json`。
  - 正版（微软账户）：`msa_auth.js` 使用设备码流程登录，凭据缓存在本机，之后自动静默登录。
- AI 玩家对话（OpenAI 兼容端点，支持 Anthropic）
  - 系统提示词 + 模板管理、上下文窗口、回复条数/字数限制。
  - 触发回复：多个触发词用英文逗号分隔；形如 `[名字]` 的单独触发词按**字面量**匹配（自动转义、忽略大小写），其它写法按完整正则匹配。检测对象为聊天框里的**完整原文**（含频道 / 称号 / 前缀），命中任一即回复。
  - 自动回复、定时回复、冷却时间。
  - 消息过滤正则、玩家黑名单。
  - 可选文生图（把图片 URL 发到聊天）。
- 自动重登：LittleSkin 会话失效（如 "Invalid access token"）时自动拉起登录流程并重新连接。

## 目录结构

```
.
├─ main.py                # 启动入口（GUI）
├─ aafm_py/               # Python 端：GUI、配置、控制器、AI 玩家
│  ├─ gui.py              #   设置 / 玩家配置 / 游戏输出 三个页面
│  ├─ controller.py       #   控制器（事件分发、运行时配置）
│  ├─ ai_player.py        #   AI 玩家触发/上下文/过滤逻辑
│  ├─ config.py           #   配置模型（config.json / mcai_chat.json）
│  ├─ llm.py              #   OpenAI 兼容 / Anthropic 客户端
│  └─ bot_engine.py       #   管理 mineflayer 子进程
├─ bot_engine.js          # Node 端无头引擎（三种登录 + 聊天解析 + quiz/autoeat）
├─ 3rdparty_auth.js       # LittleSkin OAuth 登录/刷新/自动重登
├─ msa_auth.js            # 微软账号设备码登录
├─ quiz.js / autoeat.js   # 旧项目保留的答题 / 自动进食
├─ app.js                 # 旧版纯 Node 入口（不再需要，保留兼容）
├─ config.json            # 运行时配置（gitignore，本地生成）
├─ mcai_chat.json         # AI 玩家配置（gitignore，本地生成）
├─ auth_profile.json      # LittleSkin 会话（gitignore）
└─ runtime_config.json    # 连接时生成的临时配置（gitignore）
```

## 环境要求

- Python 3.9+（需 tkinter，Windows 官方 Python 自带）
- Node.js 14+（推荐 18+）
- 可联网（访问 LLM API、皮肤站 / 微软账号服务、目标服务器）

## 安装

```bash
pip install -r requirements.txt
npm install
```

## 运行

```bash
python main.py
```

在 **设置页** 填好服务器地址、选择登录方式并登录（离线可直接填名字），再到 **玩家配置页** 写好 AI 设置，点顶部「连接」。

其它入口（可选）：

```bash
npm run login:littleskin   # 仅执行 LittleSkin 登录
npm run login:msa          # 仅执行微软账号登录
```

## 登录方式说明

1. 离线模式：任意名字，服务器需开启离线（非正版验证）。
2. LittleSkin：
   - 在设置页填写站点地址（如 `https://littleskin.cn`）与 OAuth 应用信息（Client ID / Secret / 回调端口，默认使用 LittleSkin 公开示例应用）。
   - 点「LittleSkin 登录」会弹出控制台并自动打开浏览器授权页；登录后无需重启，直接点「连接」。
   - 会话失效时会自动重登并重连。
3. 正版（微软）：
   - 点「正版登录（微软）」在弹出控制台按提示完成设备码授权；账号需已购买 Minecraft。
   - 凭据缓存在本机（默认 `~/.minecraft/nmp-cache`，可在设置页指定缓存目录），之后自动静默登录。

## AI 配置

- 在 **设置页 → AI API** 填写提供方 / API 地址 / 密钥 / 模型。兼容 OpenAI 格式，如 DeepSeek、Moonshot、Ollama 等；Anthropic 也支持。
- 在 **玩家配置页**：
  - 系统提示词决定 AI 的说话风格与行为（模板可保存复用）。
  - 触发回复示例：填 `[DiaoHelper],@Helper,!ai` 时，聊天完整原文命中任意一个即回复。单独 `[名字]` 自动按字面量匹配；其余按正则。留空则仅在启用"自动回复/定时回复"时说话。

## 本地文件与安全

- `config.json` / `mcai_chat.json` / `auth_profile.json` / `runtime_config.json` 属于本地运行时文件，已加入 `.gitignore`，**请勿提交到公开仓库**，它们包含你的 API Key、皮肤站刷新令牌等敏感信息。
- 如需发布到远程仓库，请先确认没有把上述文件以及任何密钥提交进去。

## 免责声明

1. 本项目仅用于学习、研究与个人娱乐，作者不对任何使用后果负责。
2. 使用本项目登录第三方服务器、皮肤站或微软账户属于个人行为；请遵守相关服务器规则、皮肤站服务条款与微软/游戏的服务条款。请勿用于任何违反规则或法律法规的用途。
3. 本项目会自动保存登录凭据与 API 密钥到本地文件（见上文"本地文件与安全"），请妥善保管你的设备与文件；因泄露、误用凭据导致的损失由使用者自行承担。
4. 调用大模型 API 会产生费用，费用由使用者的 API 账户承担，与作者无关。
5. 本项目可能被服务器以任何方式检测或禁止，作者不对账号被封禁、被踢出、数据丢失等任何直接或间接损失负责。
6. 本项目按"现状"提供，不提供任何明示或暗示的担保（包括但不限于适销性、特定用途适用性、不侵权）。

## License

MIT
