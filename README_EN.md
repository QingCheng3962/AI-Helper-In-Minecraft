<p align="right"><a href="README.md">简体中文</a> · <b>English</b></p>

# AI Helper In Minecraft

**Current version: v0.3.7** (SemVer; all historical version labels were rewritten — see the mapping table in the [changelog](#changelog))

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

> All version labels follow Semantic Versioning (`MAJOR.MINOR.PATCH`). **The latest release is below**; **historical entries (v0.3.2 and earlier) live in [更新日志.md](更新日志.md)** (Chinese, with an English appendix).
> Current version: **`v0.3.7`**; the client mod stays at `0.1.0` (jar names unchanged).

### v0.3.7

- **One-click Windows installer** (`installer/`): a C# setup wizard wrapped in a single `.exe` — pick an install folder, extract, create desktop/Start-menu shortcuts. It bundles the Python 3.9 runtime with every pip dependency, the Node runtime and production `node_modules`, so the target machine needs **no Python/Node installed**. NapCat is bundled by default and optional at install time.
- **Runtime data in SQLite** (`data.db`): affinity, book-write progress, prompt templates and QQ account records (tables `affinity`/`kv`/`prompt_templates`/`qq_accounts`/`meta`; WAL + single lock). Old JSON files are auto-migrated on first access and backed up as `<name>.json.bak`.
- Settings (`config.json` / `mcai_chat.json`) intentionally stay JSON (hand-editable, contain API keys).
- The installer also ships all 15 song-Mod jars (`Mod/`), the ZMusic server plugin, docs and the promo page.
- Regression: Python 156 tests pass; installer silent-install + bundled-Python import self-check passes.

### v0.3.6

- New CustomTkinter GUI (Settings / Songs / Tools / Logs / About) as the default entry; `python main.py --legacy` keeps the original tkinter UI.
- Song Mod: brand-new in-game GUI (draggable progress, speed 0.5–2.0, listen-together, in-game lyrics) for both 1.21.x and 26.x.
- Port scanning with Minecraft Server List Ping; support-ticket / dev-blog links.

### v0.3.5

- **Session/concurrency safety**: the relay resyncs by upstream instance + true high-water mark (#39); Bot/QQ engine events carry a generation so late events from an old process cannot pollute a new session (#52); ZMusic requests/broadcasts use generations, so `stop` / quit / disable invalidates in-flight searches (#41); the OAuth callback now has `state`, timeout, single-shot handling and error cleanup (#48).
- **Song request safety**: the mod no longer plays a receipt just because the text contains your name; `botName` can restrict playback data to the trusted bot sender, otherwise it falls back to matching your latest request keyword (#53).
- **Data reliability**: book progress stores a document SHA-256 + pagination fingerprint and refuses to continue with stale page numbers (#40); affinity rolls back in-memory changes when saving fails (#45); bad config types are skipped safely and corrupt JSON is backed up before falling back to defaults (#43/#44).
- **Spreadsheet safety**: XLSX column insertion rewrites formula references (#42); player names/descriptions/aliases are prefixed with `'` to block formula injection (#54).
- **Process/credentials**: shutdown stops the receipt worker and drops queued tickets (#46); AutoEat uses generations so old timers cannot touch a new session (#47); browser launch uses `execFile` and only allows `http/https` (#55); the relay only sends its token to the upstream origin and strips it on cross-origin redirects (#56).
- Regression tests: Python 156, Node 58, and Java song-mod 15+15+8 scenarios pass; the ZMusic plugin builds successfully.

### v0.3.4

- **Configurable QQ group tool prefix**: default `[AI工具]`, editable in the GUI (`qq.toolTrigger`); supports `[AI工具]在线玩家`, `[AI工具]视频`, `[AI工具]音乐 <name>`, separate from the AI chat trigger. The old `[AI]在线玩家` is no longer a tool command.
- **MC → QQ `@QQ number`**: writing `@123456789` in a relayed in-game message now becomes a real OneBot `at` segment in the QQ group instead of plain text.
- **Minecraft chat receipt printer**: a new toggle in **Misc** prints **only player chat** to a Windows thermal receipt printer, effectively printing `chat.log` (ESC/POS + GBK). No server messages, bot lines, ActionBar, header, separator or cut; queued in the background, 58/80mm, per-minute cap, skip `/` commands, preview-only mode. Config keys: `misc.mcChatPrint*`.
- Regression tests added for tool commands, `@QQ number` at segments, and receipt layout/preview/queue; Python 126 tests and Node 8 tests pass.

### v0.3.3

- **QQ group online players**: `[AI]在线玩家` replied with the online list (from v0.3.4 the tool prefix is configurable `[AI工具]`).
- **Fixed emoticon codes being swallowed by media URLs**: a space is always inserted after `[图片]`/`[视频]` links, and the final MC text gets a URL-boundary pass so `...gif[哭]` cannot break the server's `URI.create()` / system-chat packet (thanks to [@FurryAoao](https://github.com/FurryAoao) for [#38](https://github.com/QingCheng3962/AI-Helper-In-Minecraft/issues/38)).
- **QQ text can no longer spoof colour codes or media placeholders**: `&c` / `§c` are neutralised; literal `[图片]`/`[视频]` in user text no longer consumes real media links (private-use markers in the engine).
- **Range support** for media: A/B/Web servers return `206` / `Content-Range` / `Accept-Ranges`, so `<video>` seeking works and iOS/Safari is happier.
- **Media size / disk protection**: per-file limit (default 100 MB) and low-disk pause threshold (default 200 MB), with skip + notice.
- **Automatic cleanup**: `pic_http/` is cleaned at most hourly by keep-days (default 7) and total-size cap (default 2 GB, 0 = unlimited), including stale `.part` files; the B relay also prunes old messages/media.
- **`.mp4` video support**: QQ videos (`.mp4` / `.m4v` / `.mov`) are downloaded like images, relayed as `[视频] link`, returned in the website API's `videos` field, and played with `<video>`; original video names are preserved when enabled.
- **Relay syncs videos**: `other_qq_pic_sent/relay.py` downloads/stores/serves videos, upgrades old SQLite databases with a `videos` column, and serves `/filename.mp4`; the web app CSP allows `media-src 'self'`.
- Regression tests added for link spacing, emoticon/URL separation, `.mp4` save/serve, Range, media limits/cleanup, video serialization, relay mp4 sync and the online-player command.

### v0.3.3-beta

- **Website integration (QQ → website)**: `qq.webApiEnabled` / `qq.webApiToken`; built-in endpoints `/api/qq/messages`, `/api/qq/images`, `/api/qq/stream` (SSE), `/health` with token auth and CORS.
- **Pure-image messages are no longer dropped**: `qq_engine.js` resolves images first and forwards them as `[图片]`.
- **Images saved once**: the website API and the MC chat link share `_prepare_qq_images()`; images are still saved when MC is offline.
- **GUI egress mode**: **This device** (A serves the website directly) or **another LAN device** (B runs the Python relay; selecting it switches the bind address to `0.0.0.0`).
- **Python relay** (`other_qq_pic_sent/relay.py`, stdlib only): polls A, downloads images, stores messages in SQLite with local increasing IDs, fingerprint dedup, handles A restarting and resetting IDs.
- **Python web app** (`other_qq_pic_sent/webapp.py`, stdlib only): public no-login gallery by default (optional `require_login` with PBKDF2/session/CSRF/rate limit), read-only API; image URLs are `https://domain/filename` (old `/pic_http/filename` also works).
- Deployment templates: `docs/deploy/nginx-pic.conf`, `docs/deploy/cloudflared-pic.yml`, `other_qq_pic_sent/qq-relay.service`.
- Docs: [`docs/QQ图片网站接入.md`](docs/QQ图片网站接入.md) and [QSP usage guide](<how to use QSP(QQ sent picture to minecraft).md>).

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

## Acknowledgements

Thanks to the following people for filing issues and pull requests that helped find and fix many problems:

- [@HU-TAO-520](https://github.com/HU-TAO-520)
  - Issues **#1, #2, #5–#34**: check-in / book-writing consistency, command blocking, QQ reverse auth, config saving, AI work queue, song cancellation, daily data, CSV/pagination, build scripts and dependency security (P1–P3); #1 was encouragement for the project.
  - PRs **#3, #35–#37**: fixes for write confirmation, check-in rewards, command blocking, QQ reverse auth, atomic config saving, AI scheduling and song cancellation.
- [@FurryAoao](https://github.com/FurryAoao)
  - Issue **#38**: when an image was followed by an emoticon code such as `[哭]`, the `open_url` regex swallowed it and `URI.create()` failed, dropping the whole relayed message; fixed in **v0.3.3**.

Thanks also to every player and developer who reported feedback, helped test or suggested improvements.

## License

This project is licensed under the **Apache License 2.0** — see [LICENSE](LICENSE).
You may use, modify, distribute and use it commercially (just keep the copyright/license notices);
it also includes an explicit **patent grant** and requires stating significant changes.
The song mod (`mc-song-mod/`) and the ZMusic plugin (`mc-music-bridge/`) are Apache-2.0 as well.

> Note: the mod bundles **JLayer** (MP3 decoder, `com.googlecode.soundlibs:jlayer`), which is a third-party
> **LGPL** component — independent from this project's Apache-2.0 license; comply with it when redistributing.
