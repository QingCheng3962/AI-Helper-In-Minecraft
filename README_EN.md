<p align="right"><a href="README.md">简体中文</a> · <b>English</b></p>

# AI Helper In Minecraft

A "client-less" Minecraft AI player: it never opens the game itself. A GUI drives a mineflayer engine to join a server, and a large language model powers the in-game chat behaviour.

- The front end is a Python tkinter GUI with multiple tabs: **Settings / AI Config / Player Config / Roster / QQ Relay / Reconnect·Kick / Appearance / Game Output**. The Node.js (mineflayer) engine handles the connection and protocol in the background.
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
- Node.js 14+ (18+ recommended)
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

MIT
