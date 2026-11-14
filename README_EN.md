<p align="right"><a href="README.md">简体中文</a> · <b>English</b></p>

# AI Helper In Minecraft

**Current version: v0.3.6-26.11.15-Fxied** (SemVer; all historical version labels were rewritten — see the mapping table in the [changelog](#changelog))

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

> Only v0.3.6-26.11.15-Fxied / v0.3.6-26.11.14 / v0.3.6 are kept here; older entries (v0.3.5 and earlier) live in [更新日志.md](更新日志.md).

### v0.3.6-26.11.15-Fxied

- **Installer (.exe) UI rebuilt**: branded dark header, flat modern buttons, custom progress bar, install log, version/feature summary; can launch the app right after install.
- **Complete packaging (every last piece)**: `tests/`, `mc-song-mod/` source + build scripts, `mc-music-bridge/` source, `installer/` source, `docs/`, `promo/`, `AGENTS.md` — everything ships inside the exe; a fresh install is fully self-contained.
- **Song service address is hard-coded** to the example site `https://cubexmusic.qingchengsep.top` (constant `SongApiClient.DEFAULT_BASE`; players cannot change it — edit source and rebuild to use your own). Server default port is **8767** (configurable in the GUI).
- **Song requests resolved server-side**: `mod → bot HTTP API → NetEase → API returns playback data → mod`. The bot uses its own cookie to search, resolve VIP links and fetch lyrics, then returns complete playback data; the client never searches or resolves anything. Everything goes over HTTPS with no chat traffic; clients only configure API URL/token/timeout. Solo mode never touches the server.
- **Who requested a song + click-to-listen**: the bot announces the requester and a clickable button → client command `/songmod join <player>` → `POST /api/song/join` with zero chat traffic; falls back to plain text when the bot has no OP.
- **Song GUI fixes**: roomier layout, speed buttons 2.0→0.5, listener spam/ghost entries fixed, seek `Index out of bounds` fixed, progress-bar dragging fixed (no snap-back, draggable once duration is known, automatic restart-from-position fallback when in-place seek does not take effect).
- Regression: 169 Python tests, all 15 mod jars rebuilt, installer silent-install self-check passed.

### v0.3.6-26.11.14

- **Both GUIs now expose the full feature set**: the CustomTkinter UI gained every tab (AI / player config, doc-to-book, player roster, QQ relay, reconnect, misc, port scan, logs, about; theme switch). The original tkinter UI also received port scanning plus support-ticket / dev-blog buttons, and both share the same `data.db`.
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
