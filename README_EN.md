<p align="right"><a href="README.md">简体中文</a> · <b>English</b></p>

# AI Helper In Minecraft

**Current version: v0.3.1** (SemVer; all historical version labels were rewritten — see the mapping table in the [changelog](#changelog))

A "client-less" Minecraft AI player: it never opens the game itself. A GUI drives a mineflayer engine to join a server, and a large language model powers the in-game chat behaviour.

- The front end is a Python tkinter GUI with multiple tabs: **Settings / AI Config / Player Config / Roster / QQ Relay / Reconnect·Kick / Appearance / Log**. The Node.js (mineflayer) engine handles the connection and protocol in the background.
- Supports **Offline / LittleSkin / Premium (Microsoft account)** login.
- Automatic re-login when a session expires (LittleSkin auto-opens the browser OAuth flow and reconnects when done).
- Optional extras kept from the old Cubex bot: Quiz auto-answer, AutoEat.

> For learning and personal use only. Please read the [Disclaimer](#disclaimer) before using it.

## Features

- Three login modes
  - **Offline**: enter any name to join an offline-mode server.
  - **LittleSkin**: skin site URL / OAuth Client ID / Secret / callback port are configurable (defaults to littleskin.cn). `3rdparty_auth.js` performs the OAuth login and stores the yggdrasil session in `auth_profile.json`.
  - **Premium (Microsoft)**: `msa_auth.js` uses the device-code flow; credentials are cached locally for silent sign-in afterwards.
- AI player chat (OpenAI-compatible endpoints; Anthropic also supported)
  - System prompt + reusable templates, context window, max reply messages / max chars per message.
  - Trigger replies: multiple triggers separated by commas. A lone `[Name]` trigger is matched as a **literal** (auto-escaped, case-insensitive); anything else is treated as a full regex. Matching runs against the **full raw chat line** (channel / prefix / title included); any hit triggers a reply.
  - Auto reply, scheduled reply and cooldown settings.
  - Message filter regex and player blacklist.
  - Optional image generation (posts the image URL into chat).
- Auto re-login: when a LittleSkin session becomes invalid (e.g. "Invalid access token"), it launches the login flow automatically and reconnects.
- Auto reconnect & kick handling (**Reconnect·Kick** tab): when the connection ends by itself (drop / kick) the bot reconnects after a configurable delay, up to a configurable number of attempts (reset on a successful spawn). After being kicked and reconnecting it can send `/lobby` automatically. Clicking **Disconnect** manually never triggers a reconnect.
- Appearance (**Appearance** tab): pick a PNG / JPG as the window background image and dim it; the text theme can be **Auto (from background brightness)** / light background with black text / dark background with white text.

## Changelog

> **All version labels follow Semantic Versioning** (`MAJOR.MINOR.PATCH`). Historical entries were rewritten per this table:
> `v1.0 → v0.1.0`, `v2.0 → v0.2.1`, `v2.1 → v0.2.2`, `v3.0 → v0.3.0`, `v3.1 → v0.3.0.1`, `v3.2.0 → v0.3.0.2`.
> Current version: **`v0.3.1`**; the client mod (`mc-song-mod/`) is versioned together with the project (currently `0.1.0`).

### v0.3.1

- **SemVer switch**: the project is now versioned **`v0.3.1`**, and the client mod **`0.1.0`** (jars are now named `Song_0.1.0_<gameVersion>.jar`); older entries use the mapping table above.

- **`[提示词]帮助` (help)**: send `[DiaoHelper]帮助` → the bot **whispers** you a usage guide (chat, song requests, stop, affinity, video recommendations, lucky value, other commands). The **提示词 is whatever is configured under 玩家配置 → 触发正则** (`[DiaoHelper]`, `@DiaoHelper`…), *not* the song trigger `[点歌]`. The help text deliberately avoids the half-width `[点歌]` (uses `【点歌】`) so modded clients don't mistake it for a song request.
- **Affinity (new)**: `[DiaoHelper]好感度` shows your score / level / points to the next level; `[DiaoHelper]好感度 <player>` shows someone else's. Defaults: +1 per AI reply (5 min cooldown), +2 per successful song request (1 min), +1 bonus on the first interaction of each day; levels 陌生人 → 脸熟 → 熟人 → 朋友 → 好朋友 → 挚友. Stored in `affinity.json`, configured under `affinity` in `config.json`.
- **New [`HELP.md`](HELP.md)**: the full player-facing guide (song requests, affinity, video recommendations, lucky value, FAQ, config map for server owners).
- **Video recommendations**: `[DiaoHelper]视频推荐` → **public chat** posts a batch of Bilibili videos (title + note + a **clickable** link — clicking opens the browser; some servers render it as a clickable `[链接]`). Sources are **pick-one**: **番剧追番榜 / anime-ranking (default)**, **B 站首页推荐 / homepage recommendations** (the feed you see on bilibili.com — the bot visits the homepage once to grab a `buvid3` cookie first, which avoids the `-412 request was banned` block), Bilibili homepage popular, the all-site ranking, or **我的收藏夹 / my own favourites** (paste a Bilibili cookie, pick a folder from the drop-down, and the bot draws items by **randomly paging** through it). Count and all texts are editable in the new **杂项 (Misc)** tab. The cooldown is **server-wide** (30 s by default) and asking again after it **swaps in a new batch** (picked from a pool of dozens, avoiding what was just shown).
- **Daily lucky value**: `[DiaoHelper]今日幸运值` → a value fixed for that day (max value and text are configurable in the Misc tab), refreshing the next day. It is rolled **once** on the day's first query and **stored in `daily.db`**, so asking again — or **restarting the bot** — returns the very same number.
- **Daily check-in**: `[DiaoHelper]签到` → once per day; posts the result and **grants rewards**: money (random within a configurable min~max, plus an optional per-streak-day bonus) and items (id + count). What actually gets granted is decided by **two command lines** (e.g. `eco give {player} {value}`, `give {player} {item} {count}` with the `{player} {value} {item} {count}` placeholders), and the success / already-signed / disabled texts are editable. Streak and total counts live in `daily.db` and survive restarts.
- **Daily data + cross-day backup**: new `daily.db` (SQLite) holds **today's lucky values** and **who checked in**; when the stored day is no longer today, the bot copies the old DB to `db_backup/db_backup_<time>.db`, then **deletes and rebuilds** it for the new day (cumulative streaks are carried over so 连签 never breaks).
- **New 杂项 (Misc) tab**: toggles + texts + cooldown for video recommendations, the lucky value and the daily check-in (including its reward scheme), plus **mass private message** — type a line, hit "发送给所有玩家", and the bot `/msg`s every online player one by one (with a configurable **gap between players** so it never gets kicked for spamming; the bot skips itself).
- Fix: `fabric.mod.json` now injects the `minecraft` dependency per version (fixes the **26.x "incompatible"** error — every jar used to hard-code `~1.21.11`).

### v0.3.0.2

**In-game song requests (new; two options — client mod + bot-side playback by default, zero server changes)**

- **Client mod** ([`mc-song-mod/`](mc-song-mod/README.md)): one source tree building **15 versions** (Fabric **1.21.1 – 26.2**) via `build_all.ps1`; artifacts `dist/Song_<projectVersion>_<gameVersion>.jar`. 1.21.x uses **mojmap + remapping Loom** (`src/legacy`, includes the in-game screen); 26.x is unobfuscated with a rewritten render API → non-remapping Loom + Java 25 (`src/modern`, no screen).
  - Chat: `[点歌]song`, `[点歌]<netease link>`, `[点歌]<song id>`; press **K** (rebindable in Controls) or send `[点歌]台` to open the in-game **Song Screen**.
  - Fixed "the key does nothing in production": the keybind reflection hard-coded the dev-only class name `net.minecraft.client.KeyMapping$Category` (obfuscated in production) → now fully name-free reflection (match constructors by parameter types, take a static instance); works across 1.21.1–1.21.11.
  - Playback: prefers the bot's direct link; local fallback (own Netease cookie, VIP songs included) when the bot is offline; manual `302 https→http` redirect chasing, `SourceDataLine` created lazily after the first decoded frame.
- **Bot-side playback** (`aafm_py/netease.py`): the bot resolves with its **member account**, so **players never log in**. Song tab: **QR login** (drawn in the GUI), **Verify login** (shows nickname + `vipType` and tests a VIP song), playback toggle, play-text template, stop word, mod-download reply + cooldown.
  - Measured truth: the legacy endpoint `api/song/enhance/player/url` **does honor membership with a valid cookie** (320k, `trial=0`); only without a cookie you get a 30–45s trial. eapi (AES-128-ECB) is just a fallback.
- **"Whoever requests it, listens to it" (default, no broadcasting)**: the play data is sent back to the requester via **`/msg`** (`song.privatePlay`); the mod double-checks with `onlyMySong`. Turn both off for an old-style "whole server listens" broadcast.
- **Fast response**: `song.instantPlay` — a new request starts immediately instead of queueing behind the current song's duration; re-requesting the same song reuses the cached direct link (no new search).
- **`[点歌]help`**: the bot **whispers** the full usage list (song / stop / screen / help / login / link or id). The help deliberately shows the full-width `【点歌】` form (the bot accepts both, the mod only the half-width one, so help lines are never mistaken for song requests).
- **Per-player stop**: `[点歌]停止` clears only that player's queue; the mod only honors commands it sent itself.
- **Anti link-filter**: some servers replace `http(s)://…` with `[链接]` in chat. The bot now sends an **obfuscated direct link** (`¤`/`¥` for the scheme, `. / :` → `· ¦ ∶`, no `?vuutv=…`), so `http`/`://`/`.` never appear; the mod restores it, plus an `id:<songId>` fallback. `#` must not be used (servers treat it as a hex color code) and neither must `&` (MC color code).
- **Chat channel fix**: many servers re-broadcast player chat as **system messages** (`[title]name » text`), which only reach `ClientReceiveMessageEvents.GAME` — the mod now listens to **`CHAT` + `GAME`** and tells apart "messages I sent" from "server messages", so **another player's request no longer triggers my local fallback**.
- **`/msg` length guard**: MC's 256 limit applies to the **whole command** (`/msg ` + name + text); too long got the bot kicked (`DecoderException: serverbound/minecraft:chat_command`). The prefix length is now reserved and over-long lines are split/degraded automatically.
- **Optional server plugin** ([`mc-music-bridge/`](mc-music-bridge/README.md)): for servers where you *can* install plugins — `ZMusicBridge` implements the **ZMusic v5 `zmusic:packet`** protocol itself (no official plugin / cloud API / device_token).

**Other additions**

- **Document → book** (`aafm_py/docbook.py` + `bookwriter.js`): upload `.docx` / `.doc` / `.txt`, auto-wrap by page width and auto-paginate, the bot writes it into a **book and quill** in-game; sign as a written book (title template `{number}`), **write progress / continue / start from volume X**, pause / stop / rewrite, one-click clear.
- **Log saving** (`aafm_py/log_saver.py`): every log line (chat / server notices / private messages / action bar / app logs) is buffered and flushed to `logs/yyyy_mm_dd_第N次.txt` on day rollover or app close.
- **Local API launcher** (`run_with_api.py`): hooks `BotEngine` and exposes a local HTTP endpoint so a website can send messages through the running bot (optional, does not modify other files).
- **QQ relay improvements**: switchable gateway frameworks (SnowLuma default / NapCat) with **independent** configs; SnowLuma auto-reads port + token from `config/onebot_*.json` and picks a working token by a real WS handshake; QQ↔MC direction toggles decoupled; friendly 5xx/timeout messages with exponential backoff; a hard **20s timeout** on all 3rd-party auth requests.
- **Apache License 2.0**: added [`LICENSE`](LICENSE) (with an explicit patent grant, friendlier for companies and redistribution).

### v0.3.0.1

- **Command block (bottom of the Player Config tab)**: a table of commands to block (without the slash; defaults include `pay kick ban op stop whitelist kill give tp gamemode`). Any bot-sent message (AI replies / random chat) starting with `/` whose command name is listed is dropped and logged, preventing prompt-injection from running dangerous commands. Rows with "＋ add row" and per-row "Delete".
- **Message after blocking**: when a command is blocked, an optional configurable line can be sent; placeholders `{player}` = who requested it, `{command}` = command name (`{cmd}` alias); empty = block silently.
- **Identity-learn fix**: "我是X / 俺是X / 本人就是X" no longer records "我" as a name; the statement is stored for the **speaker**, with X as an alias/description. Ignored when the speaker is unknown.
- **Active connect (bottom of the Reconnect tab)**: ① every s/min/hour **send `/lobby`** to enter the lobby; ② every s/min/hour **check online**, and while offline retry connecting every N seconds **until it is back**. Each has its own red/green toggle.
- **Enable/disable buttons**: command block and random chat each have a **red/green button** (green enabled / red disabled) that toggles and saves instantly.
- **Random chat moved into Player Config** (no longer its own tab): **each line has its own random interval** (per-row min~max seconds) and there is a **random count per fire (min~max)** that picks lines from the list; rows can be added/removed.

### v0.3.0

- **Reconnect improvements**
  - Reconnect interval uses a value + unit (sec/min/hour/day) picker (up to 30 days).
  - New "custom reconnect triggers" (one regex/substring per line): a matching server chat/announcement forces a reconnect (default includes `You were kicked from lobby`) — for being moved to a login/lobby server without dropping the connection.
  - New "proactive reconnect": while online, drop + reconnect every configured interval.
  - New "keep retrying until back": ignore maxAttempts while the server restarts (optional max minutes, 0 = unlimited).
- **Random chat (new tab)**: enable; interval and per-fire count are both randomized within a min~max range; message list with "＋ add row" and per-row "Delete".
- **Human-verification auto-pass**: reads the container after a delay (retries until items populate), clicks items by "target name" (`点击这里` / `点击这里步骤N` in step order); falls back to odd-slot detection.
- **Quiz fixes**: also accepts chat-bar `[教育部]` questions (strips `新题目:`; settlement/scoring lines ignored); supports `无人答对`; sends only the final answer (thinking discarded); reuses the global AI config and auto-appends `…/chat/completions`.
- **Server messages**: unparseable public chat (announcements, command output, joins/leaves) is shown in the log and can feed the AI context.
- **Identity Q&A via AI**: answers "xxx 是谁" from the roster (descriptions/aliases/AI notes) + recent chat, including aliases; "X是Y" reply is now "X是Y，我记住了".
- **Low-health alert**: say a configurable line when health ≤ threshold (with cooldown).
- **Quick login**: picker listing local accounts with nicknames (last one preselected), quick-login via `-q`.
- **Misc**: fixed `client.chat is not a function` (messages queued until ready).

### v0.2.2

- **QQ relay**: bundled NapCat with four buttons (Start / Connect / Reconnect / Disconnect); OneBot 11 forward/reverse, group filter, access token.
- **MC → QQ**: messages starting with a trigger (default `[sentQ]`, multiple allowed) are forwarded; group targeting, cooldown, color-code stripping, `?` for illegal chars, success/failure reply.
- **Image → local link**: images saved to `pic_http/` and served by the built-in Python HTTP server at `/pic_http/`.
- **Account safety**: online health check, warning + stop-auto-reconnect on risk, auto OneBot config for new scanned accounts.
- **Update notice**: checks latest NapCat / program version at startup.
- **Chat parsing fix**: works with or without a `[guild]`-style title prefix.
- **Misc**: log tab with `[HH:MM:SS]` timestamps; GUI tab cleanup; high-DPI.

### v0.2.1

- **Auto reconnect / kick handling**: reconnect after drops/kicks; auto `/lobby` after a successful kick-reconnect.
- **Appearance**: window background image + dimming and auto/light/dark text theme.

### v0.1.0

- Initial release: client-less AI player (mineflayer), offline / LittleSkin / Microsoft login, AI chat with triggers/context/filters, Quiz auto-answer, AutoEat; bilingual README and disclaimer.

## Project Layout

```
.
├─ main.py                # Entry point (GUI)
├─ aafm_py/               # Python side: GUI, config, controller, AI player
│  ├─ gui.py              #   Tabs: Settings / AI / Player / Roster / QQ / Reconnect·Kick / Appearance / Output
│  ├─ controller.py       #   Controller (event dispatch, runtime config, auto reconnect / kick handling)
│  ├─ ai_player.py        #   Trigger / context / filter logic of the AI player
│  ├─ config.py           #   Config models (config.json / mcai_chat.json)
│  ├─ llm.py              #   OpenAI-compatible / Anthropic client
│  └─ bot_engine.py       #   Manages the mineflayer subprocess
├─ bot_engine.js          # Node headless engine (3 login modes, chat parse, quiz/autoeat)
├─ 3rdparty_auth.js       # LittleSkin OAuth login / refresh / auto re-login
├─ msa_auth.js            # Microsoft account device-code login
├─ quiz.js / autoeat.js   # Quiz auto-answer / auto-eat (kept from the old project)
├─ app.js                 # Legacy pure-Node entry (not needed anymore, kept for compatibility)
├─ config.json            # Runtime config (gitignored, generated locally)
├─ mcai_chat.json         # AI player config (gitignored, generated locally)
├─ auth_profile.json      # LittleSkin session (gitignored)
└─ runtime_config.json    # Temp config written on connect (gitignored)
```

## Requirements

- Python 3.9+ with tkinter (bundled with the official Windows Python)
- Node.js **22+** (`mineflayer` / `minecraft-protocol` require `node >= 22`; 22 LTS or newer recommended)
- Internet access (LLM API, skin site / Microsoft account services, and the target server)

## Install

```bash
pip install -r requirements.txt
npm install
```

## Run

```bash
python main.py
```

Fill in the server address on the **Settings** page, choose and complete a login method (offline mode only needs a name), configure the AI on the **Player Config** page, then click **Connect** in the toolbar.

Optional standalone commands:

```bash
npm run login:littleskin   # LittleSkin login only
npm run login:msa          # Microsoft account login only
```

## Login Modes

1. **Offline** – any name; the server must run in offline (cracked / non-premium) mode.
2. **LittleSkin**
   - On the Settings page, fill in the site URL (e.g. `https://littleskin.cn`) and the OAuth app info (Client ID / Secret / callback port; the public LittleSkin demo app is used by default).
   - Click **LittleSkin 登录**; a console opens and the browser authorization page opens automatically. No restart needed – just click **Connect** afterwards.
   - When the session expires, the app re-logins and reconnects automatically.
3. **Premium (Microsoft)**
   - Click **正版登录（微软）** and complete the device-code authorization in the console that pops up. The account must own Minecraft.
   - Credentials are cached locally (default `~/.minecraft/nmp-cache`; a custom cache folder can be set on the Settings page) for silent sign-in afterwards.

## AI Configuration

- On the **Settings → AI API** section fill in provider / API URL / key / model. OpenAI-compatible formats work (DeepSeek, Moonshot, Ollama, ...); Anthropic is supported too.
- On the **Player Config** page:
  - The system prompt defines the AI's style and behaviour (templates can be saved and reused).
  - Trigger example: `[DiaoHelper],@Helper,!ai` replies when the full raw chat line matches any of them. A lone `[Name]` is auto-treated as a literal; other tokens are regexes. Leave blank to stay silent unless **Auto reply / Scheduled reply** is on.

## Local Files & Security

- `config.json`, `mcai_chat.json`, `auth_profile.json` and `runtime_config.json` are local runtime files and are already listed in `.gitignore`. **Do not commit them to a public repository** – they contain your API keys, skin-site refresh tokens, etc.
- Before publishing to a remote repository, double-check that none of those files or any secret has been committed.

## Disclaimer

1. This project is for learning, research and personal entertainment only. The author is not responsible for any consequences of its use.
2. Logging into third-party servers, skin sites or Microsoft accounts through this project is your own act. Please comply with the relevant server rules and the terms of service of the skin site and Microsoft/game services. Do not use it for anything that violates rules or laws.
3. This project stores login credentials and API keys in local files (see "Local Files & Security" above). Please protect your device and files; any loss caused by leaked or misused credentials is your own responsibility.
4. Calling LLM APIs may incur costs, which are billed to your own API account and are unrelated to the author.
5. This project may be detected or banned by servers in any way. The author is not liable for any direct or indirect loss, including but not limited to account bans, kicks and data loss.
6. This project is provided "as is" without any express or implied warranty (including but not limited to merchantability, fitness for a particular purpose, and non-infringement).

## License

This project is licensed under the **Apache License 2.0** — see [LICENSE](LICENSE).
You may use, modify, distribute and use it commercially (just keep the copyright/license notices);
it also includes an explicit **patent grant** and requires stating significant changes.
The song mod (`mc-song-mod/`) and the ZMusic plugin (`mc-music-bridge/`) are Apache-2.0 as well.

> Note: the mod bundles **JLayer** (MP3 decoder, `com.googlecode.soundlibs:jlayer`), which is a third-party
> **LGPL** component — independent from this project's Apache-2.0 license; comply with it when redistributing.
