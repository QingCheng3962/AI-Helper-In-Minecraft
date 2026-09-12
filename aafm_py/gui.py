"""tkinter GUI for the headless AI-player bot.

Three pages:
  1. 设置页     - Minecraft server + login (offline / LittleSkin / Microsoft)
                 + AI API + 辅助功能(quiz / autoeat)
  2. 玩家配置页  - AI player chat behaviour (prompt / triggers / filters / image)
  3. 游戏输出页  - live game & chat output, chat input
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .config import (AiPlayerConfig, AppConfig, AUTH_PROFILE_FILE,
                     DEFAULT_BASE_URLS, DEFAULT_LITTLESKIN_REDIRECT_PORT,
                     TEMPLATE_ASSISTANT, TEMPLATE_BUDDY, PromptTemplateManager,
                     load_auth_profile)
from .controller import Controller, BASE_DIR

TEMPLATE_BUILTINS = {
    '聊天搭子': TEMPLATE_BUDDY,
    '助手': TEMPLATE_ASSISTANT,
}


def _build_templates() -> dict:
    tpls = dict(TEMPLATE_BUILTINS)
    for t in PromptTemplateManager.get_all():
        if t.get('name') and t.get('prompt'):
            tpls[t['name']] = t['prompt']
    return tpls


_PROVIDERS = ['openai', 'deepseek', 'moonshot', 'anthropic', 'ollama']

_THEME_AUTO = '自动（按背景）'
_THEME_LIGHT = '浅色背景（黑字）'
_THEME_DARK = '深色背景（白字）'
_THEME_LABEL_TO_VALUE = {_THEME_AUTO: 'auto', _THEME_LIGHT: 'light', _THEME_DARK: 'dark'}
_THEME_VALUE_TO_LABEL = {v: k for k, v in _THEME_LABEL_TO_VALUE.items()}


class ScrollFrame(ttk.Frame):
    """A vertically scrollable ttk frame."""

    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')

        self.inner.bind('<Configure>', lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(
            self._window, width=e.width))
        self.canvas.bind('<Enter>', self._bind_wheel)
        self.canvas.bind('<Leave>', self._unbind_wheel)

    def _bind_wheel(self, _e):
        self.canvas.bind_all('<MouseWheel>', self._on_wheel)

    def _unbind_wheel(self, _e):
        self.canvas.unbind_all('<MouseWheel>')

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')


class AiPlayerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.controller = Controller()
        self.root.title('Aafm AI 玩家 - 无客户端机器人')
        self.root.geometry('900x680')
        self.root.minsize(780, 580)

        self._tick = 0
        self._relogin_pending = False
        self._relogin_baseline = None
        self._relogin_stable_since = 0.0
        self._bg_image = None
        self._bg_photo = None
        self._bg_resize_job = None
        self._palette = None
        self._build_background()
        self._build_toolbar()
        self._build_notebook()
        self._load_config_into_widgets()
        self._apply_theme()
        self.root.bind('<Configure>', self._on_root_configure)
        self.root.after(80, self._refresh_background_image)

        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.after(100, self._poll_ui)

    # ------------------------------------------------------------------
    # Background image / theming
    # ------------------------------------------------------------------
    def _build_background(self) -> None:
        self.bg_canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        if self._bg_resize_job is not None:
            try:
                self.root.after_cancel(self._bg_resize_job)
            except Exception:  # noqa: BLE001
                pass
        self._bg_resize_job = self.root.after(200, self._render_background)

    def _background_brightness(self) -> float:
        """Average brightness (0-1) of the loaded background image."""
        if self._bg_image is None:
            return 0.12
        try:
            small = self._bg_image.resize((1, 1))
            r, g, b = small.getpixel((0, 0))
            return (r + g + b) / 3.0 / 255.0
        except Exception:  # noqa: BLE001
            return 0.12

    def _use_dark_theme(self) -> bool:
        """True => dark background with white text; False => light with black text."""
        theme = self.controller.config.appearance.textTheme
        if theme == 'light':
            return False
        if theme == 'dark':
            return True
        darkness = max(0, min(100, self.controller.config.appearance.backgroundDarkness)) / 100.0
        return self._background_brightness() * (1.0 - darkness) <= 0.5

    def _palette_for(self, dark: bool) -> dict:
        if dark:
            return {'bg': '#141414', 'panel': '#232323', 'fg': '#e8e8e8',
                    'muted': '#9a9a9a', 'field': '#2d2d30', 'select': '#0d5a8a',
                    'text_bg': '#1b1b1b', 'border': '#3f3f46', 'accent': '#4fc3f7'}
        return {'bg': '#e9e9e9', 'panel': '#f7f7f7', 'fg': '#111111',
                'muted': '#5a5a5a', 'field': '#ffffff', 'select': '#bcdcf5',
                'text_bg': '#ffffff', 'border': '#bdbdbd', 'accent': '#1565c0'}

    def _apply_theme(self) -> None:
        dark = self._use_dark_theme()
        pal = self._palette_for(dark)
        self._palette = pal

        self.root.configure(bg=pal['bg'])
        if self.bg_canvas is not None:
            self.bg_canvas.configure(bg=pal['bg'])

        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:  # noqa: BLE001
            pass

        style.configure('.', background=pal['panel'], foreground=pal['fg'],
                        fieldbackground=pal['field'], bordercolor=pal['border'],
                        lightcolor=pal['panel'], darkcolor=pal['panel'])
        style.configure('TFrame', background=pal['panel'])
        style.configure('TLabel', background=pal['panel'], foreground=pal['fg'])
        style.configure('TLabelframe', background=pal['panel'], bordercolor=pal['border'])
        style.configure('TLabelframe.Label', background=pal['panel'], foreground=pal['accent'])
        style.configure('TButton', background=pal['field'], foreground=pal['fg'],
                        bordercolor=pal['border'], focuscolor=pal['panel'])
        style.map('TButton',
                  background=[('active', pal['select']), ('pressed', pal['select'])],
                  foreground=[('disabled', pal['muted'])])
        style.configure('TCheckbutton', background=pal['panel'], foreground=pal['fg'])
        style.map('TCheckbutton', background=[('active', pal['panel'])])
        style.configure('TRadiobutton', background=pal['panel'], foreground=pal['fg'])
        style.map('TRadiobutton', background=[('active', pal['panel'])])
        style.configure('TEntry', fieldbackground=pal['field'], foreground=pal['fg'],
                        insertcolor=pal['fg'])
        style.configure('TCombobox', fieldbackground=pal['field'], foreground=pal['fg'],
                        background=pal['field'], arrowcolor=pal['fg'], bordercolor=pal['border'])
        style.map('TCombobox',
                  fieldbackground=[('readonly', pal['field'])],
                  foreground=[('readonly', pal['fg'])])
        style.configure('TNotebook', background=pal['bg'], bordercolor=pal['border'])
        style.configure('TNotebook.Tab', background=pal['panel'], foreground=pal['muted'],
                        padding=(12, 6))
        style.map('TNotebook.Tab',
                  background=[('selected', pal['select'])],
                  foreground=[('selected', pal['fg'])])
        style.configure('TScrollbar', background=pal['panel'], troughcolor=pal['bg'],
                        bordercolor=pal['border'], arrowcolor=pal['fg'])
        style.configure('TSeparator', background=pal['border'])
        style.configure('TScale', background=pal['panel'], troughcolor=pal['field'])

        self._theme_tk_widgets(self.root, pal)
        self._configure_log_tags(pal)

    def _theme_tk_widgets(self, widget, pal) -> None:
        for child in widget.winfo_children():
            if isinstance(child, tk.Text):
                child.configure(bg=pal['text_bg'], fg=pal['fg'],
                                insertbackground=pal['fg'])
            elif isinstance(child, tk.Listbox):
                child.configure(bg=pal['field'], fg=pal['fg'],
                                selectbackground=pal['select'], selectforeground=pal['fg'])
            elif isinstance(child, tk.Canvas):
                child.configure(bg=pal['panel'])
            self._theme_tk_widgets(child, pal)

    def _configure_log_tags(self, pal) -> None:
        self.log_text.tag_configure('info', foreground=pal['muted'])
        self.log_text.tag_configure('warn', foreground='#e8a13a')
        self.log_text.tag_configure('error', foreground='#e05555')
        self.log_text.tag_configure('ai', foreground=pal['accent'])
        self.log_text.tag_configure('sent', foreground='#4caf50')
        self.log_text.tag_configure('quiz', foreground='#c9a227')
        self.log_text.tag_configure('chat', foreground=pal['fg'])
        self.log_text.tag_configure('private', foreground='#c85fb0')
        self.log_text.tag_configure('msa', foreground='#3fa86a')
        self.log_text.tag_configure('qq', foreground='#9a6fd0')

    def _refresh_background_image(self) -> None:
        """(Re)load the configured background image from disk."""
        path = (self.controller.config.appearance.backgroundImage or '').strip()
        image = None
        if path and os.path.exists(path):
            try:
                from PIL import Image
                image = Image.open(path).convert('RGB')
            except Exception:  # noqa: BLE001
                image = None
        self._bg_image = image
        self._apply_theme()
        self._render_background()

    def _render_background(self) -> None:
        self._bg_resize_job = None
        if self.bg_canvas is None:
            return
        self.bg_canvas.delete('all')
        pal = self._palette or self._palette_for(True)
        if self._bg_image is None:
            self.bg_canvas.configure(bg=pal['bg'])
            return
        try:
            from PIL import Image, ImageTk
            width = max(1, self.root.winfo_width())
            height = max(1, self.root.winfo_height())
            img = self._bg_image
            scale = max(width / img.width, height / img.height)
            new_w = max(1, int(img.width * scale) + 1)
            new_h = max(1, int(img.height * scale) + 1)
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - width) // 2
            top = (new_h - height) // 2
            cropped = resized.crop((left, top, left + width, top + height))
            darkness = max(0, min(100, self.controller.config.appearance.backgroundDarkness))
            if darkness > 0:
                overlay = Image.new('RGB', cropped.size, (0, 0, 0))
                cropped = Image.blend(cropped, overlay, darkness / 100.0)
            self._bg_photo = ImageTk.PhotoImage(cropped)
            self.bg_canvas.create_image(0, 0, anchor='nw', image=self._bg_photo)
        except Exception:  # noqa: BLE001
            self._bg_image = None
            self.bg_canvas.configure(bg=pal['bg'])

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(side='top', fill='x', padx=12, pady=(12, 4))

        self.status_label = ttk.Label(bar, text='未连接', font=('Segoe UI', 10, 'bold'),
                                      foreground='#999999')
        self.status_label.pack(side='left')

        self.connect_btn = ttk.Button(bar, text='连接', command=self._on_connect)
        self.connect_btn.pack(side='left', padx=(16, 4))
        self.disconnect_btn = ttk.Button(bar, text='断开', command=self._on_disconnect,
                                         state='disabled')
        self.disconnect_btn.pack(side='left', padx=4)

        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=12)

        self.ai_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='AI 玩家开关', variable=self.ai_enabled,
                        command=self._on_ai_enabled).pack(side='left', padx=4)
        ttk.Button(bar, text='重开上下文', command=self._on_clear_context).pack(side='left', padx=4)
        ttk.Button(bar, text='保存设置', command=self._on_save).pack(side='left', padx=4)

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        self._build_settings_tab()
        self._build_ai_tab()
        self._build_player_tab()
        self._build_roster_tab()
        self._build_qq_tab()
        self._build_reconnect_tab()
        self._build_appearance_tab()
        self._build_output_tab()

    # ------------------------------------------------------------------
    # Tab 1: 设置 (Minecraft 服务器 + 登录方式)
    # ------------------------------------------------------------------
    def _build_settings_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' 设置 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)
        frm = sc.inner

        # --- Server ---
        server = ttk.LabelFrame(frm, text='Minecraft 服务器', padding=8)
        server.pack(fill='x', pady=(0, 8))

        self.var_host = tk.StringVar()
        self.var_port = tk.StringVar()
        self.var_version = tk.StringVar()
        self.var_brand = tk.StringVar()
        self.var_view = tk.StringVar()

        row = 0
        self._entry(server, row, '地址', self.var_host); row += 1
        self._entry(server, row, '端口', self.var_port); row += 1
        self._entry(server, row, '版本', self.var_version); row += 1
        self._entry(server, row, '品牌', self.var_brand); row += 1
        self._entry(server, row, '视距', self.var_view); row += 1

        # --- Login ---
        auth = ttk.LabelFrame(frm, text='登录方式', padding=8)
        auth.pack(fill='x', pady=(0, 8))

        self.var_auth_type = tk.StringVar(value='offline')
        radios = ttk.Frame(auth)
        radios.grid(row=0, column=0, columnspan=4, sticky='w', padx=4, pady=2)
        ttk.Radiobutton(radios, text='离线模式', variable=self.var_auth_type,
                        value='offline', command=self._on_auth_type_changed).pack(side='left', padx=(0, 8))
        ttk.Radiobutton(radios, text='LittleSkin', variable=self.var_auth_type,
                        value='littleskin', command=self._on_auth_type_changed).pack(side='left', padx=(0, 8))
        ttk.Radiobutton(radios, text='正版（微软账户）', variable=self.var_auth_type,
                        value='microsoft', command=self._on_auth_type_changed).pack(side='left')

        # Common username row
        ttk.Label(auth, text='用户名').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        self.var_username = tk.StringVar()
        ttk.Entry(auth, textvariable=self.var_username, width=30).grid(
            row=1, column=1, columnspan=3, sticky='we', padx=4, pady=3)
        self.username_hint = ttk.Label(auth, text='离线模式下的进服名称', foreground='#888888')
        self.username_hint.grid(row=1, column=4, sticky='w', padx=4, pady=3)

        # --- LittleSkin specific frame ---
        self.ls_frame = ttk.LabelFrame(auth, text='LittleSkin 设置', padding=6)
        self.ls_frame.grid(row=2, column=0, columnspan=5, sticky='we', padx=2, pady=(4, 2))

        self.var_site = tk.StringVar()
        self.var_client_id = tk.StringVar()
        self.var_client_secret = tk.StringVar()
        self.var_redirect_port = tk.StringVar()
        self._entry(self.ls_frame, 0, '站点地址', self.var_site,
                    hint='例 https://littleskin.cn')
        self._entry(self.ls_frame, 1, 'Client ID', self.var_client_id, col=2)
        self._entry(self.ls_frame, 2, 'Client Secret', self.var_client_secret, col=0, show='*')
        self._entry(self.ls_frame, 3, '回调端口', self.var_redirect_port, col=2)
        ttk.Button(self.ls_frame, text='LittleSkin 登录', command=self._on_littleskin_login).grid(
            row=4, column=0, columnspan=4, sticky='w', padx=4, pady=(6, 2))
        self.auth_profile_label = ttk.Label(self.ls_frame, text='未登录', foreground='#888888')
        self.auth_profile_label.grid(row=5, column=0, columnspan=5, sticky='w', padx=4)
        self.ls_frame.columnconfigure(1, weight=1)
        self.ls_frame.columnconfigure(3, weight=1)

        # --- Microsoft specific frame ---
        self.ms_frame = ttk.LabelFrame(auth, text='正版（微软）登录', padding=6)
        self.ms_frame.grid(row=3, column=0, columnspan=5, sticky='we', padx=2, pady=(4, 2))

        self.var_profiles_folder = tk.StringVar()
        ttk.Label(self.ms_frame, text='缓存目录（留空=默认）').grid(
            row=0, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(self.ms_frame, textvariable=self.var_profiles_folder, width=34).grid(
            row=0, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Button(self.ms_frame, text='正版登录（微软）', command=self._on_msa_login).grid(
            row=1, column=0, columnspan=3, sticky='w', padx=4, pady=(2, 4))
        ttk.Label(self.ms_frame,
                  text='提示：用户名将作为该账户的缓存标识；登录一次后自动静默复用，'
                       '账号须已拥有 Minecraft。',
                  foreground='#888888', wraplength=480, justify='left').grid(
            row=2, column=0, columnspan=4, sticky='w', padx=4, pady=(0, 2))
        self.ms_profile_label = ttk.Label(self.ms_frame, text='未登录', foreground='#888888')
        self.ms_profile_label.grid(row=3, column=0, columnspan=4, sticky='w', padx=4)

        auth.columnconfigure(1, weight=1)

    # ------------------------------------------------------------------
    # Tab 2: AI 配置 (AI API + 辅助功能)
    # ------------------------------------------------------------------
    def _build_ai_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' AI 配置 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)
        frm = sc.inner

        # --- AI API ---
        api = ttk.LabelFrame(frm, text='AI API（OpenAI 兼容端点，含 Anthropic）', padding=8)
        api.pack(fill='x', pady=(0, 8))

        self.var_provider = tk.StringVar()
        self.var_base_url = tk.StringVar()
        self.var_api_key = tk.StringVar()
        self.var_model = tk.StringVar()
        self.var_temperature = tk.StringVar()
        self.var_max_tokens = tk.StringVar()
        self.var_timeout = tk.StringVar()
        self.var_retry = tk.StringVar()

        row = 0
        ttk.Label(api, text='提供方').grid(row=row, column=0, sticky='w', padx=4, pady=3)
        self.provider_box = ttk.Combobox(api, textvariable=self.var_provider, values=_PROVIDERS)
        self.provider_box.grid(row=row, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        self.provider_box.bind('<<ComboboxSelected>>', lambda e: self._on_provider_changed())
        row += 1
        self._entry(api, row, 'API 地址', self.var_base_url, hint='留空则使用提供方默认'); row += 1
        self._entry(api, row, 'API 密钥', self.var_api_key, show='*'); row += 1
        self._entry(api, row, '模型', self.var_model); row += 1
        self._entry(api, row, '温度 (0-2)', self.var_temperature); row += 1
        self._entry(api, row, '最大 Token', self.var_max_tokens); row += 1
        self._entry(api, row, '超时秒', self.var_timeout); row += 1
        self._entry(api, row, '重试次数', self.var_retry); row += 1

        ttk.Label(api, text='提示：Ollama 本地模型可留空 API 密钥；baseUrl 形如 '
                            'https://api.deepseek.com/v1', foreground='#888888').grid(
            row=row, column=0, columnspan=5, sticky='w', padx=4, pady=(4, 0))

        # --- 辅助功能 ---
        extra = ttk.LabelFrame(frm, text='辅助功能', padding=8)
        extra.pack(fill='x')
        self.var_quiz = tk.BooleanVar(value=True)
        self.var_autoeat = tk.BooleanVar(value=True)
        ttk.Checkbutton(extra, text='答题（Quiz 自动答题）', variable=self.var_quiz,
                        command=lambda: self._on_extra('quiz', self.var_quiz.get())).pack(side='left')
        ttk.Checkbutton(extra, text='自动进食（AutoEat）', variable=self.var_autoeat,
                        command=lambda: self._on_extra('autoeat', self.var_autoeat.get())).pack(
            side='left', padx=16)

    # ------------------------------------------------------------------
    # Tab: 玩家资料库
    # ------------------------------------------------------------------
    def _build_roster_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' 玩家资料库 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)
        self._build_roster_frame(sc.inner)

    # ------------------------------------------------------------------
    # Tab: QQ 转述
    # ------------------------------------------------------------------
    def _build_qq_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' QQ 转述 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)
        self._build_qq_frame(sc.inner)

    # ------------------------------------------------------------------
    # Tab: 重连 / 踢出
    # ------------------------------------------------------------------
    def _build_reconnect_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' 重连/踢出 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)
        frm = ttk.LabelFrame(sc.inner, text='非主动断线与被踢出后的自动处理', padding=8)
        frm.pack(fill='x', pady=(8, 0))

        self.var_reconnect_enabled = tk.BooleanVar(value=True)
        self.var_reconnect_delay = tk.StringVar()
        self.var_reconnect_max = tk.StringVar()
        self.var_kick_lobby = tk.BooleanVar(value=True)
        self.var_kick_lobby_delay = tk.StringVar()

        ttk.Checkbutton(frm, text='启用自动重连（服务器掉线 / 被踢出时；手动点「断开」不会重连）',
                        variable=self.var_reconnect_enabled).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=4, pady=3)
        self._entry(frm, 1, '重连延迟（秒）', self.var_reconnect_delay)
        self._entry(frm, 2, '最大重连次数（重连成功后重置）', self.var_reconnect_max)

        ttk.Checkbutton(frm, text='被踢出并重连成功后自动发送 /lobby',
                        variable=self.var_kick_lobby).grid(
            row=3, column=0, columnspan=4, sticky='w', padx=4, pady=(8, 3))
        self._entry(frm, 4, '/lobby 延迟（秒）', self.var_kick_lobby_delay)

        ttk.Label(frm,
                  text='说明：连接意外结束（被踢/掉线）时按上面设置自动重连；若属于 LittleSkin '
                       '登录失效，则走自动重新登录流程，不做普通重连。重连成功进入服务器后，'
                       '等待「/lobby 延迟」再发送 /lobby。',
                  foreground='#888888', wraplength=720, justify='left').grid(
            row=5, column=0, columnspan=5, sticky='w', padx=4, pady=(6, 0))

    # ------------------------------------------------------------------
    # Tab: 界面美化
    # ------------------------------------------------------------------
    def _build_appearance_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' 界面美化 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)
        frm = ttk.LabelFrame(sc.inner, text='窗口背景图片与明暗', padding=8)
        frm.pack(fill='x', pady=(8, 0))

        self.var_bg_image = tk.StringVar()
        self.var_bg_darkness = tk.IntVar(value=40)
        self.var_text_theme = tk.StringVar(value=_THEME_AUTO)

        ttk.Label(frm, text='背景图片').grid(row=0, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_bg_image, width=52).grid(
            row=0, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Button(frm, text='选择图片…', command=self._on_choose_background).grid(
            row=0, column=3, sticky='w', padx=4, pady=3)
        ttk.Button(frm, text='清除', command=self._on_clear_background).grid(
            row=0, column=4, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='支持 PNG / JPG；图片仅作为窗口背景，面板保持纯色。',
                  foreground='#888888').grid(row=1, column=0, columnspan=5, sticky='w', padx=4)

        ttk.Label(frm, text='暗度').grid(row=2, column=0, sticky='w', padx=4, pady=(8, 3))
        ttk.Scale(frm, from_=0, to=100, orient='horizontal',
                  variable=self.var_bg_darkness,
                  command=self._on_darkness_changed).grid(
            row=2, column=1, columnspan=2, sticky='we', padx=4, pady=(8, 3))
        self.bg_darkness_label = ttk.Label(frm, text='40%', foreground='#888888')
        self.bg_darkness_label.grid(row=2, column=3, sticky='w', padx=4)

        ttk.Label(frm, text='字体主题').grid(row=3, column=0, sticky='w', padx=4, pady=3)
        self.theme_combo = ttk.Combobox(frm, textvariable=self.var_text_theme, state='readonly',
                                        values=[_THEME_AUTO, _THEME_LIGHT, _THEME_DARK], width=24)
        self.theme_combo.grid(row=3, column=1, sticky='w', padx=4, pady=3)
        self.theme_combo.bind('<<ComboboxSelected>>', lambda e: self._on_text_theme_changed())

        ttk.Button(frm, text='应用并保存', command=self._on_apply_appearance).grid(
            row=4, column=0, sticky='w', padx=4, pady=(10, 2))
        ttk.Label(frm, text='「自动」会根据背景图片亮度决定黑字/白字：亮底黑字、暗底白字。',
                  foreground='#888888', wraplength=720, justify='left').grid(
            row=5, column=0, columnspan=5, sticky='w', padx=4)
        frm.columnconfigure(1, weight=1)

    def _on_choose_background(self) -> None:
        path = filedialog.askopenfilename(
            title='选择背景图片',
            filetypes=[('图片', '*.png *.jpg *.jpeg'), ('所有文件', '*.*')])
        if not path:
            return
        self.var_bg_image.set(path)
        self.controller.config.appearance.backgroundImage = path
        self._refresh_background_image()

    def _on_clear_background(self) -> None:
        self.var_bg_image.set('')
        self.controller.config.appearance.backgroundImage = ''
        self._refresh_background_image()

    def _on_darkness_changed(self, _value=None) -> None:
        value = int(self.var_bg_darkness.get())
        self.controller.config.appearance.backgroundDarkness = value
        try:
            self.bg_darkness_label.config(text=f'{value}%')
        except Exception:  # noqa: BLE001
            pass
        self._render_background()

    def _on_text_theme_changed(self) -> None:
        self.controller.config.appearance.textTheme = _THEME_LABEL_TO_VALUE.get(
            self.var_text_theme.get(), 'auto')
        self._apply_theme()
        self._render_background()

    def _on_apply_appearance(self) -> None:
        self._gather_config()
        self._refresh_background_image()
        self._append_log('界面设置已保存。', 'info')
        messagebox.showinfo('aafm AI 玩家', '界面设置已保存。')

    def _build_qq_frame(self, parent) -> None:
        frm = ttk.LabelFrame(
            parent, text='QQ 群聊转述（OneBot 11：NapCat / Lagrange / go-cqhttp）', padding=8)
        frm.pack(fill='x', pady=(8, 0))

        self.var_qq_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text='启用：把指定 QQ 群的消息转述到 MC 公共聊天',
                        variable=self.var_qq_enabled,
                        command=self._on_qq_enabled).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=4, pady=3)

        self.var_qq_mode = tk.StringVar(value='forward')
        ttk.Label(frm, text='连接模式').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        mode_box = ttk.Combobox(frm, textvariable=self.var_qq_mode, state='readonly',
                                values=['forward', 'reverse'], width=12)
        mode_box.grid(row=1, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='forward=连接网关 / reverse=监听网关连入',
                  foreground='#888888').grid(row=1, column=2, columnspan=2, sticky='w', padx=4)

        self.var_qq_url = tk.StringVar()
        ttk.Label(frm, text='正向地址').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_qq_url, width=42).grid(
            row=2, column=1, columnspan=3, sticky='we', padx=4, pady=3)

        self.var_qq_token = tk.StringVar()
        ttk.Label(frm, text='Access Token').grid(row=3, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_qq_token, show='*', width=42).grid(
            row=3, column=1, columnspan=3, sticky='we', padx=4, pady=3)

        self.var_qq_listen_host = tk.StringVar()
        self.var_qq_listen_port = tk.StringVar()
        ttk.Label(frm, text='反向监听').grid(row=4, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_qq_listen_host, width=18).grid(
            row=4, column=1, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_qq_listen_port, width=8).grid(
            row=4, column=2, sticky='w', padx=4, pady=3)

        self.var_qq_groups = tk.StringVar()
        ttk.Label(frm, text='群号').grid(row=5, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_qq_groups, width=42).grid(
            row=5, column=1, columnspan=3, sticky='we', padx=4, pady=3)
        ttk.Label(frm, text='多个用逗号/空格分隔；留空=转述全部群',
                  foreground='#888888').grid(row=5, column=4, sticky='w', padx=4)

        self.var_qq_format = tk.StringVar()
        ttk.Label(frm, text='转述格式').grid(row=6, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_qq_format, width=42).grid(
            row=6, column=1, columnspan=3, sticky='we', padx=4, pady=3)

        ttk.Label(frm,
                  text='占位符：{group}=群名 {name}=昵称 {message}=内容；默认 &7&o 为灰色斜体。'
                       '图片/表情等非文本会以 [图片] / [表情] 等占位转述。',
                  foreground='#888888', wraplength=720, justify='left').grid(
            row=7, column=0, columnspan=5, sticky='w', padx=4, pady=(2, 0))

        self.qq_status_label = ttk.Label(frm, text='未连接', foreground='#999999')
        self.qq_status_label.grid(row=8, column=0, columnspan=2, sticky='w', padx=4, pady=(4, 2))
        ttk.Button(frm, text='启动/重连 QQ', command=self._on_qq_start).grid(
            row=8, column=2, sticky='w', padx=4, pady=(4, 2))
        ttk.Button(frm, text='停止 QQ', command=self._on_qq_stop).grid(
            row=8, column=3, sticky='w', padx=4, pady=(4, 2))

        # --- MC -> QQ ---
        mc = ttk.LabelFrame(frm, text='MC → QQ 转述（公屏消息以触发词开头即发送到 QQ）', padding=6)
        mc.grid(row=9, column=0, columnspan=5, sticky='we', padx=2, pady=(8, 2))

        self.var_mc2qq_enabled = tk.BooleanVar(value=False)
        self.var_mc2qq_trigger = tk.StringVar()
        self.var_mc2qq_group = tk.StringVar()
        self.var_mc2qq_format = tk.StringVar()
        self.var_mc2qq_cooldown = tk.StringVar()

        ttk.Checkbutton(mc, text='启用 MC → QQ', variable=self.var_mc2qq_enabled).grid(
            row=0, column=0, sticky='w', padx=4, pady=3)
        ttk.Label(mc, text='触发词（, 分隔）').grid(row=0, column=1, sticky='e', padx=4)
        ttk.Entry(mc, textvariable=self.var_mc2qq_trigger, width=24).grid(
            row=0, column=2, columnspan=2, sticky='w', padx=4)

        ttk.Label(mc, text='目标群').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(mc, textvariable=self.var_mc2qq_group, width=18).grid(
            row=1, column=1, sticky='w', padx=4)
        ttk.Label(mc, text='留空=用上方群号第一个；也可用 [sentQ:群号] 临时指定',
                  foreground='#888888').grid(row=1, column=2, columnspan=2, sticky='w', padx=4)

        ttk.Label(mc, text='消息模板').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(mc, textvariable=self.var_mc2qq_format, width=38).grid(
            row=2, column=1, columnspan=2, sticky='we', padx=4)
        ttk.Label(mc, text='{player} / {message}', foreground='#888888').grid(
            row=2, column=3, sticky='w', padx=4)

        ttk.Label(mc, text='冷却秒').grid(row=3, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(mc, textvariable=self.var_mc2qq_cooldown, width=6).grid(
            row=3, column=1, sticky='w', padx=4)
        ttk.Label(mc, text='发送前自动去掉 MC 颜色码；非法字符以 ? 代替',
                  foreground='#888888').grid(row=3, column=2, columnspan=2, sticky='w', padx=4)
        mc.columnconfigure(1, weight=1)

        frm.columnconfigure(1, weight=1)

    def _build_roster_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent,
                             text='玩家是谁 + AI 性格/别名研究（列：玩家名|玩家描述|别名1..3|描述1..|AI记录1..5）', padding=8)
        frm.pack(fill='x', pady=(8, 0))

        self.var_roster = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text='启用身份问答：问「xxx是谁」直接查表；玩家说「X就是Y/X是Y」会写入其「玩家描述」',
                        variable=self.var_roster,
                        command=self._on_roster_enabled).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=4, pady=3)
        ttk.Button(frm, text='导入 xlsx / csv…', command=self._on_roster_import).grid(
            row=0, column=3, sticky='e', padx=4, pady=3)
        ttk.Button(frm, text='下载模板', command=self._on_roster_template).grid(
            row=0, column=4, sticky='e', padx=4, pady=3)

        self.roster_list = tk.Listbox(frm, height=4, width=90)
        self.roster_list.grid(row=1, column=0, columnspan=3, sticky='we', padx=4, pady=3)
        roster_scroll = ttk.Scrollbar(frm, orient='vertical', command=self.roster_list.yview)
        roster_scroll.grid(row=1, column=3, sticky='ns')
        self.roster_list.configure(yscrollcommand=roster_scroll.set)

        ttk.Button(frm, text='删除所选资料表', command=self._on_roster_delete).grid(
            row=2, column=0, sticky='w', padx=4, pady=(0, 2))
        self.roster_status = ttk.Label(frm, text='', foreground='#888888')
        self.roster_status.grid(row=2, column=1, columnspan=4, sticky='w', padx=4, pady=(0, 2))

        # --- AI 性格研究 / 别名分析 ---
        self.var_roster_research = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text='AI 性格研究：阅读聊天自动研究并写入 AI记录1..5（滚动最新5条）；说「研究X」可手动',
                        variable=self.var_roster_research,
                        command=self._on_roster_research).grid(
            row=3, column=0, columnspan=4, sticky='w', padx=4, pady=3)
        self.var_roster_alias = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text='AI 别名分析：读聊天识别别名，写入 别名1..3 空格（不覆盖手填）；说「X的别名」可手动',
                        variable=self.var_roster_alias,
                        command=self._on_roster_alias).grid(
            row=4, column=0, columnspan=4, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='间隔秒').grid(row=3, column=4, sticky='e', padx=(4, 2), pady=3)
        self.var_roster_research_interval = tk.StringVar(value='180')
        interval_entry = ttk.Entry(frm, textvariable=self.var_roster_research_interval,
                                   width=6, justify='center')
        interval_entry.grid(row=3, column=5, sticky='w', padx=(0, 4), pady=3)
        interval_entry.bind('<Return>', lambda e: self._on_roster_research())
        frm.columnconfigure(4, weight=1)
        frm.columnconfigure(5, weight=1)

        ttk.Label(frm,
                  text='列模型：第1列玩家名；第2列「玩家描述」（权威列，可留空）；第3~5列 别名1/别名2/别名3；'
                       '其后 描述1/描述2/… 随机回答列；最右 AI记录1..5。'
                       '回答「xxx是谁」：优先玩家描述→随机描述；若问的是某玩家的别名（如“碧空是谁”，碧空是 Be_kong 的别名）也按该玩家回答。'
                       '别名/描述/AI记录 均可直接在 xlsx/csv 里手改；AI 只填空别名格、不覆盖手填；身份「X就是Y」写回玩家描述列。'
                       '模板按钮用于另存一张空表自行管理。',
                  foreground='#888888', wraplength=720, justify='left').grid(
            row=5, column=0, columnspan=6, sticky='w', padx=4)
        frm.columnconfigure(0, weight=1)
        self._refresh_roster_list()

    # ------------------------------------------------------------------
    # Tab 2: 玩家配置页
    # ------------------------------------------------------------------
    def _build_player_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' 玩家配置 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)
        frm = sc.inner

        self._build_prompt_frame(frm)
        self._build_trigger_frame(frm)
        self._build_block_frame(frm)

    def _build_prompt_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent, text='提示词与上下文', padding=8)
        frm.pack(fill='x', pady=(0, 8))

        self.var_template = tk.StringVar(value='聊天搭子')
        ttk.Label(frm, text='模板').grid(row=0, column=0, sticky='w', padx=4, pady=3)
        self.template_combo = ttk.Combobox(frm, textvariable=self.var_template,
                     values=list(_build_templates().keys()),
                     state='readonly')
        self.template_combo.grid(row=0, column=1, sticky='w', padx=4, pady=3)
        ttk.Button(frm, text='应用模板', command=self._on_apply_template).grid(
            row=0, column=2, padx=4)

        self.var_new_tpl_name = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_new_tpl_name, width=14).grid(
            row=0, column=3, sticky='w', padx=(8, 2), pady=3)
        ttk.Button(frm, text='保存为模板', command=self._on_save_template).grid(
            row=0, column=4, padx=2)
        ttk.Button(frm, text='删除模板', command=self._on_delete_template).grid(
            row=0, column=5, padx=2)

        ttk.Label(frm, text='系统提示词').grid(row=1, column=0, sticky='nw', padx=4, pady=3)
        prompt_txt = tk.Text(frm, height=7, width=72, wrap='word')
        prompt_txt.grid(row=1, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        self.system_prompt_widget = prompt_txt
        prompt_scroll = ttk.Scrollbar(frm, orient='vertical', command=prompt_txt.yview)
        prompt_scroll.grid(row=1, column=3, sticky='ns')
        prompt_txt.configure(yscrollcommand=prompt_scroll.set)
        frm.columnconfigure(1, weight=1)

        self.var_context = tk.BooleanVar(value=True)
        self.var_context_len = tk.StringVar()
        ttk.Checkbutton(frm, text='启用上下文', variable=self.var_context).grid(
            row=2, column=0, sticky='w', padx=4, pady=3)
        self._entry(frm, 2, '长度', self.var_context_len, col=1)
        self.var_max_msgs = tk.StringVar()
        self.var_max_chars = tk.StringVar()
        self._entry(frm, 2, '最多消息', self.var_max_msgs, col=2)
        self._entry(frm, 3, '单条字数', self.var_max_chars, col=1)

    def _build_trigger_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent, text='触发回复（命中任一个正则即回复；用聊天完整原文检测，非触发保持静默）', padding=8)
        frm.pack(fill='x', pady=(0, 8))

        self.var_trigger = tk.BooleanVar(value=True)
        self.var_trigger_regex = tk.StringVar()
        self.var_cooldown = tk.StringVar()
        self.var_auto = tk.BooleanVar(value=False)
        self.var_schedule = tk.BooleanVar(value=False)
        self.var_schedule_interval = tk.StringVar()

        ttk.Checkbutton(frm, text='启用触发回复', variable=self.var_trigger).grid(
            row=0, column=0, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='触发正则（, 分隔）').grid(row=0, column=1, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_trigger_regex, width=46).grid(
            row=0, column=2, columnspan=3, sticky='we', padx=4, pady=3)
        self._entry(frm, 1, '冷却秒', self.var_cooldown, col=1)

        ttk.Checkbutton(frm, text='自动回复（对每条消息回复）', variable=self.var_auto).grid(
            row=2, column=0, sticky='w', padx=4, pady=3)
        ttk.Checkbutton(frm, text='定时回复（需有匹配消息）', variable=self.var_schedule).grid(
            row=2, column=1, sticky='w', padx=4)
        self._entry(frm, 2, '间隔秒', self.var_schedule_interval, col=2)

        hint = ttk.Label(frm, text='例：填 [DiaoHelper],@Helper,!ai 时，玩家消息(含前缀/称号的完整原文)'
                                  '命中任意一个即回复。多个用英文逗号 , 隔开；留空=不回复。\n'
                                  '提示：单独一个 [名字] 会自动按字面量匹配(忽略大小写)，不会当成字符类；'
                                  '其它写法仍为完整正则。',
                         foreground='#888888', wraplength=680, justify='left')
        hint.grid(row=3, column=0, columnspan=6, sticky='w', padx=4, pady=(4, 0))

    def _build_block_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent, text='消息过滤 / 黑名单', padding=8)
        frm.pack(fill='x', pady=(0, 8))

        self.var_restrict = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text='启用消息过滤', variable=self.var_restrict).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=4, pady=3)

        self.var_block_regex = tk.StringVar()
        ttk.Label(frm, text='拦截正则（用 ; 分隔）').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_block_regex, width=50).grid(
            row=1, column=1, columnspan=3, sticky='we', padx=4, pady=3)

        ttk.Label(frm, text='黑名单玩家').grid(row=2, column=0, sticky='nw', padx=4, pady=3)
        self.blocked_list = tk.Listbox(frm, height=5)
        self.blocked_list.grid(row=2, column=1, columnspan=3, sticky='we', padx=4, pady=3)
        self.var_new_block = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_new_block, width=26).grid(
            row=3, column=1, sticky='w', padx=4, pady=3)
        ttk.Button(frm, text='添加', command=self._on_add_blocked).grid(row=3, column=2, padx=2)
        ttk.Button(frm, text='删除', command=self._on_remove_blocked).grid(
            row=3, column=3, sticky='w', padx=2)

        img = ttk.LabelFrame(parent, text='文生图（可选）', padding=8)
        img.pack(fill='x')
        self.var_image = tk.BooleanVar(value=False)
        self.var_image_model = tk.StringVar()
        ttk.Checkbutton(img, text='启用文生图', variable=self.var_image).pack(side='left', padx=4)
        ttk.Label(img, text='模型').pack(side='left', padx=(12, 2))
        ttk.Entry(img, textvariable=self.var_image_model, width=22).pack(side='left')

    # ------------------------------------------------------------------
    # Tab 3: 游戏输出页
    # ------------------------------------------------------------------
    def _build_output_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text=' 游戏输出 ')

        self.log_text = tk.Text(tab, height=22, wrap='word', state='disabled',
                                font=('Consolas', 9))
        self.log_text.pack(fill='both', expand=True)
        log_scroll = ttk.Scrollbar(tab, orient='vertical', command=self.log_text.yview)
        log_scroll.pack(side='right', fill='y')
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.tag_configure('info', foreground='#888888')
        self.log_text.tag_configure('warn', foreground='#ffb74d')
        self.log_text.tag_configure('error', foreground='#ff6b6b')
        self.log_text.tag_configure('ai', foreground='#8fb7ff')
        self.log_text.tag_configure('sent', foreground='#7ddb7d')
        self.log_text.tag_configure('quiz', foreground='#ffd54f')
        self.log_text.tag_configure('chat', foreground='#e9ecf1')
        self.log_text.tag_configure('private', foreground='#ff9ad8')
        self.log_text.tag_configure('msa', foreground='#b9f6ca')
        self.log_text.tag_configure('qq', foreground='#c792ea')

        bottom = ttk.Frame(tab)
        bottom.pack(fill='x', pady=(6, 0))
        self.chat_var = tk.StringVar()
        self.chat_entry = ttk.Entry(bottom, textvariable=self.chat_var)
        self.chat_entry.pack(side='left', fill='x', expand=True, padx=(0, 6))
        self.chat_entry.bind('<Return>', lambda e: self._on_send_chat())
        ttk.Button(bottom, text='发送', command=self._on_send_chat).pack(side='right')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _entry(self, parent, row, label, var, col=0, hint=None, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky='w', padx=4, pady=3)
        e = ttk.Entry(parent, textvariable=var, show=show or '', width=30)
        e.grid(row=row, column=col + 1, sticky='we', padx=4, pady=3)
        if hint:
            ttk.Label(parent, text=hint, foreground='#888888').grid(
                row=row, column=col + 2, sticky='w', padx=4)
        return e

    def _append_log(self, text: str, tag: str = 'info') -> None:
        self.log_text.configure(state='normal')
        self.log_text.insert('end', text + '\n', tag)
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def _set_connected_state(self, connected: bool):
        if connected:
            self.connect_btn.config(state='disabled')
            self.disconnect_btn.config(state='normal')
        else:
            self.connect_btn.config(state='normal')
            self.disconnect_btn.config(state='disabled')

    def _on_auth_type_changed(self):
        at = self.var_auth_type.get()
        if at == 'littleskin':
            self.ls_frame.grid()
            self.ms_frame.grid_remove()
            self.username_hint.config(text='离线兜底名称（LittleSkin 实际以所选角色为准）')
        elif at == 'microsoft':
            self.ls_frame.grid_remove()
            self.ms_frame.grid()
            self.username_hint.config(text='微软账户的缓存标识（可随意，建议用角色名）')
        else:
            self.ls_frame.grid_remove()
            self.ms_frame.grid_remove()
            self.username_hint.config(text='离线模式下的进服名称')

    def _load_config_into_widgets(self) -> None:
        cfg = self.controller.config
        ai = self.controller.ai_config

        self.var_host.set(cfg.server.host)
        self.var_port.set(str(cfg.server.port))
        self.var_version.set(cfg.server.version)
        self.var_brand.set(cfg.server.brand)
        self.var_view.set(cfg.server.viewDistance)
        self.var_auth_type.set(cfg.auth.type if cfg.auth.type in ('littleskin', 'microsoft') else 'offline')
        self.var_username.set(cfg.auth.username)
        self.var_site.set(cfg.auth.site_base())
        self.var_client_id.set(str(cfg.auth.clientId))
        self.var_client_secret.set(cfg.auth.clientSecret or '')
        self.var_redirect_port.set(str(cfg.auth.redirectPort))
        self.var_profiles_folder.set(cfg.auth.profilesFolder or '')
        self.ai_enabled.set(ai.enabled)

        self.var_provider.set(ai.provider or 'openai')
        self.var_base_url.set(ai.baseUrl or '')
        self.var_api_key.set(ai.apiKey or '')
        self.var_model.set(ai.model)
        self.var_temperature.set(str(ai.temperature))
        self.var_max_tokens.set(str(ai.maxTokens))
        self.var_timeout.set(str(ai.timeoutSeconds))
        self.var_retry.set(str(ai.retryCount))

        self.system_prompt_widget.delete('1.0', 'end')
        self.system_prompt_widget.insert('1.0', ai.systemPrompt)
        self.var_template.set(ai.templateName if ai.templateName in _build_templates() else '')
        self.var_context.set(ai.contextEnabled)
        self.var_context_len.set(str(ai.contextLength))
        self.var_max_msgs.set(str(ai.maxReplyMessages))
        self.var_max_chars.set(str(ai.maxCharsPerMessage))

        self.var_trigger.set(ai.triggerEnabled)
        self.var_trigger_regex.set(ai.triggerRegex)
        self.var_cooldown.set(str(ai.triggerCooldownSeconds))
        self.var_auto.set(ai.autoReplyEnabled)
        self.var_schedule.set(ai.scheduleEnabled)
        self.var_schedule_interval.set(str(ai.scheduleIntervalSeconds))

        self.var_restrict.set(ai.restrictionEnabled)
        self.var_block_regex.set(';'.join(ai.blockedRegexPatterns))
        self._refresh_blocked_list()

        self.var_image.set(ai.imageGenerationEnabled)
        self.var_image_model.set(ai.imageModel)

        self.var_quiz.set(cfg.quiz.enabled)
        self.var_autoeat.set(cfg.autoeat.enabled)
        self.var_roster.set(cfg.roster.enabled)
        self.var_roster_research.set(cfg.roster.personalityEnabled)
        self.var_roster_alias.set(cfg.roster.aliasEnabled)
        self.var_roster_research_interval.set(str(cfg.roster.researchIntervalSeconds))

        self.var_qq_enabled.set(cfg.qq.enabled)
        self.var_qq_mode.set(cfg.qq.mode if cfg.qq.mode in ('forward', 'reverse') else 'forward')
        self.var_qq_url.set(cfg.qq.url or '')
        self.var_qq_token.set(cfg.qq.accessToken or '')
        self.var_qq_listen_host.set(cfg.qq.listenHost or '0.0.0.0')
        self.var_qq_listen_port.set(str(cfg.qq.listenPort))
        self.var_qq_groups.set(', '.join(cfg.qq.groups or []))
        self.var_qq_format.set(cfg.qq.format or '')

        self.var_mc2qq_enabled.set(cfg.qq.mcToQqEnabled)
        self.var_mc2qq_trigger.set(cfg.qq.mcTrigger or '[sentQ]')
        self.var_mc2qq_group.set(cfg.qq.mcTargetGroup or '')
        self.var_mc2qq_format.set(cfg.qq.mcFormat or '[MC] {player}：{message}')
        self.var_mc2qq_cooldown.set(str(cfg.qq.mcCooldownSeconds))

        self.var_reconnect_enabled.set(cfg.reconnect.enabled)
        self.var_reconnect_delay.set(str(cfg.reconnect.delaySeconds))
        self.var_reconnect_max.set(str(cfg.reconnect.maxAttempts))
        self.var_kick_lobby.set(cfg.reconnect.kickLobbyEnabled)
        self.var_kick_lobby_delay.set(str(cfg.reconnect.kickLobbyDelaySeconds))

        self.var_bg_image.set(cfg.appearance.backgroundImage or '')
        self.var_bg_darkness.set(cfg.appearance.backgroundDarkness)
        self.var_text_theme.set(_THEME_VALUE_TO_LABEL.get(cfg.appearance.textTheme, _THEME_AUTO))
        try:
            self.bg_darkness_label.config(text=f'{cfg.appearance.backgroundDarkness}%')
        except Exception:  # noqa: BLE001
            pass

        self._on_auth_type_changed()
        self._refresh_auth_label()

    def _refresh_blocked_list(self) -> None:
        self.blocked_list.delete(0, 'end')
        for b in self.controller.ai_config.blockedPlayers:
            mark = '●' if b.get('enabled', True) else '○'
            self.blocked_list.insert('end', f"{mark} {b.get('name', '')}")

    def _refresh_auth_label(self) -> None:
        profile = load_auth_profile()
        if profile:
            site = profile.get('site') or self.var_site.get().strip() or 'LittleSkin'
            self.auth_profile_label.config(
                text=f"已登录: {profile.get('username')}  (UUID {str(profile.get('uuid', ''))[:8]}..., {site})",
                foreground='#7ddb7d')
        else:
            self.auth_profile_label.config(
                text='未找到 auth_profile.json，请点「LittleSkin 登录」或使用离线/正版登录。',
                foreground='#999999')

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_provider_changed(self):
        provider = self.var_provider.get()
        if provider in DEFAULT_BASE_URLS and not self.var_base_url.get().strip():
            self.var_base_url.set(DEFAULT_BASE_URLS[provider])

    def _on_apply_template(self):
        name = self.var_template.get()
        prompt = _build_templates().get(name)
        if prompt:
            self.system_prompt_widget.delete('1.0', 'end')
            self.system_prompt_widget.insert('1.0', prompt)

    def _on_save_template(self):
        name = self.var_new_tpl_name.get().strip()
        if not name:
            messagebox.showwarning('aafm', '请输入模板名称。')
            return
        prompt = self.system_prompt_widget.get('1.0', 'end').strip()
        if not prompt:
            messagebox.showwarning('aafm', '提示词内容为空。')
            return
        PromptTemplateManager.save(name, prompt)
        self.var_new_tpl_name.set('')
        self._refresh_template_combobox()
        self._append_log(f'已保存模板: {name}', 'info')

    def _on_delete_template(self):
        name = self.var_template.get()
        if name in TEMPLATE_BUILTINS:
            messagebox.showwarning('aafm', '内置模板不可删除。')
            return
        PromptTemplateManager.delete(name)
        self._refresh_template_combobox()
        self._append_log(f'已删除模板: {name}', 'info')

    def _refresh_template_combobox(self):
        tpls = list(_build_templates().keys())
        self.template_combo['values'] = tpls
        if self.var_template.get() not in tpls:
            self.var_template.set(tpls[0] if tpls else '')

    def _gather_config(self) -> None:
        cfg = self.controller.config
        ai = self.controller.ai_config

        cfg.server.host = self.var_host.get().strip() or 'localhost'
        try:
            cfg.server.port = int(self.var_port.get().strip())
        except ValueError:
            cfg.server.port = 25565
        cfg.server.version = self.var_version.get().strip()
        cfg.server.brand = self.var_brand.get().strip() or 'aafm-python'
        cfg.server.viewDistance = self.var_view.get().strip() or 'tiny'

        cfg.auth.type = self.var_auth_type.get()
        cfg.auth.username = self.var_username.get().strip() or 'AI_Bot'
        cfg.auth.site = self.var_site.get().strip().rstrip('/')
        cfg.auth.clientId = self.var_client_id.get().strip() or '1153'
        cfg.auth.clientSecret = self.var_client_secret.get().strip()
        try:
            cfg.auth.redirectPort = int(self.var_redirect_port.get().strip())
        except ValueError:
            cfg.auth.redirectPort = DEFAULT_LITTLESKIN_REDIRECT_PORT
        cfg.auth.profilesFolder = self.var_profiles_folder.get().strip()

        ai.provider = self.var_provider.get().strip() or 'openai'
        ai.baseUrl = self.var_base_url.get().strip()
        ai.apiKey = self.var_api_key.get()
        ai.model = self.var_model.get().strip() or 'gpt-4o-mini'
        ai.temperature = self._parse_float(self.var_temperature.get(), 0.7)
        ai.maxTokens = self._parse_int(self.var_max_tokens.get(), 1024)
        ai.timeoutSeconds = self._parse_int(self.var_timeout.get(), 30)
        ai.retryCount = self._parse_int(self.var_retry.get(), 1)

        ai.systemPrompt = self.system_prompt_widget.get('1.0', 'end').strip()
        ai.templateName = self.var_template.get()
        ai.contextEnabled = self.var_context.get()
        ai.contextLength = self._parse_int(self.var_context_len.get(), 20)
        ai.maxReplyMessages = self._parse_int(self.var_max_msgs.get(), 3)
        ai.maxCharsPerMessage = self._parse_int(self.var_max_chars.get(), 100)

        ai.triggerEnabled = self.var_trigger.get()
        ai.triggerRegex = self.var_trigger_regex.get().strip()
        ai.triggerCooldownSeconds = self._parse_int(self.var_cooldown.get(), 10)
        ai.autoReplyEnabled = self.var_auto.get()
        ai.scheduleEnabled = self.var_schedule.get()
        ai.scheduleIntervalSeconds = self._parse_int(self.var_schedule_interval.get(), 30)
        if ai.autoReplyEnabled:
            ai.triggerEnabled = False
            ai.scheduleEnabled = False

        ai.restrictionEnabled = self.var_restrict.get()
        raw_regex = self.var_block_regex.get().strip()
        ai.blockedRegexPatterns = [x.strip() for x in raw_regex.split(';') if x.strip()]

        ai.imageGenerationEnabled = self.var_image.get()
        ai.imageModel = self.var_image_model.get().strip() or 'gpt-image-1'

        cfg.quiz.enabled = self.var_quiz.get()
        cfg.autoeat.enabled = self.var_autoeat.get()

        cfg.roster.enabled = self.var_roster.get()
        cfg.roster.personalityEnabled = self.var_roster_research.get()
        cfg.roster.aliasEnabled = self.var_roster_alias.get()
        try:
            cfg.roster.researchIntervalSeconds = max(
                30, int(self.var_roster_research_interval.get().strip()))
        except (TypeError, ValueError):
            cfg.roster.researchIntervalSeconds = 180

        qq = cfg.qq
        qq.enabled = self.var_qq_enabled.get()
        qq.mode = self.var_qq_mode.get().strip() or 'forward'
        qq.url = self.var_qq_url.get().strip() or 'ws://127.0.0.1:3001'
        qq.accessToken = self.var_qq_token.get().strip()
        qq.listenHost = self.var_qq_listen_host.get().strip() or '0.0.0.0'
        qq.listenPort = self._parse_int(self.var_qq_listen_port.get(), 3002)
        qq.groups = [x for x in re.split(r'[,，;\s]+', self.var_qq_groups.get()) if x]
        qq.format = self.var_qq_format.get().strip() or '&7&o[{group}][{name}]：{message}'
        qq.mcToQqEnabled = self.var_mc2qq_enabled.get()
        qq.mcTrigger = self.var_mc2qq_trigger.get().strip() or '[sentQ]'
        qq.mcTargetGroup = self.var_mc2qq_group.get().strip()
        qq.mcFormat = self.var_mc2qq_format.get().strip() or '[MC] {player}：{message}'
        qq.mcCooldownSeconds = self._parse_int(self.var_mc2qq_cooldown.get(), 3)
        qq.validate()

        rc = cfg.reconnect
        rc.enabled = self.var_reconnect_enabled.get()
        rc.delaySeconds = self._parse_int(self.var_reconnect_delay.get(), 5)
        rc.maxAttempts = self._parse_int(self.var_reconnect_max.get(), 5)
        rc.kickLobbyEnabled = self.var_kick_lobby.get()
        rc.kickLobbyDelaySeconds = self._parse_int(self.var_kick_lobby_delay.get(), 2)
        rc.validate()

        ap = cfg.appearance
        ap.backgroundImage = self.var_bg_image.get().strip()
        ap.backgroundDarkness = int(self.var_bg_darkness.get())
        ap.textTheme = _THEME_LABEL_TO_VALUE.get(self.var_text_theme.get(), 'auto')
        ap.validate()

        cfg.save()
        ai.save()

    def _on_save(self):
        self._gather_config()
        self.controller.sync_roster_runtime()
        self.controller.ai_config = self.controller.ai_config.__class__.load()
        self._append_log('设置已保存。', 'info')
        messagebox.showinfo('aafm AI 玩家', '设置已保存。')

    def _on_connect(self):
        self._relogin_pending = False
        self._gather_config()
        self.controller.sync_roster_runtime()
        self.controller.connect()
        self._set_connected_state(True)
        self._refresh_auth_label()
        self._append_log('正在连接...', 'info')

    def _on_disconnect(self):
        self.controller.disconnect()
        self._set_connected_state(False)
        self._append_log('已断开连接。', 'info')

    def _on_ai_enabled(self):
        self._gather_config()
        self.controller.set_ai_enabled(self.ai_enabled.get())

    def _on_clear_context(self):
        self.controller.clear_ai_context()
        self._append_log('上下文已清空。', 'info')

    def _on_send_chat(self):
        text = self.chat_var.get().strip()
        if not text:
            return
        self.controller.send_chat(text)
        self.chat_var.set('')
        self._append_log(f'» {text}', 'sent')

    def _on_add_blocked(self):
        name = self.var_new_block.get().strip()
        if not name:
            return
        self.controller.add_blocked_player(name)
        self.var_new_block.set('')
        self._refresh_blocked_list()

    def _on_remove_blocked(self):
        sel = self.blocked_list.curselection()
        if not sel:
            return
        idx = sel[0]
        players = self.controller.ai_config.blockedPlayers
        if 0 <= idx < len(players):
            players.pop(idx)
            self.controller.ai_config.save()
            if self.controller.ai_player:
                self.controller.ai_player.config = self.controller.ai_config
                self.controller.ai_player._compile_patterns()
            self._refresh_blocked_list()
            self._append_log('已删除黑名单玩家。', 'info')

    def _on_extra(self, which, enabled):
        self._gather_config()
        self.controller.set_extra_enabled(which, enabled)
        self._append_log(f'{which} {"已启用" if enabled else "已禁用"}。', 'info')

    def _on_qq_enabled(self):
        self._gather_config()
        self.controller.set_qq_enabled(self.var_qq_enabled.get())

    def _on_qq_start(self):
        self._gather_config()
        self.controller.restart_qq()
        self._append_log('正在按当前设置连接 QQ 网关…', 'info')

    def _on_qq_stop(self):
        self.controller.stop_qq()
        self._append_log('已退出 QQ 转述。', 'info')

    def _on_roster_enabled(self):
        enabled = self.var_roster.get()
        self.controller.roster_set_enabled(enabled)
        self._append_log(f'玩家身份问答{"已启用" if enabled else "已禁用"}。', 'info')

    def _on_roster_research(self):
        self._gather_config()
        self.controller.sync_roster_runtime()
        self._append_log(f'AI 性格研究{"已启用" if self.var_roster_research.get() else "已禁用"}。', 'info')

    def _on_roster_alias(self):
        self._gather_config()
        self.controller.sync_roster_runtime()
        self._append_log(f'AI 别名分析{"已启用" if self.var_roster_alias.get() else "已禁用"}。', 'info')

    def _on_roster_template(self):
        path = filedialog.asksaveasfilename(
            title='下载资料表模板',
            defaultextension='.xlsx',
            initialfile='玩家资料模板.xlsx',
            filetypes=[('Excel 工作簿', '*.xlsx'), ('所有文件', '*.*')])
        if not path:
            return
        ok, msg = self.controller.export_roster_template(path)
        if not ok:
            messagebox.showerror('下载模板', msg)
        else:
            messagebox.showinfo('下载模板', f'模板已保存到：\n{path}')
            self._append_log(f'模板已导出: {path}', 'info')

    def _on_roster_import(self):
        path = filedialog.askopenfilename(
            title='导入玩家资料表',
            filetypes=[('Excel / CSV', '*.xlsx *.csv'),
                       ('Excel', '*.xlsx'), ('CSV', '*.csv'), ('所有文件', '*.*')])
        if not path:
            return
        ok, msg = self.controller.roster_add_file(path)
        self._refresh_roster_list()
        if not ok:
            messagebox.showerror('玩家资料库', msg)
        else:
            self._append_log(f'玩家资料库：{msg}', 'info')

    def _on_roster_delete(self):
        sel = self.roster_list.curselection()
        if not sel:
            return
        idx = sel[0]
        paths = list(self.controller.config.roster.files or [])
        if 0 <= idx < len(paths):
            path = paths[idx]
            if self.controller.roster_remove_file(path):
                self._append_log(f'已删除玩家资料表: {path}', 'info')
                self._refresh_roster_list()

    def _refresh_roster_list(self):
        self.roster_list.delete(0, 'end')
        for i, p in enumerate(self.controller.config.roster.files or [], 1):
            count = self.controller.roster.count_for(p)
            self.roster_list.insert('end', f'{i}. {os.path.basename(p)}  ({count} 条)  {p}')
        self.roster_status.config(
            text=f'共 {len(self.controller.config.roster.files or [])} 个文件，'
                 f'{self.controller.roster.total} 条记录。')

    def _spawn_console(self, args: list):
        try:
            if os.name == 'nt':
                kwargs = {'creationflags': subprocess.CREATE_NEW_CONSOLE}
            else:
                kwargs = {}
            subprocess.Popen(args, cwd=BASE_DIR, **kwargs)
            return True
        except Exception as e:  # noqa: BLE001
            messagebox.showerror('aafm AI 玩家', f'无法启动: {e}')
            return False

    def _on_littleskin_login(self):
        self._gather_config()
        if not self._spawn_console(['node', '3rdparty_auth.js']):
            return
        self._append_log('已启动 LittleSkin 登录窗口。请在弹窗里打开授权链接并选择角色，'
                         '完成后回到这里点「连接」即可。', 'info')

    def _profile_mtime(self):
        try:
            return os.path.getmtime(AUTH_PROFILE_FILE)
        except OSError:
            return None

    def _start_relogin(self):
        """Auto re-login after a LittleSkin session expires: launch the helper
        (which opens the browser) and watch for a fresh auth_profile.json."""
        if self._relogin_pending:
            return
        if self.var_auth_type.get() != 'littleskin':
            return
        self._gather_config()
        if not self._spawn_console(['node', '3rdparty_auth.js', 'relogin']):
            return
        self._relogin_pending = True
        self._relogin_baseline = self._profile_mtime()
        self._relogin_stable_since = 0.0
        self.status_label.config(text='登录失效，正在浏览器重新登录…', foreground='#ffb74d')
        self._append_log('LittleSkin 登录已失效。已自动打开浏览器进行重新登录，'
                         '完成后将自动重新连接。', 'warn')

    def _check_relogin_progress(self):
        if not self._relogin_pending:
            return
        if self.controller.connected or self.controller.connecting:
            if self.controller.connected:
                self._relogin_pending = False
            return
        now = time.monotonic()
        mt = self._profile_mtime()
        if mt is not None and mt != self._relogin_baseline:
            if self._relogin_stable_since == 0.0:
                self._relogin_stable_since = now
            elif now - self._relogin_stable_since >= 2.5:
                self._relogin_pending = False
                self._append_log('已获取新的 LittleSkin 登录信息，正在自动重新连接…', 'info')
                self._on_connect()
        elif self._relogin_stable_since != 0.0:
            self._relogin_stable_since = 0.0

    def _on_msa_login(self):
        self._gather_config()
        at = self.var_auth_type.get()
        username = self.var_username.get().strip() or 'MSA_Account'
        if at != 'microsoft':
            self.var_auth_type.set('microsoft')
            self._on_auth_type_changed()
            self._gather_config()
        args = ['node', 'msa_auth.js', username]
        folder = self.var_profiles_folder.get().strip()
        if folder:
            args.append(folder)
        if not self._spawn_console(args):
            return
        self._append_log('已启动正版（微软）登录窗口。请按提示在浏览器完成授权，'
                         '完成后回到这里点「连接」即可。', 'info')

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _poll_ui(self):
        self._tick += 1
        for ev in self.controller.drain_ui():
            self._handle_ui_event(ev)
        if self._relogin_pending:
            self._check_relogin_progress()
        # Periodically refresh the LittleSkin login status (file may appear any time)
        if self._tick % 20 == 0:
            self._refresh_auth_label()
        self.root.after(100, self._poll_ui)

    def _handle_ui_event(self, ev):
        kind = ev.get('kind')
        if kind == 'status':
            self.status_label.config(text=ev.get('text', ''), foreground='#4fc3f7')
        elif kind == 'log':
            self._append_log(ev.get('message', ''), self._log_tag(ev.get('level', 'info')))
        elif kind == 'chat':
            prefix = '[私聊] ' if ev.get('private') else ''
            tag = 'private' if ev.get('private') else 'chat'
            self._append_log(f'{prefix}{ev.get("player", "?")}: {ev.get("content", "")}', tag)
        elif kind == 'actionBar':
            self._append_log(f'[ActionBar] {ev.get("text", "")}', 'info')
        elif kind == 'health':
            self._append_log(f'生命值 {ev.get("health")}  饥饿 {ev.get("food")}', 'info')
        elif kind == 'spawn':
            self._set_connected_state(True)
            username = ev.get('username') or ''
            self.status_label.config(text=f'已连接（{username}）', foreground='#4fc3f7')
        elif kind == 'disconnected':
            self._set_connected_state(False)
            self.status_label.config(text='已断开', foreground='#999999')
        elif kind == 'reconnect':
            state = ev.get('state')
            if state == 'scheduled':
                self._set_connected_state(False)
                self.status_label.config(
                    text=f"掉线，{ev.get('delay', 0)}s 后自动重连"
                         f"（{ev.get('attempt')}/{ev.get('max')}）",
                    foreground='#ffb74d')
            elif state == 'connecting':
                self._set_connected_state(True)
                self.status_label.config(
                    text=f"正在自动重连（第 {ev.get('attempt')} 次）…",
                    foreground='#4fc3f7')
            elif state == 'giveup':
                self._set_connected_state(False)
                self.status_label.config(text='自动重连已放弃', foreground='#ff6b6b')
        elif kind == 'qqStatus':
            state = ev.get('state')
            if state == 'connected':
                who = ev.get('selfId') or ''
                text = f'QQ 已连接 {who}'.strip()
                color = '#7ddb7d'
            elif state == 'connecting':
                text, color = 'QQ 连接中…', '#ffb74d'
            else:
                text, color = 'QQ 未连接', '#999999'
            self.qq_status_label.config(text=text, foreground=color)
        elif kind == 'reloginNeeded':
            self._append_log(f'LittleSkin 登录失效: {ev.get("message", "")}', 'error')
            self._start_relogin()

    @staticmethod
    def _log_tag(level):
        return {'error': 'error', 'ai': 'ai', 'quiz': 'quiz', 'sent': 'sent',
                'warn': 'warn', 'msa': 'msa', 'qq': 'qq'}.get(level, 'info')

    @staticmethod
    def _parse_int(s, default):
        try:
            return int(str(s).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_float(s, default):
        try:
            return float(str(s).strip())
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------
    def _on_close(self):
        try:
            self._gather_config()
            self.controller.shutdown()
        finally:
            self.root.destroy()


def _enable_high_dpi() -> None:
    """Enable per-monitor high-DPI awareness so widgets render crisply."""
    if os.name != 'nt':
        return
    try:
        import ctypes
    except Exception:  # noqa: BLE001
        return
    # Try the best option first (PER_MONITOR_AWARE_V2 = -4), then fall back.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    _enable_high_dpi()
    root = tk.Tk()
    # Scale widget fonts to the actual display DPI (crisper text at 125%/150%).
    try:
        dpi = root.winfo_fpixels('1i')
        if dpi and dpi > 0:
            root.tk.call('tk', 'scaling', dpi / 72.0)
    except Exception:  # noqa: BLE001
        pass
    AiPlayerGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
