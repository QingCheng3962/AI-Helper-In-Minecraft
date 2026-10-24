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
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .config import (AiPlayerConfig, AppConfig, AUTH_PROFILE_FILE,
                     DEFAULT_BASE_URLS, DEFAULT_LITTLESKIN_REDIRECT_PORT,
                     TEMPLATE_ASSISTANT, TEMPLATE_BUDDY, PromptTemplateManager,
                     load_auth_profile)
from .controller import Controller, BASE_DIR
from . import docbook

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
        self.log_text.tag_configure('song', foreground='#4dd0e1')

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
        self._build_docbook_tab()
        self._build_song_tab()
        self._build_misc_tab()
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

        self._build_logforward_frame(frm)

    def _build_logforward_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent, text='日志转发到 MC（可选项）', padding=8)
        frm.pack(fill='x', pady=(8, 0))

        self.var_logfwd_enabled = tk.BooleanVar(value=False)
        self.var_logfwd_format = tk.StringVar()
        self.var_logfwd_error = tk.BooleanVar(value=True)
        self.var_logfwd_warn = tk.BooleanVar(value=False)

        ttk.Checkbutton(frm, text='启用：把日志转发到 MC 公共聊天',
                        variable=self.var_logfwd_enabled).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=4, pady=3)

        ttk.Label(frm, text='转发级别').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        ttk.Checkbutton(frm, text='错误', variable=self.var_logfwd_error).grid(
            row=1, column=1, sticky='w', padx=4)
        ttk.Checkbutton(frm, text='警告', variable=self.var_logfwd_warn).grid(
            row=1, column=2, sticky='w', padx=4)

        ttk.Label(frm, text='播报格式').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_logfwd_format, width=46).grid(
            row=2, column=1, columnspan=3, sticky='we', padx=4, pady=3)
        ttk.Label(frm, text='占位符 {log}=日志内容；默认 &7Log -> &c{log}',
                  foreground='#888888').grid(
            row=3, column=0, columnspan=4, sticky='w', padx=4)
        frm.columnconfigure(1, weight=1)

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

        self._build_search_frame(frm)

    def _build_search_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent, text='联网搜索（AI 需要时自动搜并参考）', padding=8)
        frm.pack(fill='x', pady=(8, 0))

        self.var_search_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text='启用联网搜索', variable=self.var_search_enabled).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=4, pady=3)

        self.var_search_provider = tk.StringVar(value='bing')
        ttk.Label(frm, text='后端').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        ttk.Combobox(frm, textvariable=self.var_search_provider, state='readonly',
                     values=['bing', 'duckduckgo', 'custom'], width=12).grid(
            row=1, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='bing/duckduckgo=免Key；custom=填下面的 API（国内推荐 bing）',
                  foreground='#888888').grid(row=1, column=2, columnspan=2, sticky='w', padx=4)

        self.var_search_url = tk.StringVar()
        self.var_search_key = tk.StringVar()
        self._entry(frm, 2, 'API 地址', self.var_search_url, hint='custom 时才需要')
        self._entry(frm, 3, 'API 密钥', self.var_search_key, show='*')

        self.var_search_n = tk.StringVar()
        self._entry(frm, 4, '结果条数', self.var_search_n)

        self.var_search_regex = tk.StringVar()
        ttk.Label(frm, text='触发正则').grid(row=5, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_search_regex, width=64).grid(
            row=5, column=1, columnspan=3, sticky='we', padx=4, pady=3)
        ttk.Label(frm, text='命中该正则的消息会先联网搜索（留空=默认关键词）',
                  foreground='#888888').grid(row=6, column=0, columnspan=4, sticky='w', padx=4)
        frm.columnconfigure(1, weight=1)

    # ------------------------------------------------------------------
    # Tab: 文档转书
    # ------------------------------------------------------------------
    def _build_docbook_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' 文档转书 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)
        frm = ttk.LabelFrame(
            sc.inner,
            text='上传 .docx / .doc / .txt，转换为游戏内的书与笔（书与笔）',
            padding=8)
        frm.pack(fill='x', pady=(8, 0))

        self.var_book_file = tk.StringVar()
        self.var_book_pagelines = tk.StringVar(value='14')
        self.var_book_perbook = tk.StringVar(value='100')
        self.var_book_start = tk.StringVar()
        self.var_book_endtext = tk.StringVar()
        self.var_book_enddrop = tk.BooleanVar(value=False)
        self.var_book_autoget = tk.BooleanVar(value=True)
        self.var_book_sign = tk.BooleanVar(value=False)
        self.var_book_title = tk.StringVar()
        self.var_book_startbook = tk.StringVar(value='1')

        ttk.Label(frm, text='文档文件').grid(row=0, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_book_file, width=56).grid(
            row=0, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Button(frm, text='选择文件…', command=self._on_choose_book_file).grid(
            row=0, column=3, sticky='w', padx=4, pady=3)

        ttk.Label(frm, text='每页最大行数（Java 上限 14）').grid(
            row=1, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_book_pagelines, width=8).grid(
            row=1, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='每本书最大页数').grid(row=1, column=2, sticky='e', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_book_perbook, width=8).grid(
            row=1, column=3, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='（每页 1024 字为游戏硬上限，自动保证；分页只按行数）',
                  foreground='#888888').grid(row=1, column=4, columnspan=2, sticky='w', padx=4, pady=3)

        ttk.Label(frm, text='开始提示（进服发言，可留空）').grid(
            row=2, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_book_start).grid(
            row=2, column=1, columnspan=5, sticky='we', padx=4, pady=3)

        ttk.Checkbutton(frm, text='写完后把书丢出（多本书时，非最后一本会自动丢出）',
                        variable=self.var_book_enddrop).grid(
            row=3, column=0, columnspan=6, sticky='w', padx=4, pady=3)

        ttk.Label(frm, text='结束提示（写完发言，可留空）').grid(
            row=4, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_book_endtext).grid(
            row=4, column=1, columnspan=5, sticky='we', padx=4, pady=3)

        ttk.Checkbutton(frm, text='创造模式下缺少书与笔时自动获取',
                        variable=self.var_book_autoget).grid(
            row=5, column=0, columnspan=6, sticky='w', padx=4, pady=3)

        signrow = ttk.Frame(frm)
        signrow.grid(row=6, column=0, columnspan=6, sticky='we', padx=4, pady=3)
        ttk.Checkbutton(signrow, text='编辑为成书（署名）',
                        variable=self.var_book_sign).pack(side='left')
        ttk.Label(signrow, text='　书名模板（{number}=第几本，只含数字）：').pack(side='left')
        ttk.Entry(signrow, textvariable=self.var_book_title, width=40).pack(
            side='left', fill='x', expand=True)
        ttk.Label(signrow, text='　例：资本论 P{number}',
                  foreground='#888888').pack(side='left')

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=6, sticky='w', pady=(6, 0))
        ttk.Button(btns, text='预览分页', command=self._on_preview_book).pack(
            side='left', padx=(0, 6))
        ttk.Button(btns, text='写入书与笔', command=self._on_write_book).pack(
            side='left', padx=(0, 6))
        ttk.Button(btns, text='清空书与笔内容', command=self._on_clear_book).pack(side='left')

        ctl = ttk.Frame(frm)
        ctl.grid(row=8, column=0, columnspan=6, sticky='w', pady=(4, 0))
        ttk.Button(ctl, text='暂停', command=lambda: self._on_book_control('pause')).pack(
            side='left', padx=(0, 6))
        ttk.Button(ctl, text='停止', command=lambda: self._on_book_control('stop')).pack(
            side='left', padx=(0, 6))
        ttk.Button(ctl, text='重新写入', command=self._on_rewrite_book).pack(
            side='left', padx=(0, 6))
        ttk.Button(ctl, text='从未写入的地方开始写入',
                   command=self._on_continue_book).pack(side='left', padx=(0, 12))
        ttk.Label(ctl, text='从第').pack(side='left')
        ttk.Entry(ctl, textvariable=self.var_book_startbook, width=6).pack(side='left', padx=2)
        ttk.Label(ctl, text='册开始').pack(side='left', padx=(0, 4))
        ttk.Button(ctl, text='从该册开始写入',
                   command=self._on_start_from_book).pack(side='left')

        self.book_progress_label = ttk.Label(
            frm, text='进度：未开始', foreground='#888888')
        self.book_progress_label.grid(row=9, column=0, columnspan=6, sticky='w', padx=4, pady=(4, 0))

        ttk.Label(sc.inner, text='分页预览（已按书页宽度自动换行、自动翻页）').pack(
            anchor='w', padx=4, pady=(10, 2))
        self.book_preview = tk.Text(sc.inner, height=14, wrap='word',
                                    font=('Consolas', 9), state='disabled')
        self.book_preview.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(3, weight=0)

    # ------------------------------------------------------------------
    # Tab: 玩家资料库
    # ------------------------------------------------------------------
    # Tab: 点歌 (客户端 Mod 方案)
    # ------------------------------------------------------------------
    def _build_song_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' 点歌 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)

        basic = ttk.LabelFrame(sc.inner, text='点歌（客户端 Mod：玩家发 [点歌]歌名 即本地播放）', padding=8)
        basic.pack(fill='x', pady=(8, 0))

        self.var_song_trigger = tk.StringVar(value='[点歌]')
        self.var_song_stop_word = tk.StringVar(value='停止')
        self.var_song_query = tk.StringVar()
        self.var_song_reply_enabled = tk.BooleanVar(value=True)
        self.var_song_post_play_link = tk.BooleanVar(value=True)
        self.var_song_instant_play = tk.BooleanVar(value=True)
        self.var_song_private_play = tk.BooleanVar(value=True)
        self.var_song_mod_url = tk.StringVar()
        self.var_song_reply_text = tk.StringVar()
        self.var_song_reply_cooldown = tk.StringVar()

        row = ttk.Frame(basic)
        row.pack(fill='x', pady=3)
        ttk.Label(row, text='触发词').pack(side='left')
        ttk.Entry(row, textvariable=self.var_song_trigger, width=10).pack(
            side='left', padx=(4, 10))
        ttk.Label(row, text='停止词').pack(side='left')
        ttk.Entry(row, textvariable=self.var_song_stop_word, width=6).pack(
            side='left', padx=(4, 10))
        ttk.Label(row, text='让机器人点歌').pack(side='left')
        song_entry = ttk.Entry(row, textvariable=self.var_song_query)
        song_entry.pack(side='left', fill='x', expand=True, padx=(4, 6))
        song_entry.bind('<Return>', lambda _e: self._on_song_play())
        ttk.Button(row, text='点歌', command=self._on_song_play).pack(side='left')
        ttk.Label(basic,
                  text='点「点歌」= 机器人往公屏发「触发词+歌名」，于是所有装了点歌 Mod 的玩家都会听到。',
                  foreground='#888888', wraplength=760, justify='left').pack(anchor='w', padx=4)

        reply = ttk.LabelFrame(sc.inner, text='机器人回复 Mod 下载链接（提醒没装 Mod、听不到的人）', padding=8)
        reply.pack(fill='x', pady=(8, 0))
        ttk.Checkbutton(reply, text='有人发 [点歌] 时，机器人在公屏回一条下载链接',
                        variable=self.var_song_reply_enabled).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=4, pady=3)
        self._entry(reply, 1, 'Mod 下载链接', self.var_song_mod_url)
        self._entry(reply, 2, '回复文案（可留空，{url} 会替换成下载链接）', self.var_song_reply_text)
        self._entry(reply, 3, '回复冷却（秒）', self.var_song_reply_cooldown)
        reply.columnconfigure(1, weight=1)

        # --- 机器人代播（会员 Cookie） ---
        play = ttk.LabelFrame(
            sc.inner, text='机器人代播：用会员账号解析歌曲并发可播放直链（玩家无需各自登录）', padding=8)
        play.pack(fill='x', pady=(8, 0))
        ttk.Checkbutton(play, text='有人发 [点歌] 时，机器人解析并发出「播放文案 | 直链」（客户端 Mod 播放）',
                        variable=self.var_song_post_play_link).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=4, pady=3)
        ttk.Checkbutton(play, text='新点歌立刻插播（不等当前这首放完，响应最快；同一首歌再点会直接重播、不重新搜索）',
                        variable=self.var_song_instant_play).grid(
            row=7, column=0, columnspan=3, sticky='w', padx=4, pady=(4, 2))
        ttk.Checkbutton(play, text='只放给点歌的人（谁点谁听）：播放数据用 /msg 私聊发给点歌的人，'
                                   '公屏不出现点歌消息，别的玩家不会跟着响',
                        variable=self.var_song_private_play).grid(
            row=8, column=0, columnspan=3, sticky='w', padx=4, pady=(0, 2))
        ttk.Label(play, text='播放文案').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        self.var_song_play_text = tk.StringVar(value='{player} 点了一首 {music}')
        ttk.Entry(play, textvariable=self.var_song_play_text, width=48).grid(
            row=1, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Label(play, text='可用 {player} 点歌的人、{music} 歌名 - 歌手、{title} 歌名、{artist} 歌手；'
                             '后面会自动追加「 | 直链」', foreground='#888888').grid(
            row=2, column=0, columnspan=3, sticky='w', padx=4)
        ttk.Label(play, text='网易云登录（会员）').grid(row=3, column=0, sticky='w', padx=4, pady=3)
        self.var_song_netease_cookie = tk.StringVar()
        ttk.Entry(play, textvariable=self.var_song_netease_cookie, width=40).grid(
            row=3, column=1, sticky='we', padx=4, pady=3)
        netease_btns = ttk.Frame(play)
        netease_btns.grid(row=3, column=2, sticky='w', padx=4)
        ttk.Button(netease_btns, text='扫码登录', command=self._on_netease_login).pack(side='left')
        ttk.Button(netease_btns, text='验证登录', command=self._on_netease_verify).pack(
            side='left', padx=(4, 0))

        qrrow = ttk.Frame(play)
        qrrow.grid(row=4, column=0, columnspan=3, sticky='w', padx=4, pady=4)
        self._netease_qr_label = ttk.Label(qrrow)
        self._netease_qr_label.pack(side='left')
        self._netease_qr_photo = None
        self._netease_url = ''
        qrbtns = ttk.Frame(qrrow)
        qrbtns.pack(side='left', anchor='n', padx=8)
        ttk.Button(qrbtns, text='在浏览器打开登录链接',
                   command=self._on_netease_open).pack(anchor='w', pady=2)
        ttk.Button(qrbtns, text='复制登录链接',
                   command=self._on_netease_copy).pack(anchor='w', pady=2)
        self._netease_status = tk.StringVar(value='未登录（VIP 歌只能放 45 秒试听）')
        ttk.Label(play, textvariable=self._netease_status, foreground='#888888',
                  wraplength=780, justify='left').grid(
            row=5, column=0, columnspan=3, sticky='w', padx=4, pady=(0, 3))
        ttk.Label(play, foreground='#888888', wraplength=780, justify='left',
                  text='点「扫码登录」→ 上面直接出现二维码（也可用浏览器打开链接）→ '
                       '用【网易云音乐 App】扫一扫并在手机上点确认；成功后 Cookie 会自动填入并保存。\n'
                       '自己粘贴 Cookie 的话：把浏览器的 music.163.com Cookie（含 MUSIC_U=…）粘到上面，'
                       '点「验证登录」——会显示账号昵称，并实测一首会员歌能不能拿到完整直链。').grid(
            row=6, column=0, columnspan=3, sticky='w', padx=4)
        play.columnconfigure(1, weight=1)

        info = ttk.LabelFrame(sc.inner, text='说明 / 使用方法（服务端零改动）', padding=8)
        info.pack(fill='x', pady=(8, 0))
        ttk.Label(info, justify='left', wraplength=780, foreground='#666666',
                  text=(
                      '原理：机器人在「点歌」页登录网易云会员后，看到聊天里的 [点歌]歌名 就用会员账号'
                      '解析出可播放直链并发到公屏，各客户端 Mod 直接播放 —— 相当于“全服点歌”，'
                      '而**服务端不需要装任何插件**。\n'
                      '  1) 把点歌 Mod（songmod，Fabric 1.21.1~26.2）放进玩家的 mods/ 文件夹；\n'
                      '  2) 玩家在聊天里发 [点歌]告白气球，或直接用 Mod 里的「点歌台」界面；\n'
                      '     （玩家发 [点歌]help 机器人会私聊他全部用法：歌名/停止/台/help/登录）\n'
                      '  3) 没装 Mod 的人听不到 —— 可用上面的「回复 Mod 下载链接」提醒他。\n\n'
                      'Mod 的触发词 / 音量 / 界面按键在客户端 config/songmod.json 里改。\n'
                      '源码：项目目录 mc-song-mod/。'
                  )).pack(anchor='w', padx=4, pady=4)

    def _on_song_play(self):
        self._gather_config()
        trigger = (self.var_song_trigger.get() or '[点歌]').strip() or '[点歌]'
        query = self.var_song_query.get().strip()
        if not query:
            self._append_log('请先输入歌名。', 'warn')
            return
        self.controller.send_chat(trigger + query)
        self._append_log(f'点歌：{trigger}{query}', 'info')

    # --- 网易云扫码登录（机器人代播用） ---
    def _ui(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:  # noqa: BLE001
            pass

    def _on_netease_login(self):
        if getattr(self, '_netease_login_running', False):
            self._append_log('网易云登录已在进行中…', 'warn')
            return
        self._netease_login_running = True
        self._netease_status.set('正在获取二维码…')
        self._append_log('正在获取网易云登录二维码…', 'info')
        threading.Thread(target=self._netease_login_worker, daemon=True).start()

    def _show_netease_qr(self, url):
        """把登录链接画成二维码显示在界面里。"""
        self._netease_url = url
        try:
            import qrcode
            from PIL import ImageTk
            img = qrcode.make(url).resize((200, 200))
            photo = ImageTk.PhotoImage(img)
            self._netease_qr_photo = photo   # 必须保留引用
            self._netease_qr_label.configure(image=photo, text='')
        except Exception as e:  # noqa: BLE001
            self._netease_qr_photo = None
            self._netease_qr_label.configure(
                image='', text='（生成二维码失败：' + str(e) + '，请用下面按钮打开链接）')

    def _on_netease_open(self):
        if not self._netease_url:
            self._append_log('还没有登录链接，先点「扫码登录」。', 'warn')
            return
        try:
            import webbrowser
            webbrowser.open(self._netease_url)
        except Exception as e:  # noqa: BLE001
            self._append_log('打开浏览器失败：' + str(e), 'warn')

    def _on_netease_copy(self):
        if not self._netease_url:
            self._append_log('还没有登录链接，先点「扫码登录」。', 'warn')
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._netease_url)
            self._append_log('登录链接已复制到剪贴板。', 'info')
        except Exception as e:  # noqa: BLE001
            self._append_log('复制失败：' + str(e), 'warn')

    def _netease_login_worker(self):
        qr = None
        try:
            from . import netease
            qr = netease.QrLogin()
            unikey = qr.start()
            if not unikey:
                self._ui(lambda: self._netease_status.set('获取二维码失败（网易云接口无响应）'))
                return
            url = qr.login_url()
            self._ui(lambda: self._show_netease_qr(url))
            self._ui(lambda: self._netease_status.set('请用【网易云音乐 App】扫描上方二维码，并在手机上点确认'))
            self._ui(lambda: self._append_log('网易云扫码登录：二维码已显示在界面里（也点「在浏览器打开登录链接」）。', 'info'))
            scanned = False
            for _ in range(120):
                time.sleep(2)
                code, cookie = qr.poll()
                if code == 800:
                    self._ui(lambda: self._netease_status.set('二维码已过期，请重新点「扫码登录」'))
                    return
                if code == 802:
                    if not scanned:
                        scanned = True
                        self._ui(lambda: self._append_log('已扫码，请在手机上点「确认登录」。', 'info'))
                    self._ui(lambda: self._netease_status.set('已扫码，请在手机上点「确认登录」…'))
                elif code == 803:
                    if not cookie:
                        self._ui(lambda: self._netease_status.set(
                            '登录成功但没拿到 Cookie（请把界面上的状态发给开发者）'))
                        self._ui(lambda: self._append_log('网易云 803 但没有 MUSIC_U，请反馈。', 'warn'))
                        return
                    self._ui(lambda: self._apply_netease_cookie(cookie))
                    return
            self._ui(lambda: self._netease_status.set('登录超时，请重试'))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            self._ui(lambda: self._netease_status.set('登录出错：' + msg))
        finally:
            if qr is not None:
                qr.close()
            self._netease_login_running = False

    # ------------------------------------------------------------------
    # Tab: 杂项（视频推荐 / 今日幸运值 / 群发私聊）
    # ------------------------------------------------------------------
    _VIDEO_SOURCE_LABELS = ['番剧追番榜（追番）', 'B 站首页推荐（主页）', 'B 站首页热门',
                            'B 站全站排行', '我的收藏夹（要登录 B 站）']
    _VIDEO_SOURCE_KEYS = ['anime', 'home', 'hot', 'rank', 'fav']

    def _build_misc_tab(self) -> None:
        host = ttk.Frame(self.notebook, padding=(8, 8))
        self.notebook.add(host, text=' 杂项 ')
        sc = ScrollFrame(host)
        sc.pack(fill='both', expand=True)

        # ---------------- 视频推荐 ----------------
        video = ttk.LabelFrame(
            sc.inner, text='视频推荐（玩家发 [触发词]视频推荐 → 公屏推一批 B 站视频）', padding=8)
        video.pack(fill='x', pady=(8, 0))
        self.var_misc_video_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(video, text='开启「视频推荐」',
                        variable=self.var_misc_video_enabled).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=4, pady=3)

        ttk.Label(video, text='推荐来源').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_video_source = tk.StringVar(value=self._VIDEO_SOURCE_LABELS[0])
        ttk.Combobox(video, textvariable=self.var_misc_video_source, state='readonly',
                     values=self._VIDEO_SOURCE_LABELS, width=24).grid(
            row=1, column=1, sticky='w', padx=4, pady=3)

        ttk.Label(video, text='每次推几条').grid(row=1, column=2, sticky='e', padx=(20, 4))
        self.var_misc_video_count = tk.StringVar(value='5')
        ttk.Entry(video, textvariable=self.var_misc_video_count, width=6).grid(
            row=1, column=3, sticky='w', padx=4, pady=3)

        ttk.Label(video, text='全服冷却（秒）').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_video_cooldown = tk.StringVar(value='30')
        ttk.Entry(video, textvariable=self.var_misc_video_cooldown, width=8).grid(
            row=2, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(video, text='谁的请求都算；冷却过后再发 = 换一批新的（越点越多）',
                  foreground='#888888').grid(row=2, column=2, columnspan=2, sticky='w', padx=4)

        ttk.Label(video, text='抬头文案').grid(row=3, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_video_title = tk.StringVar()
        ttk.Entry(video, textvariable=self.var_misc_video_title, width=62).grid(
            row=3, column=1, columnspan=3, sticky='we', padx=4, pady=3)
        ttk.Label(video, text='可用 {player} 谁点的、{count} 条数、{source} 来源名',
                  foreground='#888888').grid(row=4, column=1, columnspan=3, sticky='w', padx=4)

        ttk.Label(video, text='每条文案').grid(row=5, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_video_item = tk.StringVar()
        ttk.Entry(video, textvariable=self.var_misc_video_item, width=62).grid(
            row=5, column=1, columnspan=3, sticky='we', padx=4, pady=3)
        ttk.Label(video, text='可用 {index} 序号、{title} 标题、{owner} 备注（UP 主 / 更新进度）、{link} 链接',
                  foreground='#888888').grid(row=6, column=1, columnspan=3, sticky='w', padx=4)

        self.var_misc_video_cool = tk.StringVar()
        self.var_misc_video_fail = tk.StringVar()
        self._entry(video, 7, '冷却中提示', self.var_misc_video_cool,
                    hint='可用 {wait} 还要等几秒；{player} 谁点的')
        self._entry(video, 8, '抓取失败提示', self.var_misc_video_fail)

        # ---- B 站账号（来源选「我的收藏夹」才需要）----
        bili = ttk.LabelFrame(video, text='B 站账号（只有来源选「我的收藏夹」才需要）',
                              padding=(6, 6))
        bili.grid(row=9, column=0, columnspan=4, sticky='we', padx=4, pady=(8, 4))
        self.var_misc_bili_cookie = tk.StringVar()
        self.var_misc_bili_fav = tk.StringVar()
        self.var_misc_bili_status = tk.StringVar(value='（还没读取）')
        self._bili_fav_map = {}
        self._bili_fav_cur = {'media_id': 0, 'name': ''}

        ttk.Label(bili, text='SESSDATA').grid(row=0, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(bili, textvariable=self.var_misc_bili_cookie, width=44,
                  show='*').grid(row=0, column=1, sticky='we', padx=4, pady=3)
        ttk.Button(bili, text='读取收藏夹', command=self._on_bili_folders).grid(
            row=0, column=2, sticky='w', padx=4, pady=3)
        ttk.Label(bili, textvariable=self.var_misc_bili_status,
                  foreground='#888888').grid(row=0, column=3, sticky='w', padx=4)

        ttk.Label(bili, text='收藏夹').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        self.cmb_bili_fav = ttk.Combobox(bili, textvariable=self.var_misc_bili_fav,
                                         width=36, values=[])
        self.cmb_bili_fav.grid(row=1, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(bili, text='点上面按钮读出来再选；也可以直接填收藏夹 id（纯数字）',
                  foreground='#888888').grid(row=1, column=2, columnspan=2, sticky='w', padx=4)
        ttk.Label(bili, text='推荐随机翻页抽，所以每次推的都不一样（同一个夹子也不会老推那几条）',
                  foreground='#888888').grid(row=2, column=1, columnspan=3, sticky='w', padx=4)

        ttk.Label(bili, text='拿法：浏览器登录 bilibili.com → F12 → Application → Cookies → 复制 '
                             'SESSDATA 的值（整条 Cookie 粘进来也行）；再点「读取收藏夹」选一个夹子',
                  foreground='#888888', wraplength=760, justify='left').grid(
            row=3, column=1, columnspan=3, sticky='w', padx=4, pady=(2, 0))
        bili.columnconfigure(1, weight=1)
        video.columnconfigure(1, weight=1)

        # ---------------- 今日幸运值 ----------------
        luck = ttk.LabelFrame(
            sc.inner, text='今日幸运值（玩家发 [触发词]今日幸运值 → 公屏回当天的运气值）', padding=8)
        luck.pack(fill='x', pady=(8, 0))
        self.var_misc_luck_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(luck, text='开启「今日幸运值」', variable=self.var_misc_luck_enabled).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=4, pady=3)
        ttk.Label(luck, text='最大值').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_luck_max = tk.StringVar(value='100')
        ttk.Entry(luck, textvariable=self.var_misc_luck_max, width=8).grid(
            row=1, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(luck, text='当天第一次查询时随机定下来，存进 daily.db，重启程序也不会变；'
                             '跨天自动备份到 db_backup/ 后重置',
                  foreground='#888888').grid(row=1, column=2, sticky='w', padx=4)
        ttk.Label(luck, text='回复文案').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_luck_text = tk.StringVar()
        ttk.Entry(luck, textvariable=self.var_misc_luck_text, width=62).grid(
            row=2, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Label(luck, text='可用 {player} 玩家、{value} 幸运值、{max} 最大值、'
                             '{comment} 评语（按分数自动给）、{date} 日期',
                  foreground='#888888').grid(row=3, column=1, columnspan=2, sticky='w', padx=4)
        luck.columnconfigure(1, weight=1)

        # ---------------- 每日签到 ----------------
        ck = ttk.LabelFrame(
            sc.inner, text='每日签到（玩家发 [提示词]签到 → 每天一次，发奖励）', padding=8)
        ck.pack(fill='x', pady=(8, 0))
        self.var_misc_checkin_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(ck, text='开启「每日签到」', variable=self.var_misc_checkin_enabled).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=4, pady=3)

        ttk.Label(ck, text='金币').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_checkin_money_min = tk.StringVar(value='100')
        ttk.Entry(ck, textvariable=self.var_misc_checkin_money_min, width=8).grid(
            row=1, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(ck, text='～').grid(row=1, column=2, sticky='w')
        self.var_misc_checkin_money_max = tk.StringVar(value='300')
        ttk.Entry(ck, textvariable=self.var_misc_checkin_money_max, width=8).grid(
            row=1, column=3, sticky='w', padx=4, pady=3)
        ttk.Label(ck, text='最少～最多，随机给（两个填一样就是固定值）',
                  foreground='#888888').grid(row=1, column=4, sticky='w', padx=4)

        ttk.Label(ck, text='连签加成').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_checkin_streak_bonus = tk.StringVar(value='0')
        ttk.Entry(ck, textvariable=self.var_misc_checkin_streak_bonus, width=8).grid(
            row=2, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(ck, text='每多连签一天，额外多给多少金币（0 = 不额外给）',
                  foreground='#888888').grid(row=2, column=2, columnspan=3, sticky='w', padx=4)

        ttk.Label(ck, text='物品').grid(row=3, column=0, sticky='w', padx=4, pady=3)
        self.var_misc_checkin_item = tk.StringVar()
        ck_item = ttk.Entry(ck, textvariable=self.var_misc_checkin_item, width=24)
        ck_item.grid(row=3, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Label(ck, text='数量').grid(row=3, column=3, sticky='e', padx=(8, 4))
        self.var_misc_checkin_item_count = tk.StringVar(value='1')
        ttk.Entry(ck, textvariable=self.var_misc_checkin_item_count, width=6).grid(
            row=3, column=4, sticky='w', padx=4, pady=3)
        ttk.Label(ck, text='物品 id，例：diamond / minecraft:diamond（留空 = 只发金币）',
                  foreground='#888888').grid(row=4, column=1, columnspan=4, sticky='w', padx=4)

        self.var_misc_checkin_money_cmd = tk.StringVar()
        self.var_misc_checkin_item_cmd = tk.StringVar()
        self.var_misc_checkin_text = tk.StringVar()
        self.var_misc_checkin_repeat = tk.StringVar()
        self.var_misc_checkin_off = tk.StringVar()
        self._entry(ck, 5, '发金币命令', self.var_misc_checkin_money_cmd,
                    hint='例：eco give {player} {value} / money give {player} {value}')
        self._entry(ck, 6, '发物品命令', self.var_misc_checkin_item_cmd,
                    hint='例：give {player} {item} {count}')
        ttk.Label(ck, text='命令里可用 {player} 玩家、{value}（= {money}）金币、{item} 物品、{count} 数量；'
                           '开头不用写 /，会自动补',
                  foreground='#888888').grid(row=7, column=1, columnspan=4, sticky='w', padx=4)
        self._entry(ck, 8, '签到成功文案', self.var_misc_checkin_text)
        ttk.Label(ck, text='可用 {player}、{money}/{value} 金币、{item}、{count}、'
                           '{items} 物品描述（没物品时为空）、{streak} 连签天数、{total} 累计次数、{reward_status} 发奖状态',
                  foreground='#888888').grid(row=9, column=1, columnspan=4, sticky='w', padx=4)
        self._entry(ck, 10, '今天已签文案', self.var_misc_checkin_repeat,
                    hint='可用 {player}、{streak}、{total}')
        self._entry(ck, 11, '功能关闭文案', self.var_misc_checkin_off,
                    hint='没开签到时回复的话，可用 {player}')
        ttk.Label(ck, text='连签天数 / 累计次数存在 daily.db，重启程序不会丢；跨天自动备份到 db_backup/ 后重置当天记录',
                  foreground='#888888').grid(row=12, column=1, columnspan=4, sticky='w', padx=4, pady=(4, 0))
        ck.columnconfigure(1, weight=1)

        # ---------------- 群发私聊 ----------------
        mass = ttk.LabelFrame(
            sc.inner, text='群发私聊（把一句话用 /msg 发给所有在线玩家）', padding=8)
        mass.pack(fill='x', pady=(8, 0))
        self.var_misc_mass_text = tk.StringVar()
        self.var_misc_mass_skipself = tk.BooleanVar(value=True)
        row = ttk.Frame(mass)
        row.pack(fill='x', pady=3)
        mass_entry = ttk.Entry(row, textvariable=self.var_misc_mass_text)
        mass_entry.pack(side='left', fill='x', expand=True, padx=(4, 6))
        mass_entry.bind('<Return>', lambda _e: self._on_mass_send())
        ttk.Button(row, text='发送给所有玩家', command=self._on_mass_send).pack(side='left')
        row2 = ttk.Frame(mass)
        row2.pack(fill='x', pady=(0, 2))
        ttk.Label(row2, text='每个玩家之间的间隔（秒）').pack(side='left', padx=4)
        self.var_misc_mass_gap = tk.StringVar(value='0.5')
        ttk.Entry(row2, textvariable=self.var_misc_mass_gap, width=8).pack(side='left')
        ttk.Checkbutton(row2, text='跳过机器人自己',
                        variable=self.var_misc_mass_skipself).pack(side='left', padx=(12, 0))
        ttk.Label(mass, text='发送时先向引擎要一份在线名单，再一个一个私聊；间隔太小容易被服务器当刷屏踢下线，'
                             '建议 0.3～1 秒。',
                  foreground='#888888', wraplength=780, justify='left').pack(anchor='w', padx=4)

    def _on_bili_folders(self) -> None:
        """读 B 站收藏夹：验证 Cookie + 把收藏夹填进下拉框。"""
        cookie = (self.var_misc_bili_cookie.get() or '').strip()
        if not cookie:
            messagebox.showinfo('aafm AI 玩家',
                                '先在左边填 SESSDATA（或整条 Cookie），再点「读取收藏夹」。')
            return
        self.var_misc_bili_status.set('正在读取…')

        def work() -> None:
            from . import bilibili
            info = bilibili.login_info(cookie)
            if not info.get('ok'):
                msg = '❌ ' + str(info.get('message') or 'Cookie 无效')
                self.root.after(0, lambda: self.var_misc_bili_status.set(msg))
                return
            res = bilibili.fav_folders(cookie)
            name = str(info.get('name') or '')

            def apply() -> None:
                if not res.get('ok'):
                    self.var_misc_bili_status.set('❌ ' + str(res.get('message') or '没读到'))
                    return
                folders = res.get('folders') or []
                self._bili_fav_map = {}
                labels = []
                for f in folders:
                    label = '%s（%s 个）' % (str(f.get('title') or ''),
                                          int(f.get('count') or 0))
                    self._bili_fav_map[label] = f
                    labels.append(label)
                self.cmb_bili_fav.configure(values=labels)
                self.var_misc_bili_status.set('✅ %s：%s 个收藏夹' % (name or '已登录', len(labels)))
                cur = int(getattr(self.controller.config.misc, 'biliFavMediaId', 0) or 0)
                for label, f in self._bili_fav_map.items():
                    if int(f.get('media_id') or 0) == cur:
                        self.var_misc_bili_fav.set(label)
                        self._bili_fav_cur = {'media_id': cur, 'name': str(f.get('title') or '')}
                        break
                else:
                    if not (self.var_misc_bili_fav.get() or '').strip() and labels:
                        first = self._bili_fav_map[labels[0]]
                        self.var_misc_bili_fav.set(labels[0])
                        self._bili_fav_cur = {'media_id': int(first.get('media_id') or 0),
                                              'name': str(first.get('title') or '')}
                self._append_log(f'已读取 B 站收藏夹：{name}，共 {len(labels)} 个', 'info')

            self.root.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def _on_mass_send(self) -> None:
        """群发按钮：把输入框里的内容 /msg 给所有在线玩家。"""
        text = (self.var_misc_mass_text.get() or '').strip()
        if not text:
            messagebox.showinfo('aafm AI 玩家', '先写点要发的内容吧。')
            return
        if '[点歌]' in text:
            if not messagebox.askyesno(
                    'aafm AI 玩家',
                    '内容里有半角 [点歌]，装了点歌 Mod 的玩家会把它当成点歌。确定要发吗？'):
                return
        self._gather_config()
        gap = self._parse_float(self.var_misc_mass_gap.get(), 0.5)
        n = self.controller.mass_message(text, gap=gap, background=True)
        if n == 0:
            self._append_log('群发没发出去（机器人没连上，或上一次还在发）。', 'warn')
        else:
            self._append_log(f'已开始群发：{text}（间隔 {gap:g} 秒）', 'info')

    def _apply_netease_cookie(self, cookie):
        """把扫码拿到的 Cookie 填进输入框并立即保存到 config.json。"""
        try:
            self.var_song_netease_cookie.set(cookie)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.controller.config.song.neteaseCookie = cookie
            self.controller.config.save()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._netease_status.set('登录成功 ✅ 已保存会员 Cookie：' + (cookie[:24] + '…'))
        except Exception:  # noqa: BLE001
            pass
        self._append_log('网易云登录成功，已保存会员 Cookie。现在可以替玩家播放 VIP 歌了。', 'info')

    # --- 验证登录（自己粘贴的 Cookie 能不能用） ---
    def _on_netease_verify(self):
        """验证输入框里的 Cookie：显示账号昵称，并实测一首会员歌能不能播放。"""
        if getattr(self, '_netease_verify_running', False):
            self._append_log('正在验证中，请稍候…', 'warn')
            return
        cookie = (self.var_song_netease_cookie.get() or '').strip()
        if not cookie:
            self._append_log('请先把网易云 Cookie 粘进上面输入框（含 MUSIC_U=…），再点「验证登录」。', 'warn')
            return
        self._netease_verify_running = True
        self._netease_status.set('正在验证 Cookie…')
        self._append_log('正在验证网易云 Cookie…', 'info')
        threading.Thread(target=self._netease_verify_worker, args=(cookie,), daemon=True).start()

    def _netease_verify_worker(self, cookie):
        res = None
        try:
            from . import netease
            res = netease.check_login(cookie)
        except Exception as e:  # noqa: BLE001
            res = {'ok': False, 'message': '验证出错：' + str(e)}
        msg = (res or {}).get('message') or '验证失败'
        ok = bool((res or {}).get('ok'))

        def done():
            self._netease_verify_running = False
            try:
                self._netease_status.set(msg)
            except Exception:  # noqa: BLE001
                pass
            self._append_log(msg, 'info' if ok else 'warn')
            if ok:
                # 验证通过就顺手存进 config.json
                try:
                    self.controller.config.song.neteaseCookie = cookie
                    self.controller.config.save()
                except Exception:  # noqa: BLE001
                    pass
        self._ui(done)

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
    # 玩家配置内的：随机发言 / 屏蔽命令（表格 + 红绿启用按钮）
    # ------------------------------------------------------------------
    def _make_toggle_button(self, parent, var, on_change=None):
        """A green (enabled) / red (disabled) toggle button bound to ``var``."""
        btn = tk.Button(parent, relief='flat', fg='white', font=('Segoe UI', 9, 'bold'),
                        width=8, cursor='hand2')

        def refresh():
            if var.get():
                btn.config(text='已启用', bg='#2e7d32', activebackground='#276b2a')
            else:
                btn.config(text='已禁用', bg='#c62828', activebackground='#a52222')

        def toggle():
            var.set(not var.get())
            refresh()
            if on_change:
                try:
                    on_change()
                except Exception:  # noqa: BLE001
                    pass

        btn.config(command=toggle)
        refresh()
        return btn, refresh

    def _chk(self, parent, text='', variable=None, command=None, **kw):
        """Drop-in Checkbutton replacement: a green(enabled)/red(disabled) button."""
        btn = tk.Button(parent, relief='flat', bd=0, anchor='w', justify='left',
                        activeforeground='white', cursor='hand2', wraplength=680)

        def refresh():
            try:
                on = bool(variable.get()) if variable is not None else False
            except Exception:  # noqa: BLE001
                on = False
            btn.config(text=('● ' if on else '○ ') + str(text),
                       fg='white',
                       bg=('#2e7d32' if on else '#c62828'),
                       activebackground=('#276b2a' if on else '#a52222'))

        def click():
            if variable is not None:
                variable.set(not variable.get())
            refresh()
            if command:
                try:
                    command()
                except Exception:  # noqa: BLE001
                    pass

        btn.config(command=click)
        if variable is not None:
            try:
                variable.trace_add('write', lambda *a: refresh())
            except Exception:  # noqa: BLE001
                pass
        refresh()
        return btn

    def _build_random_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent, text='随机发言（每条独立随机时间，最小~最大秒）', padding=8)
        frm.pack(fill='x', pady=(0, 8))

        self.var_idle_enabled = tk.BooleanVar(value=False)
        head = ttk.Frame(frm)
        head.pack(fill='x')
        self.idle_toggle_btn, self._refresh_idle_toggle = self._make_toggle_button(
            head, self.var_idle_enabled, on_change=self._on_idle_toggle)
        self.idle_toggle_btn.pack(side='left')
        ttk.Label(head, text='  每次条数').pack(side='left', padx=(8, 2))
        self.var_idle_cmin = tk.StringVar()
        self.var_idle_cmax = tk.StringVar()
        ttk.Entry(head, textvariable=self.var_idle_cmin, width=4).pack(side='left')
        ttk.Label(head, text='~').pack(side='left')
        ttk.Entry(head, textvariable=self.var_idle_cmax, width=4).pack(side='left')
        ttk.Label(head, text='  文本', foreground='#888888').pack(side='left', padx=(8, 0))
        ttk.Label(head, text='最小秒', foreground='#888888').pack(side='right', padx=(0, 56))
        ttk.Label(head, text='最大秒', foreground='#888888').pack(side='right', padx=(0, 6))

        self.random_rows_frame = ttk.Frame(frm)
        self.random_rows_frame.pack(fill='x')
        self._random_rows = []
        ttk.Button(frm, text='＋ 添加一行', command=lambda: self._add_random_row()).pack(
            anchor='w', pady=(6, 0))
        ttk.Label(frm, text='每条按自己的「最小~最大秒」随机到点发言；支持 & 颜色码。',
                  foreground='#888888').pack(anchor='w', pady=(2, 0))

    def _add_random_row(self, text: str = '', lo: int = 60, hi: int = 300):
        row = ttk.Frame(self.random_rows_frame)
        row.pack(fill='x', pady=2)
        t_var = tk.StringVar(value=str(text))
        lo_var = tk.StringVar(value=str(lo))
        hi_var = tk.StringVar(value=str(hi))
        ttk.Entry(row, textvariable=t_var).pack(side='left', fill='x', expand=True)
        ttk.Entry(row, textvariable=hi_var, width=7).pack(side='right', padx=(4, 0))
        ttk.Entry(row, textvariable=lo_var, width=7).pack(side='right', padx=(4, 0))

        def remove():
            try:
                row.destroy()
            finally:
                self._random_rows = [r for r in self._random_rows if r['row'] is not row]

        ttk.Button(row, text='删除', command=remove).pack(side='right', padx=(6, 0))
        rec = {'text': t_var, 'min': lo_var, 'max': hi_var, 'row': row}
        self._random_rows.append(rec)
        return rec

    def _build_command_block_frame(self, parent) -> None:
        frm = ttk.LabelFrame(
            parent, text='屏蔽命令（防止提示词注入执行危险命令，如 pay / kick / ban）', padding=8)
        frm.pack(fill='x', pady=(0, 8))

        self.var_cmdblock_enabled = tk.BooleanVar(value=True)
        head = ttk.Frame(frm)
        head.pack(fill='x')
        self.cmd_toggle_btn, self._refresh_cmd_toggle = self._make_toggle_button(
            head, self.var_cmdblock_enabled, on_change=self._on_cmdblock_toggle)
        self.cmd_toggle_btn.pack(side='left')
        ttk.Label(head, text='  机器人发出的这些命令会被拦截（不含斜杠）',
                  foreground='#888888').pack(side='left', padx=(8, 0))

        self.cmd_rows_frame = ttk.Frame(frm)
        self.cmd_rows_frame.pack(fill='x')
        self._cmd_rows = []
        ttk.Button(frm, text='＋ 添加一行', command=lambda: self._add_command_row()).pack(
            anchor='w', pady=(6, 0))

        msgrow = ttk.Frame(frm)
        msgrow.pack(fill='x', pady=(6, 0))
        ttk.Label(msgrow, text='屏蔽后发送').pack(side='left')
        self.var_cmdblock_msg = tk.StringVar()
        ttk.Entry(msgrow, textvariable=self.var_cmdblock_msg).pack(
            side='left', fill='x', expand=True, padx=(6, 0))
        ttk.Label(frm, text='占位符 {cmd}=命令名；留空=只拦截不发送。支持 & 颜色码。',
                  foreground='#888888').pack(anchor='w', pady=(2, 0))

    def _add_command_row(self, cmd: str = ''):
        row = ttk.Frame(self.cmd_rows_frame)
        row.pack(fill='x', pady=2)
        var = tk.StringVar(value=str(cmd))
        ttk.Entry(row, textvariable=var).pack(side='left', fill='x', expand=True)

        def remove():
            try:
                row.destroy()
            finally:
                self._cmd_rows = [r for r in self._cmd_rows if r['row'] is not row]

        ttk.Button(row, text='删除', command=remove).pack(side='right', padx=(6, 0))
        rec = {'cmd': var, 'row': row}
        self._cmd_rows.append(rec)
        return rec

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
        self.var_reconnect_unit = tk.StringVar(value='秒')
        self.var_reconnect_max = tk.StringVar()
        self.var_keep_retry = tk.BooleanVar(value=False)
        self.var_keep_retry_max = tk.StringVar()
        self.var_kick_lobby = tk.BooleanVar(value=True)
        self.var_kick_lobby_delay = tk.StringVar()

        ttk.Checkbutton(frm, text='启用自动重连（服务器掉线 / 被踢出 / 命中触发语时；手动点「断开」不会重连）',
                        variable=self.var_reconnect_enabled).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='重连间隔').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_reconnect_delay, width=8).grid(
            row=1, column=1, sticky='w', padx=4, pady=3)
        ttk.Combobox(frm, textvariable=self.var_reconnect_unit, state='readonly',
                     values=['秒', '分钟', '小时', '天'], width=6).grid(
            row=1, column=2, sticky='w', padx=4, pady=3)
        self._entry(frm, 2, '最大重连次数（重连成功后重置）', self.var_reconnect_max)

        self.var_proactive_enabled = tk.BooleanVar(value=False)
        self.var_proactive_value = tk.StringVar()
        self.var_proactive_unit = tk.StringVar(value='分钟')
        ttk.Checkbutton(frm, text='在线定时主动重连（每隔设定时间断开重连一次）',
                        variable=self.var_proactive_enabled).grid(
            row=3, column=0, columnspan=2, sticky='w', padx=4, pady=(8, 3))
        ttk.Entry(frm, textvariable=self.var_proactive_value, width=8).grid(
            row=3, column=2, sticky='w', padx=4, pady=(8, 3))
        ttk.Combobox(frm, textvariable=self.var_proactive_unit, state='readonly',
                     values=['秒', '分钟', '小时', '天'], width=6).grid(
            row=3, column=3, sticky='w', padx=4, pady=(8, 3))

        ttk.Checkbutton(frm, text='被踢出并重连成功后自动发送 /lobby',
                        variable=self.var_kick_lobby).grid(
            row=4, column=0, columnspan=4, sticky='w', padx=4, pady=(8, 3))
        self._entry(frm, 5, '/lobby 延迟（秒）', self.var_kick_lobby_delay)

        ttk.Label(frm, text='自定义重连触发语（每行一个，正则/包含，命中即自动重连）').grid(
            row=6, column=0, columnspan=5, sticky='w', padx=4, pady=(8, 2))
        self.reconnect_triggers_text = tk.Text(frm, height=4, width=72, wrap='word')
        self.reconnect_triggers_text.grid(row=7, column=0, columnspan=5, sticky='we', padx=4)
        ttk.Label(frm, text='例：You were kicked from lobby  —— 服务器聊天/公告里出现该句就重连。',
                  foreground='#888888', wraplength=720, justify='left').grid(
            row=8, column=0, columnspan=5, sticky='w', padx=4, pady=(2, 0))

        ttk.Label(frm,
                  text='说明：连接意外结束（被踢/掉线）时按上面设置自动重连；若属于 LittleSkin '
                       '登录失效，则走自动重新登录流程，不做普通重连。重连成功进入服务器后，'
                       '等待「/lobby 延迟」再发送 /lobby。',
                  foreground='#888888', wraplength=720, justify='left').grid(
            row=9, column=0, columnspan=5, sticky='w', padx=4, pady=(6, 0))

        active = ttk.LabelFrame(sc.inner, text='主动连接', padding=8)
        active.pack(fill='x', pady=(8, 0))

        self.var_autolobby_enabled = tk.BooleanVar(value=False)
        self.var_autolobby_value = tk.StringVar()
        self.var_autolobby_unit = tk.StringVar(value='分钟')
        r1 = ttk.Frame(active)
        r1.pack(fill='x')
        b1, _ = self._make_toggle_button(r1, self.var_autolobby_enabled,
                                         on_change=self._on_active_toggle)
        b1.pack(side='left')
        ttk.Label(r1, text='  每隔').pack(side='left', padx=(8, 2))
        ttk.Entry(r1, textvariable=self.var_autolobby_value, width=6).pack(side='left')
        ttk.Combobox(r1, textvariable=self.var_autolobby_unit, state='readonly',
                     values=['秒', '分钟', '小时'], width=6).pack(side='left', padx=4)
        ttk.Label(r1, text='主动发送 /lobby 进入服务器大厅').pack(side='left', padx=4)

        self.var_onlinecheck_enabled = tk.BooleanVar(value=False)
        self.var_onlinecheck_value = tk.StringVar()
        self.var_onlinecheck_unit = tk.StringVar(value='分钟')
        self.var_onlinecheck_retry = tk.StringVar()
        r2 = ttk.Frame(active)
        r2.pack(fill='x', pady=(6, 0))
        b2, _ = self._make_toggle_button(r2, self.var_onlinecheck_enabled,
                                         on_change=self._on_active_toggle)
        b2.pack(side='left')
        ttk.Label(r2, text='  每隔').pack(side='left', padx=(8, 2))
        ttk.Entry(r2, textvariable=self.var_onlinecheck_value, width=6).pack(side='left')
        ttk.Combobox(r2, textvariable=self.var_onlinecheck_unit, state='readonly',
                     values=['秒', '分钟', '小时'], width=6).pack(side='left', padx=4)
        ttk.Label(r2, text='检测在线；离线时每').pack(side='left', padx=4)
        ttk.Entry(r2, textvariable=self.var_onlinecheck_retry, width=6).pack(side='left')
        ttk.Label(r2, text='秒重试，直到连上').pack(side='left', padx=4)

    def _on_active_toggle(self):
        self._gather_config()
        self._append_log('主动连接设置已更新。', 'info')

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

    # ------------------------------------------------------------------
    # 文档转书 actions
    # ------------------------------------------------------------------
    def _on_choose_book_file(self) -> None:
        path = filedialog.askopenfilename(
            title='选择要转换的文档',
            filetypes=[('文档', '*.txt *.docx *.doc'), ('所有文件', '*.*')])
        if not path:
            return
        self.var_book_file.set(path)
        self._on_preview_book()

    def _book_pages(self):
        """Read the chosen document and split it into pages (or None on error)."""
        path = self.var_book_file.get().strip()
        if not path:
            messagebox.showwarning('aafm', '请先选择要转换的文档。')
            return None
        if not os.path.exists(path):
            messagebox.showerror('aafm', f'文件不存在：\n{path}')
            return None
        max_lines = self._parse_int(self.var_book_pagelines.get(), 14)
        max_lines = max(1, min(docbook.MAX_LINES_PER_PAGE, max_lines))
        try:
            # Pagination is driven by the line count; the character count is only
            # the game's hard limit (1024) used as a safety cap.
            pages = docbook.load_pages(
                path, max_chars=docbook.MAX_PAGE_CHARS, max_lines=max_lines)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror('aafm', f'读取文档失败：\n{e}')
            return None
        return pages

    def _on_preview_book(self) -> None:
        pages = self._book_pages()
        if pages is None:
            return
        self.book_preview.configure(state='normal')
        self.book_preview.delete('1.0', 'end')
        if not pages:
            self.book_preview.insert('end', '（没有解析到文本内容）')
        else:
            total_chars = sum(len(p) for p in pages)
            self.book_preview.insert(
                'end', f'共 {len(pages)} 页，{total_chars} 字符。\n'
                       f'（每页 ≤ {docbook.MAX_LINES_PER_PAGE} 行，行宽按保守字体估算 ≤ '
                       f'{docbook.PAGE_LINE_WIDTH}px，不会超出书页）\n'
                       + '=' * 48 + '\n')
            for i, page in enumerate(pages, 1):
                lines = page.split('\n')
                max_w = max((docbook.line_width(x) for x in lines), default=0)
                self.book_preview.insert(
                    'end',
                    f'—— 第 {i} 页 ({len(page)} 字 / {len(lines)} 行 / 最宽 {max_w}px) ——\n')
                self.book_preview.insert('end', page + '\n\n')
        self.book_preview.configure(state='disabled')
        self._append_log(f'已预览分页：{len(pages)} 页。', 'info')

    def _on_write_book(self) -> None:
        pages = self._book_pages()
        if pages is None:
            return
        if not pages:
            messagebox.showwarning('aafm', '没有解析到可写入的文本内容。')
            return
        self._gather_config()
        cfg = self.controller.config.docBook
        if cfg.signBook and not cfg.titleTemplate.strip():
            if not messagebox.askyesno(
                    'aafm', '已勾选“编辑为成书”，但书名模板为空，将签名为无标题成书。继续吗？'):
                return
        ok = self.controller.write_book(
            pages,
            start_text=cfg.startText,
            end_text=cfg.endText,
            end_drop=cfg.endDrop,
            auto_get_book=cfg.autoGetBook,
            pages_per_book=cfg.pagesPerBook,
            sign_book=cfg.signBook,
            title_template=cfg.titleTemplate,
            doc_file=self.var_book_file.get().strip(),
            start_page=0, book_start=1)
        if ok:
            self._append_log(
                f'已发送写入指令：{len(pages)} 页（引擎将写入书与笔）。', 'sent')
        else:
            self._append_log('写入指令发送失败（未连接或引擎未就绪）。', 'error')

    def _on_book_control(self, action: str) -> None:
        self._gather_config()
        self.controller.pause_book(action)

    def _on_continue_book(self) -> None:
        self._gather_config()
        self.controller.continue_book()

    def _on_rewrite_book(self) -> None:
        if not messagebox.askyesno('aafm', '从第一页重新写入？（已记录的进度将被覆盖）'):
            return
        self._gather_config()
        self.controller.rewrite_book()

    def _on_start_from_book(self) -> None:
        """Start writing from the N-th volume (册/本): page offset + {number}."""
        pages = self._book_pages()
        if pages is None:
            return
        if not pages:
            messagebox.showwarning('aafm', '没有解析到可写入的文本内容。')
            return
        self._gather_config()
        cfg = self.controller.config.docBook
        per_book = max(1, int(cfg.pagesPerBook or 100))
        total_books = max(1, (len(pages) + per_book - 1) // per_book)
        book_no = self._parse_int(self.var_book_startbook.get(), 1)
        if book_no < 1:
            book_no = 1
        if book_no > total_books:
            messagebox.showwarning(
                'aafm', f'本文档共约 {total_books} 册，起始册号 {book_no} 超出范围。')
            return
        start_page = (book_no - 1) * per_book
        ok = self.controller.write_book(
            pages,
            start_text=cfg.startText,
            end_text=cfg.endText,
            end_drop=cfg.endDrop,
            auto_get_book=cfg.autoGetBook,
            pages_per_book=per_book,
            sign_book=cfg.signBook,
            title_template=cfg.titleTemplate,
            doc_file=self.var_book_file.get().strip(),
            start_page=start_page, book_start=book_no)
        if ok:
            self._append_log(
                f'已发送写入指令：从第 {book_no} 册（第 {start_page + 1} 页）开始。', 'sent')
        else:
            self._append_log('写入指令发送失败（未连接或引擎未就绪）。', 'error')

    def _on_clear_book(self) -> None:
        if not messagebox.askyesno(
                'aafm',
                '确定要清空机器人背包里所有「书与笔」的内容吗？\n'
                '（只删除书页文字，不删除书与笔本身）'):
            return
        if self.controller.clear_book():
            self._append_log('已发送清空书与笔指令（引擎将删除书页内容）。', 'sent')
        else:
            self._append_log('清空指令发送失败（未连接或引擎未就绪）。', 'error')

    def _build_qq_frame(self, parent) -> None:
        # ============ 模块 1：网关设置 ============
        net = ttk.LabelFrame(parent, text='网关设置（OneBot 11：SnowLuma / NapCat）', padding=8)
        net.pack(fill='x', pady=(8, 0))

        self.var_qq_framework = tk.StringVar(value='SnowLuma')
        self.var_qq_enabled = tk.BooleanVar(value=False)
        self.var_qq_mode = tk.StringVar(value='forward')
        self.var_qq_url = tk.StringVar()
        self.var_qq_token = tk.StringVar()
        self.var_qq_listen_host = tk.StringVar()
        self.var_qq_listen_port = tk.StringVar()
        self.var_qq_launcher = tk.StringVar()
        self._qq_editing_framework = 'snowluma'

        r = ttk.Frame(net)
        r.pack(fill='x', pady=2)
        ttk.Label(r, text='启动框架').pack(side='left')
        fwbox = ttk.Combobox(r, textvariable=self.var_qq_framework, state='readonly',
                             values=['SnowLuma', 'NapCat'], width=10)
        fwbox.pack(side='left', padx=(4, 12))
        fwbox.bind('<<ComboboxSelected>>', lambda e: self._on_qq_framework_changed())

        r = ttk.Frame(net)
        r.pack(fill='x', pady=2)
        self.qq_launcher_label = ttk.Label(r, text='启动路径')
        self.qq_launcher_label.pack(side='left')
        ttk.Entry(r, textvariable=self.var_qq_launcher).pack(
            side='left', fill='x', expand=True, padx=(4, 0))

        r = ttk.Frame(net)
        r.pack(fill='x', pady=2)
        ttk.Label(r, text='连接模式').pack(side='left')
        ttk.Combobox(r, textvariable=self.var_qq_mode, state='readonly',
                     values=['forward', 'reverse'], width=9).pack(side='left', padx=(4, 10))
        ttk.Label(r, text='正向地址').pack(side='left')
        ttk.Entry(r, textvariable=self.var_qq_url).pack(
            side='left', fill='x', expand=True, padx=(4, 0))

        r = ttk.Frame(net)
        r.pack(fill='x', pady=2)
        ttk.Label(r, text='Access Token').pack(side='left')
        ttk.Entry(r, textvariable=self.var_qq_token, show='*', width=26).pack(
            side='left', padx=(4, 12))
        ttk.Label(r, text='反向监听').pack(side='left')
        ttk.Entry(r, textvariable=self.var_qq_listen_host, width=13).pack(side='left', padx=(4, 2))
        ttk.Entry(r, textvariable=self.var_qq_listen_port, width=6).pack(side='left')

        r = ttk.Frame(net)
        r.pack(fill='x', pady=(6, 0))
        ttk.Button(r, text='启动', command=self._on_qq_launch_framework).pack(side='left', padx=2)
        ttk.Button(r, text='快速登录', command=self._on_qq_quick_login).pack(side='left', padx=2)
        ttk.Button(r, text='连接', command=self._on_qq_start).pack(side='left', padx=2)
        ttk.Button(r, text='重连', command=self._on_qq_reconnect).pack(side='left', padx=2)
        ttk.Button(r, text='断开', command=self._on_qq_stop).pack(side='left', padx=2)
        self.qq_status_label = ttk.Label(r, text='未连接', foreground='#999999')
        self.qq_status_label.pack(side='left', padx=12)

        ttk.Label(net,
                  text='SnowLuma 填它解压后的目录；NapCat 填含 launcher-win10.bat 的目录（留空用项目自带的 NapCat/）。'
                       '两个框架的路径 / 地址 / Token 各自独立保存，切换「启动框架」即切换。',
                  foreground='#888888', wraplength=780, justify='left').pack(anchor='w', padx=2, pady=(4, 0))

        # ============ 模块 2：双向转发 ============
        relay = ttk.LabelFrame(parent, text='双向转发', padding=8)
        relay.pack(fill='x', pady=(8, 0))

        q2m = ttk.LabelFrame(relay, text='QQ → MC：群消息转述到游戏公屏', padding=6)
        q2m.pack(fill='x')
        self.var_qq_groups = tk.StringVar()
        self.var_qq_format = tk.StringVar()
        self.var_qq_to_mc = tk.BooleanVar(value=True)
        r = ttk.Frame(q2m)
        r.pack(fill='x', pady=2)
        ttk.Checkbutton(r, text='启用 QQ → MC（关闭只停止发到游戏，不断开 QQ、不影响 MC → QQ）',
                        variable=self.var_qq_to_mc,
                        command=self._on_qq_to_mc).pack(side='left')
        r = ttk.Frame(q2m)
        r.pack(fill='x', pady=2)
        ttk.Label(r, text='群号').pack(side='left')
        ttk.Entry(r, textvariable=self.var_qq_groups).pack(
            side='left', fill='x', expand=True, padx=(4, 8))
        ttk.Label(r, text='多个用逗号/空格分隔；留空=全部群', foreground='#888888').pack(side='left')
        r = ttk.Frame(q2m)
        r.pack(fill='x', pady=2)
        ttk.Label(r, text='转述格式').pack(side='left')
        ttk.Entry(r, textvariable=self.var_qq_format).pack(
            side='left', fill='x', expand=True, padx=(4, 8))
        ttk.Label(r, text='{group} {name} {message}', foreground='#888888').pack(side='left')
        ttk.Label(q2m, text='图片/表情等非文本以 [图片]/[表情] 占位；勾选「图片转本地链接」后图片会变成链接。',
                  foreground='#888888', wraplength=780, justify='left').pack(anchor='w', padx=2, pady=(2, 0))

        m2q = ttk.LabelFrame(relay, text='MC → QQ：公屏消息以触发词开头即发送到群', padding=6)
        m2q.pack(fill='x', pady=(8, 0))
        self.var_mc2qq_enabled = tk.BooleanVar(value=False)
        self.var_mc2qq_trigger = tk.StringVar()
        self.var_mc2qq_group = tk.StringVar()
        self.var_mc2qq_format = tk.StringVar()
        self.var_mc2qq_cooldown = tk.StringVar()
        self.var_mc2qq_ok = tk.StringVar()
        self.var_mc2qq_fail = tk.StringVar()
        r = ttk.Frame(m2q)
        r.pack(fill='x', pady=2)
        ttk.Checkbutton(r, text='启用 MC → QQ', variable=self.var_mc2qq_enabled).pack(side='left')
        ttk.Label(r, text='触发词（, 分隔）').pack(side='left', padx=(12, 4))
        ttk.Entry(r, textvariable=self.var_mc2qq_trigger, width=24).pack(side='left')
        ttk.Label(r, text='目标群').pack(side='left', padx=(12, 4))
        ttk.Entry(r, textvariable=self.var_mc2qq_group, width=16).pack(side='left')
        ttk.Label(r, text='留空=用上方群号第一个；也可 [sentQ:群号] 临时指定',
                  foreground='#888888').pack(side='left', padx=(6, 0))
        r = ttk.Frame(m2q)
        r.pack(fill='x', pady=2)
        ttk.Label(r, text='消息模板').pack(side='left')
        ttk.Entry(r, textvariable=self.var_mc2qq_format).pack(
            side='left', fill='x', expand=True, padx=(4, 8))
        ttk.Label(r, text='{player} / {message}', foreground='#888888').pack(side='left')
        r = ttk.Frame(m2q)
        r.pack(fill='x', pady=2)
        ttk.Label(r, text='冷却秒').pack(side='left')
        ttk.Entry(r, textvariable=self.var_mc2qq_cooldown, width=6).pack(side='left', padx=(4, 12))
        ttk.Label(r, text='成功提示').pack(side='left')
        ttk.Entry(r, textvariable=self.var_mc2qq_ok, width=20).pack(side='left', padx=(4, 8))
        ttk.Label(r, text='失败提示').pack(side='left')
        ttk.Entry(r, textvariable=self.var_mc2qq_fail, width=20).pack(side='left', padx=(4, 0))

        # ============ 模块 3：账号安全 ============
        sec = ttk.LabelFrame(parent, text='账号安全（可选）', padding=6)
        sec.pack(fill='x', pady=(8, 0))
        self.var_qq_autoreconnect = tk.BooleanVar(value=True)
        self.var_qq_health = tk.StringVar()
        self.var_qq_autoconfig = tk.BooleanVar(value=True)
        r = ttk.Frame(sec)
        r.pack(fill='x', pady=2)
        ttk.Checkbutton(r, text='自动重连', variable=self.var_qq_autoreconnect).pack(side='left')
        ttk.Label(r, text='在线检测秒').pack(side='left', padx=(12, 4))
        ttk.Entry(r, textvariable=self.var_qq_health, width=6).pack(side='left')
        ttk.Checkbutton(r, text='自动为新扫码账号写配置（仅 NapCat）',
                        variable=self.var_qq_autoconfig).pack(side='left', padx=(12, 0))
        ttk.Label(sec, text='连接因疑似风控被关闭、或在线检测连续失败时，会告警并停止自动重连，避免反复登录加重风控；恢复后点「重连」。',
                  foreground='#888888', wraplength=780, justify='left').pack(anchor='w', padx=2, pady=(2, 0))

        # ============ 模块 4：图片转本地链接 ============
        img = ttk.LabelFrame(parent, text='图片转本地链接（Python HTTP 服务，仅暴露 /pic_http/）', padding=6)
        img.pack(fill='x', pady=(8, 0))
        self.var_qq_image_link = tk.BooleanVar(value=False)
        self.var_qq_img_port = tk.StringVar()
        self.var_qq_img_base = tk.StringVar()
        r = ttk.Frame(img)
        r.pack(fill='x', pady=2)
        ttk.Checkbutton(r, text='图片转链接', variable=self.var_qq_image_link).pack(side='left')
        ttk.Label(r, text='监听端口').pack(side='left', padx=(12, 4))
        ttk.Entry(r, textvariable=self.var_qq_img_port, width=8).pack(side='left')
        ttk.Label(r, text='公网地址').pack(side='left', padx=(12, 4))
        ttk.Entry(r, textvariable=self.var_qq_img_base).pack(
            side='left', fill='x', expand=True, padx=(4, 0))
        ttk.Label(img, text='留空=自动探测公网 IP，例 http://1.2.3.4:8765；图片存到项目 pic_http/，需在路由器把端口转发到本机。',
                  foreground='#888888', wraplength=780, justify='left').pack(anchor='w', padx=2, pady=(2, 0))

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
        self._build_health_frame(frm)
        self._build_verify_frame(frm)
        self._build_random_frame(frm)
        self._build_command_block_frame(frm)

    def _on_idle_toggle(self):
        self._gather_config()
        self._append_log('随机发言已' + ('启用' if self.var_idle_enabled.get() else '禁用')
                         + '。', 'info')

    def _on_cmdblock_toggle(self):
        self._gather_config()
        self._append_log('屏蔽命令已' + ('启用' if self.var_cmdblock_enabled.get() else '禁用')
                         + '。', 'info')

    def _build_verify_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent, text='进服人机验证自动通过（箱子界面找异类）', padding=8)
        frm.pack(fill='x', pady=(0, 8))

        self.var_verify_enabled = tk.BooleanVar(value=False)
        self.var_verify_title = tk.StringVar()
        self.var_verify_name = tk.StringVar()
        self.var_verify_delay = tk.StringVar()

        ttk.Checkbutton(frm, text='启用：打开验证界面时自动点击目标物品',
                        variable=self.var_verify_enabled).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=4, pady=3)

        ttk.Label(frm, text='标题关键词').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_verify_title, width=30).grid(
            row=1, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Label(frm, text='例 HumanVerify', foreground='#888888').grid(
            row=1, column=3, sticky='w', padx=4)

        ttk.Label(frm, text='目标物品名').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_verify_name, width=30).grid(
            row=2, column=1, columnspan=2, sticky='we', padx=4, pady=3)
        ttk.Label(frm, text='含此名的物品会被点击；有“步骤N”则按序号顺序',
                  foreground='#888888').grid(row=2, column=3, sticky='w', padx=4)

        ttk.Label(frm, text='点击间隔(ms)').grid(row=3, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_verify_delay, width=8).grid(
            row=3, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='找不到命名目标时，回退为“找异类”判定（多数=背景，少数=目标）',
                  foreground='#888888').grid(row=4, column=0, columnspan=4, sticky='w', padx=4)
        frm.columnconfigure(1, weight=1)

    def _build_health_frame(self, parent) -> None:
        frm = ttk.LabelFrame(parent, text='低血量报警', padding=8)
        frm.pack(fill='x', pady=(0, 8))

        self.var_health_enabled = tk.BooleanVar(value=False)
        self.var_health_threshold = tk.StringVar()
        self.var_health_cooldown = tk.StringVar()
        self.var_health_msg = tk.StringVar()

        ttk.Checkbutton(frm, text='启用：血量低于阈值时在公屏发言',
                        variable=self.var_health_enabled).grid(
            row=0, column=0, columnspan=4, sticky='w', padx=4, pady=3)

        ttk.Label(frm, text='预设血量').grid(row=1, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_health_threshold, width=6).grid(
            row=1, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(frm, text='（1-20，血量≤该值即触发）', foreground='#888888').grid(
            row=1, column=2, sticky='w', padx=4)
        ttk.Label(frm, text='冷却秒').grid(row=1, column=3, sticky='e', padx=4)
        ttk.Entry(frm, textvariable=self.var_health_cooldown, width=6).grid(
            row=1, column=4, sticky='w', padx=4)

        ttk.Label(frm, text='预设话').grid(row=2, column=0, sticky='w', padx=4, pady=3)
        ttk.Entry(frm, textvariable=self.var_health_msg, width=60).grid(
            row=2, column=1, columnspan=4, sticky='we', padx=4, pady=3)
        ttk.Label(frm, text='支持 & 颜色码；冷却内不会重复发言。',
                  foreground='#888888').grid(
            row=3, column=0, columnspan=5, sticky='w', padx=4)
        frm.columnconfigure(2, weight=1)

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

        self.var_server_ctx = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text='服务器消息加入上下文（供 AI 参考）',
                        variable=self.var_server_ctx).grid(
            row=3, column=2, columnspan=2, sticky='w', padx=4, pady=3)

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
    # Tab: 日志页
    # ------------------------------------------------------------------
    def _build_output_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text=' 日志 ')

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
        self.log_text.tag_configure('song', foreground='#4dd0e1')
        self.log_text.tag_configure('server', foreground='#90caf9')

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
        stamp = time.strftime('[%H:%M:%S] ')
        self.log_text.configure(state='normal')
        self.log_text.insert('end', stamp + text + '\n', tag)
        self.log_text.see('end')
        self.log_text.configure(state='disabled')
        # Backend events are already saved by the controller; only record the
        # lines the GUI itself originates (settings saved, manual actions...).
        if not getattr(self, '_from_backend', False):
            try:
                self.controller.record_log(text)
            except Exception:  # noqa: BLE001
                pass

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
        self.var_server_ctx.set(bool(getattr(ai, 'serverMessagesInContext', True)))
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
        self.var_idle_enabled.set(bool(ai.idleChatEnabled))
        self.var_idle_cmin.set(str(ai.idleChatCountMin))
        self.var_idle_cmax.set(str(ai.idleChatCountMax))
        for _rec in list(getattr(self, '_random_rows', [])):
            _rec['row'].destroy()
        self._random_rows = []
        for _it in (ai.idleChatItems or []):
            self._add_random_row(_it.get('text', ''), _it.get('min', 60), _it.get('max', 300))
        if hasattr(self, '_refresh_idle_toggle'):
            self._refresh_idle_toggle()
        self.var_image_model.set(ai.imageModel)
        self.var_search_enabled.set(bool(ai.searchEnabled))
        self.var_search_provider.set(ai.searchProvider or 'duckduckgo')
        self.var_search_url.set(ai.searchApiUrl or '')
        self.var_search_key.set(ai.searchApiKey or '')
        self.var_search_n.set(str(ai.searchMaxResults))
        self.var_search_regex.set(ai.searchTriggerRegex or '')

        self.var_cmdblock_enabled.set(bool(cfg.commandBlock.enabled))
        for _rec in list(getattr(self, '_cmd_rows', [])):
            _rec['row'].destroy()
        self._cmd_rows = []
        for _c in (cfg.commandBlock.commands or []):
            self._add_command_row(_c)
        self.var_cmdblock_msg.set(cfg.commandBlock.message or '')
        if hasattr(self, '_refresh_cmd_toggle'):
            self._refresh_cmd_toggle()

        self.var_quiz.set(cfg.quiz.enabled)
        self.var_autoeat.set(cfg.autoeat.enabled)
        self.var_health_enabled.set(bool(cfg.healthAlert.enabled))
        self.var_health_threshold.set(str(cfg.healthAlert.threshold))
        self.var_health_cooldown.set(str(cfg.healthAlert.cooldownSeconds))
        self.var_health_msg.set(cfg.healthAlert.message or '')
        self.var_verify_enabled.set(bool(cfg.verify.enabled))
        self.var_verify_title.set(cfg.verify.titleKeyword or '')
        self.var_verify_name.set(cfg.verify.nameKeyword or '')
        self.var_verify_delay.set(str(cfg.verify.clickDelayMs))
        lf = cfg.logForward
        self.var_logfwd_enabled.set(bool(lf.enabled))
        self.var_logfwd_format.set(lf.format or '&7Log -> &c{log}')
        _lf_levels = [str(x).lower() for x in (lf.levels or [])]
        self.var_logfwd_error.set('error' in _lf_levels)
        self.var_logfwd_warn.set('warn' in _lf_levels)
        self.var_roster.set(cfg.roster.enabled)
        self.var_roster_research.set(cfg.roster.personalityEnabled)
        self.var_roster_alias.set(cfg.roster.aliasEnabled)
        self.var_roster_research_interval.set(str(cfg.roster.researchIntervalSeconds))

        self.var_qq_enabled.set(cfg.qq.enabled)
        self.var_qq_to_mc.set(bool(cfg.qq.qqToMcEnabled))
        self.var_qq_groups.set(', '.join(cfg.qq.groups or []))
        self.var_qq_format.set(cfg.qq.format or '')
        self.var_qq_image_link.set(bool(cfg.qq.imageAsLink))
        self.var_qq_img_port.set(str(cfg.qq.imageHttpPort))
        self.var_qq_img_base.set(cfg.qq.imagePublicBase or '')
        self.var_qq_autoreconnect.set(bool(cfg.qq.autoReconnect))
        self.var_qq_health.set(str(cfg.qq.healthCheckSeconds))
        self.var_qq_autoconfig.set(bool(cfg.qq.napcat.autoConfigNewAccounts))
        # Endpoint fields follow the selected framework (SnowLuma / NapCat each
        # keep their own path / url / token).
        self._qq_editing_framework = ('napcat' if cfg.qq.framework == 'napcat' else 'snowluma')
        self.var_qq_framework.set('NapCat' if cfg.qq.framework == 'napcat' else 'SnowLuma')
        self._load_qq_endpoint_widgets(self._qq_editing_framework, auto_token=True)

        self.var_mc2qq_enabled.set(cfg.qq.mcToQqEnabled)
        self.var_mc2qq_trigger.set(cfg.qq.mcTrigger or '[sentQ]')
        self.var_mc2qq_group.set(cfg.qq.mcTargetGroup or '')
        self.var_mc2qq_format.set(cfg.qq.mcFormat or '[MC] {player}：{message}')
        self.var_mc2qq_cooldown.set(str(cfg.qq.mcCooldownSeconds))
        self.var_mc2qq_ok.set(cfg.qq.mcSuccessMsg or '&a@{player}转述成功')
        self.var_mc2qq_fail.set(cfg.qq.mcFailureMsg or '&c@{player}转述失败')

        self.var_reconnect_enabled.set(cfg.reconnect.enabled)
        _secs = int(cfg.reconnect.delaySeconds or 0)
        if _secs >= 86400 and _secs % 86400 == 0:
            self.var_reconnect_delay.set(str(_secs // 86400)); self.var_reconnect_unit.set('天')
        elif _secs >= 3600 and _secs % 3600 == 0:
            self.var_reconnect_delay.set(str(_secs // 3600)); self.var_reconnect_unit.set('小时')
        elif _secs >= 60 and _secs % 60 == 0:
            self.var_reconnect_delay.set(str(_secs // 60)); self.var_reconnect_unit.set('分钟')
        else:
            self.var_reconnect_delay.set(str(_secs)); self.var_reconnect_unit.set('秒')
        self.var_reconnect_max.set(str(cfg.reconnect.maxAttempts))
        self.var_kick_lobby.set(cfg.reconnect.kickLobbyEnabled)
        self.var_kick_lobby_delay.set(str(cfg.reconnect.kickLobbyDelaySeconds))
        self.reconnect_triggers_text.delete('1.0', 'end')
        if cfg.reconnect.customTriggers:
            self.reconnect_triggers_text.insert('1.0', '\n'.join(cfg.reconnect.customTriggers))

        self.var_proactive_enabled.set(bool(cfg.reconnect.proactiveEnabled))
        _ps = int(cfg.reconnect.proactiveIntervalSeconds or 0)
        if _ps >= 86400 and _ps % 86400 == 0:
            self.var_proactive_value.set(str(_ps // 86400)); self.var_proactive_unit.set('天')
        elif _ps >= 3600 and _ps % 3600 == 0:
            self.var_proactive_value.set(str(_ps // 3600)); self.var_proactive_unit.set('小时')
        elif _ps >= 60 and _ps % 60 == 0:
            self.var_proactive_value.set(str(_ps // 60)); self.var_proactive_unit.set('分钟')
        else:
            self.var_proactive_value.set(str(_ps)); self.var_proactive_unit.set('秒')

        self.var_autolobby_enabled.set(bool(cfg.reconnect.autoLobbyEnabled))
        _al = int(cfg.reconnect.autoLobbyIntervalSeconds or 0)
        if _al >= 3600 and _al % 3600 == 0:
            self.var_autolobby_value.set(str(_al // 3600)); self.var_autolobby_unit.set('小时')
        elif _al >= 60 and _al % 60 == 0:
            self.var_autolobby_value.set(str(_al // 60)); self.var_autolobby_unit.set('分钟')
        else:
            self.var_autolobby_value.set(str(_al)); self.var_autolobby_unit.set('秒')
        self.var_onlinecheck_enabled.set(bool(cfg.reconnect.onlineCheckEnabled))
        _oc = int(cfg.reconnect.onlineCheckIntervalSeconds or 0)
        if _oc >= 3600 and _oc % 3600 == 0:
            self.var_onlinecheck_value.set(str(_oc // 3600)); self.var_onlinecheck_unit.set('小时')
        elif _oc >= 60 and _oc % 60 == 0:
            self.var_onlinecheck_value.set(str(_oc // 60)); self.var_onlinecheck_unit.set('分钟')
        else:
            self.var_onlinecheck_value.set(str(_oc)); self.var_onlinecheck_unit.set('秒')
        self.var_onlinecheck_retry.set(str(cfg.reconnect.onlineReconnectIntervalSeconds))

        self.var_bg_image.set(cfg.appearance.backgroundImage or '')
        self.var_bg_darkness.set(cfg.appearance.backgroundDarkness)
        self.var_text_theme.set(_THEME_VALUE_TO_LABEL.get(cfg.appearance.textTheme, _THEME_AUTO))
        try:
            self.bg_darkness_label.config(text=f'{cfg.appearance.backgroundDarkness}%')
        except Exception:  # noqa: BLE001
            pass

        self.var_book_file.set(cfg.docBook.lastFile or '')
        self.var_book_pagelines.set(str(cfg.docBook.pageLines))
        self.var_book_perbook.set(str(cfg.docBook.pagesPerBook))
        self.var_book_start.set(cfg.docBook.startText or '')
        self.var_book_endtext.set(cfg.docBook.endText or '')
        self.var_book_enddrop.set(bool(cfg.docBook.endDrop))
        self.var_book_autoget.set(bool(cfg.docBook.autoGetBook))
        self.var_book_sign.set(bool(cfg.docBook.signBook))
        self.var_book_title.set(cfg.docBook.titleTemplate or '')

        self.var_song_trigger.set(cfg.song.trigger or '[点歌]')
        self.var_song_stop_word.set(cfg.song.stopWord or '停止')
        self.var_song_reply_enabled.set(bool(cfg.song.replyEnabled))
        self.var_song_post_play_link.set(bool(cfg.song.postPlayLink))
        self.var_song_mod_url.set(cfg.song.modUrl or '')
        self.var_song_reply_text.set(cfg.song.replyText or '')
        self.var_song_reply_cooldown.set(str(cfg.song.replyCooldownSeconds))
        self.var_song_netease_cookie.set(cfg.song.neteaseCookie or '')
        self.var_song_play_text.set(cfg.song.playText or '{player} 点了一首 {music}')
        self.var_song_instant_play.set(bool(getattr(cfg.song, 'instantPlay', True)))
        self.var_song_private_play.set(bool(getattr(cfg.song, 'privatePlay', True)))
        misc = getattr(cfg, 'misc', None)
        if misc is not None:
            self.var_misc_video_enabled.set(bool(misc.videoEnabled))
            src = getattr(misc, 'videoSource', 'anime')
            label = dict(zip(self._VIDEO_SOURCE_KEYS, self._VIDEO_SOURCE_LABELS)).get(
                src, self._VIDEO_SOURCE_LABELS[0])
            self.var_misc_video_source.set(label)
            self.var_misc_video_count.set(str(misc.videoCount))
            self.var_misc_video_cooldown.set(str(misc.videoCooldownSeconds))
            self.var_misc_video_title.set(misc.videoTitle)
            self.var_misc_video_item.set(misc.videoItemText)
            self.var_misc_video_cool.set(misc.videoCoolText)
            self.var_misc_video_fail.set(misc.videoFailText)
            self.var_misc_luck_enabled.set(bool(misc.luckEnabled))
            self.var_misc_luck_max.set(str(misc.luckMax))
            self.var_misc_luck_text.set(misc.luckText)
            self.var_misc_checkin_enabled.set(bool(misc.checkinEnabled))
            self.var_misc_checkin_money_min.set(str(misc.checkinMoneyMin))
            self.var_misc_checkin_money_max.set(str(misc.checkinMoneyMax))
            self.var_misc_checkin_streak_bonus.set(str(misc.checkinStreakBonus))
            self.var_misc_checkin_item.set(misc.checkinItem or '')
            self.var_misc_checkin_item_count.set(str(misc.checkinItemCount))
            self.var_misc_checkin_money_cmd.set(misc.checkinMoneyCmd)
            self.var_misc_checkin_item_cmd.set(misc.checkinItemCmd)
            self.var_misc_checkin_text.set(misc.checkinText)
            self.var_misc_checkin_repeat.set(misc.checkinRepeatText)
            self.var_misc_checkin_off.set(misc.checkinOffText)
            self.var_misc_bili_cookie.set(misc.biliCookie or '')
            self._bili_fav_cur = {'media_id': int(misc.biliFavMediaId or 0),
                                  'name': str(misc.biliFavName or '')}
            self.var_misc_bili_fav.set(str(misc.biliFavName or ''))
            self.var_misc_bili_status.set(
                '已配置：%s' % misc.biliFavName if misc.biliFavName else '（还没读取）')
            self.var_misc_mass_gap.set(f'{float(misc.massGapSeconds):g}')
            self.var_misc_mass_skipself.set(bool(misc.massSkipSelf))
        if hasattr(self, '_netease_status'):
            self._netease_status.set('已登录（会员 Cookie 已保存）'
                                     if (cfg.song.neteaseCookie or '').strip()
                                     else '未登录（VIP 歌只能放 45 秒试听）')

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

    def _gather_config(self) -> bool:
        """把界面里的值收进配置对象并写盘。返回 ``True`` 表示两份都存成功了。

        调用方可以忽略返回值（行为和以前一样）；只有需要提示「已保存」的入口
        才该检查它，否则写盘失败也会弹出成功提示。
        """
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
        ai.serverMessagesInContext = self.var_server_ctx.get()
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

        ai.searchEnabled = self.var_search_enabled.get()
        ai.searchProvider = self.var_search_provider.get().strip() or 'duckduckgo'
        ai.searchApiUrl = self.var_search_url.get().strip()
        ai.searchApiKey = self.var_search_key.get()
        ai.searchMaxResults = self._parse_int(self.var_search_n.get(), 5)
        ai.searchTriggerRegex = self.var_search_regex.get().strip()

        ai.idleChatEnabled = self.var_idle_enabled.get()
        _items = []
        for _rec in self._random_rows:
            _t = _rec['text'].get().strip()
            if not _t:
                continue
            _lo = self._parse_int(_rec['min'].get(), 60)
            _hi = self._parse_int(_rec['max'].get(), max(_lo, 60))
            _items.append({'text': _t, 'min': _lo, 'max': _hi})
        ai.idleChatItems = _items
        ai.idleChatCountMin = self._parse_int(self.var_idle_cmin.get(), 1)
        ai.idleChatCountMax = self._parse_int(self.var_idle_cmax.get(), 1)
        ai.validate()

        cfg.commandBlock.enabled = self.var_cmdblock_enabled.get()
        cfg.commandBlock.commands = [r['cmd'].get().strip() for r in self._cmd_rows
                                     if r['cmd'].get().strip()]
        cfg.commandBlock.message = self.var_cmdblock_msg.get().strip() or '&c已拦截危险命令：/{cmd}'
        cfg.commandBlock.validate()

        cfg.quiz.enabled = self.var_quiz.get()
        cfg.autoeat.enabled = self.var_autoeat.get()

        cfg.healthAlert.enabled = self.var_health_enabled.get()
        cfg.healthAlert.threshold = self._parse_int(self.var_health_threshold.get(), 6)
        cfg.healthAlert.cooldownSeconds = self._parse_int(self.var_health_cooldown.get(), 30)
        cfg.healthAlert.message = (self.var_health_msg.get().strip()
                                   or '&c我血量过低了，快救救我！')
        cfg.healthAlert.validate()

        cfg.verify.enabled = self.var_verify_enabled.get()
        cfg.verify.titleKeyword = self.var_verify_title.get().strip()
        cfg.verify.nameKeyword = self.var_verify_name.get().strip()
        cfg.verify.clickDelayMs = self._parse_int(self.var_verify_delay.get(), 250)
        cfg.verify.validate()

        cfg.logForward.enabled = self.var_logfwd_enabled.get()
        cfg.logForward.format = (self.var_logfwd_format.get().strip()
                                 or '&7Log -> &c{log}')
        _levels = []
        if self.var_logfwd_error.get():
            _levels.append('error')
        if self.var_logfwd_warn.get():
            _levels.append('warn')
        cfg.logForward.levels = _levels or ['error']
        cfg.logForward.validate()

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
        qq.qqToMcEnabled = self.var_qq_to_mc.get()
        qq.framework = ('napcat' if str(self.var_qq_framework.get()).strip().lower().startswith('nap')
                        else 'snowluma')
        # Save the endpoint fields into the framework currently shown in the UI.
        self._store_qq_endpoint_widgets(getattr(self, '_qq_editing_framework', qq.framework))
        qq.napcat.autoConfigNewAccounts = self.var_qq_autoconfig.get()
        qq.groups = [x for x in re.split(r'[,，;\s]+', self.var_qq_groups.get()) if x]
        qq.format = self.var_qq_format.get().strip() or '&7&o[{group}][{name}]：{message}'
        qq.imageAsLink = self.var_qq_image_link.get()
        qq.imageHttpPort = self._parse_int(self.var_qq_img_port.get(), 8765)
        qq.imagePublicBase = self.var_qq_img_base.get().strip()
        qq.autoReconnect = self.var_qq_autoreconnect.get()
        qq.healthCheckSeconds = self._parse_int(self.var_qq_health.get(), 30)
        qq.mcToQqEnabled = self.var_mc2qq_enabled.get()
        qq.mcTrigger = self.var_mc2qq_trigger.get().strip() or '[sentQ]'
        qq.mcTargetGroup = self.var_mc2qq_group.get().strip()
        qq.mcFormat = self.var_mc2qq_format.get().strip() or '[MC] {player}：{message}'
        qq.mcCooldownSeconds = self._parse_int(self.var_mc2qq_cooldown.get(), 3)
        qq.mcSuccessMsg = self.var_mc2qq_ok.get().strip() or '&a@{player}转述成功'
        qq.mcFailureMsg = self.var_mc2qq_fail.get().strip() or '&c@{player}转述失败'
        qq.validate()

        rc = cfg.reconnect
        rc.enabled = self.var_reconnect_enabled.get()
        _units = {'秒': 1, '分钟': 60, '小时': 3600, '天': 86400}
        rc.delaySeconds = max(0, self._parse_int(self.var_reconnect_delay.get(), 5)
                              * _units.get(self.var_reconnect_unit.get(), 1))
        rc.maxAttempts = self._parse_int(self.var_reconnect_max.get(), 5)
        rc.kickLobbyEnabled = self.var_kick_lobby.get()
        rc.kickLobbyDelaySeconds = self._parse_int(self.var_kick_lobby_delay.get(), 2)
        _trigs = [x.strip() for x in
                  self.reconnect_triggers_text.get('1.0', 'end').splitlines() if x.strip()]
        rc.customTriggers = _trigs or ['You were kicked from lobby']
        rc.proactiveEnabled = self.var_proactive_enabled.get()
        _punits = {'秒': 1, '分钟': 60, '小时': 3600, '天': 86400}
        rc.proactiveIntervalSeconds = max(
            1, self._parse_int(self.var_proactive_value.get(), 5)
            * _punits.get(self.var_proactive_unit.get(), 60))
        _aunits = {'秒': 1, '分钟': 60, '小时': 3600}
        rc.autoLobbyEnabled = self.var_autolobby_enabled.get()
        rc.autoLobbyIntervalSeconds = max(
            1, self._parse_int(self.var_autolobby_value.get(), 5)
            * _aunits.get(self.var_autolobby_unit.get(), 60))
        rc.onlineCheckEnabled = self.var_onlinecheck_enabled.get()
        rc.onlineCheckIntervalSeconds = max(
            1, self._parse_int(self.var_onlinecheck_value.get(), 1)
            * _aunits.get(self.var_onlinecheck_unit.get(), 60))
        rc.onlineReconnectIntervalSeconds = self._parse_int(self.var_onlinecheck_retry.get(), 30)
        rc.validate()

        ap = cfg.appearance
        ap.backgroundImage = self.var_bg_image.get().strip()
        ap.backgroundDarkness = int(self.var_bg_darkness.get())
        ap.textTheme = _THEME_LABEL_TO_VALUE.get(self.var_text_theme.get(), 'auto')
        ap.validate()

        db = cfg.docBook
        db.pageChars = docbook.MAX_PAGE_CHARS
        db.pageLines = self._parse_int(self.var_book_pagelines.get(), 14)
        db.pagesPerBook = self._parse_int(self.var_book_perbook.get(), 100)
        db.startText = self.var_book_start.get().strip()
        db.endText = self.var_book_endtext.get().strip()
        db.endDrop = bool(self.var_book_enddrop.get())
        db.autoGetBook = bool(self.var_book_autoget.get())
        db.signBook = bool(self.var_book_sign.get())
        db.titleTemplate = self.var_book_title.get().strip()
        db.lastFile = self.var_book_file.get().strip()
        db.validate()

        song = cfg.song
        song.trigger = self.var_song_trigger.get().strip() or '[点歌]'
        song.stopWord = self.var_song_stop_word.get().strip() or '停止'
        song.replyEnabled = bool(self.var_song_reply_enabled.get())
        song.postPlayLink = bool(self.var_song_post_play_link.get())
        song.modUrl = self.var_song_mod_url.get().strip()
        song.replyText = self.var_song_reply_text.get().strip()
        song.replyCooldownSeconds = self._parse_int(self.var_song_reply_cooldown.get(), 20)
        song.neteaseCookie = self.var_song_netease_cookie.get().strip()
        song.playText = self.var_song_play_text.get().strip() or '{player} 点了一首 {music}'
        song.instantPlay = bool(self.var_song_instant_play.get())
        song.privatePlay = bool(self.var_song_private_play.get())
        song.validate()

        misc = cfg.misc
        misc.videoEnabled = bool(self.var_misc_video_enabled.get())
        label = self.var_misc_video_source.get().strip()
        misc.videoSource = dict(zip(self._VIDEO_SOURCE_LABELS, self._VIDEO_SOURCE_KEYS)).get(
            label, getattr(misc, 'videoSource', 'anime') or 'anime')
        misc.videoCount = self._parse_int(self.var_misc_video_count.get(), 5)
        misc.videoCooldownSeconds = self._parse_int(self.var_misc_video_cooldown.get(), 30)
        misc.videoTitle = self.var_misc_video_title.get().strip()
        misc.videoItemText = self.var_misc_video_item.get().strip()
        misc.videoCoolText = self.var_misc_video_cool.get().strip()
        misc.videoFailText = self.var_misc_video_fail.get().strip()
        misc.luckEnabled = bool(self.var_misc_luck_enabled.get())
        misc.luckMax = self._parse_int(self.var_misc_luck_max.get(), 100)
        misc.luckText = self.var_misc_luck_text.get().strip()
        misc.checkinEnabled = bool(self.var_misc_checkin_enabled.get())
        misc.checkinMoneyMin = self._parse_int(self.var_misc_checkin_money_min.get(), 100)
        misc.checkinMoneyMax = self._parse_int(self.var_misc_checkin_money_max.get(), 300)
        misc.checkinStreakBonus = self._parse_int(self.var_misc_checkin_streak_bonus.get(), 0)
        misc.checkinItem = self.var_misc_checkin_item.get().strip()
        misc.checkinItemCount = self._parse_int(self.var_misc_checkin_item_count.get(), 1)
        misc.checkinMoneyCmd = self.var_misc_checkin_money_cmd.get().strip()
        misc.checkinItemCmd = self.var_misc_checkin_item_cmd.get().strip()
        misc.checkinText = self.var_misc_checkin_text.get().strip()
        misc.checkinRepeatText = self.var_misc_checkin_repeat.get().strip()
        misc.checkinOffText = self.var_misc_checkin_off.get().strip()
        misc.biliCookie = self.var_misc_bili_cookie.get().strip()
        sel = (self.var_misc_bili_fav.get() or '').strip()
        folder = self._bili_fav_map.get(sel) if sel else None
        if folder:
            misc.biliFavMediaId = int(folder.get('media_id') or 0)
            misc.biliFavName = str(folder.get('title') or '')
        elif sel.isdigit():
            misc.biliFavMediaId = int(sel)
            misc.biliFavName = ''
        else:                                   # 没重新读收藏夹：保留原来配的那个
            misc.biliFavMediaId = int(self._bili_fav_cur.get('media_id') or 0)
            misc.biliFavName = str(self._bili_fav_cur.get('name') or '')
        misc.massGapSeconds = self._parse_float(self.var_misc_mass_gap.get(), 0.5)
        misc.massSkipSelf = bool(self.var_misc_mass_skipself.get())
        misc.validate()

        ok_cfg = cfg.save()
        ok_ai = ai.save()
        return bool(ok_cfg and ok_ai)

    def _on_save(self):
        saved = self._gather_config()
        if not saved:
            # 写盘失败时原文件已被保留（原子替换），但界面必须如实报错，
            # 不能弹「设置已保存」——用户会以为配置生效了，实际还是旧的。
            self._append_log('设置保存失败：原配置未被覆盖，请检查磁盘空间或文件占用后重试。',
                             'error')
            messagebox.showerror('aafm AI 玩家',
                                 '设置保存失败：原配置未被覆盖。\n'
                                 '请检查磁盘空间，或确认配置文件没有被其他程序占用，然后重试。')
            return
        self.controller.sync_roster_runtime()
        # 保存后立刻把新配置套到正在运行的 AI（重建 Provider、作废旧请求），
        # 否则界面显示新模型、后台仍用旧模型（#18）。
        self.controller.reload_ai_player()
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
        enabled = bool(self.var_qq_enabled.get())
        try:
            self._gather_config()
        except Exception as e:  # noqa: BLE001
            self._append_log('保存设置出错（已忽略，仍切换 QQ）: ' + str(e), 'warn')
        self.controller.set_qq_enabled(enabled)
        self._append_log('QQ 转述已' + ('启用' if enabled else '禁用') + '。', 'info')

    def _on_qq_to_mc(self):
        try:
            self._gather_config()
        except Exception as e:  # noqa: BLE001
            self._append_log('保存设置出错: ' + str(e), 'warn')
        on = bool(self.var_qq_to_mc.get())
        self._append_log('QQ → MC 已' + ('启用' if on else '禁用')
                         + '（不会断开 QQ 连接，MC → QQ 不受影响）。', 'info')

    def _load_qq_endpoint_widgets(self, fw: str, auto_token: bool = False) -> None:
        """Show the given framework's endpoint settings in the widgets."""
        qq = self.controller.config.qq
        ep = qq.napcat if fw == 'napcat' else qq.snowluma
        self.var_qq_mode.set(ep.mode if ep.mode in ('forward', 'reverse') else 'forward')
        url = ep.url or ''
        token = ep.accessToken or ''
        # SnowLuma: prefer the live port/token from its config (per account), so
        # switching accounts never leaves a stale token that answers 401.
        if fw == 'snowluma':
            try:
                det = self.controller.snowluma_ws_auth()
            except Exception:  # noqa: BLE001
                det = None
            if det:
                host, port, det_token = det
                if det_token:
                    token = det_token
                if (auto_token or not url.strip()):
                    url = url.strip() or f'ws://{host}:{port}'
        self.var_qq_url.set(url)
        self.var_qq_token.set(token)
        self.var_qq_listen_host.set(ep.listenHost or '0.0.0.0')
        self.var_qq_listen_port.set(str(ep.listenPort))
        self.var_qq_launcher.set(ep.launcherPath or '')
        try:
            self.qq_launcher_label.config(
                text='启动路径（SnowLuma 目录）' if fw == 'snowluma'
                else '启动路径（NapCat 目录）')
        except Exception:  # noqa: BLE001
            pass

    def _store_qq_endpoint_widgets(self, fw: str) -> None:
        """Save the widgets into the given framework's endpoint section."""
        qq = self.controller.config.qq
        ep = qq.napcat if fw == 'napcat' else qq.snowluma
        ep.mode = self.var_qq_mode.get().strip() or 'forward'
        ep.url = self.var_qq_url.get().strip()
        ep.accessToken = self.var_qq_token.get().strip()
        ep.listenHost = self.var_qq_listen_host.get().strip() or '0.0.0.0'
        ep.listenPort = self._parse_int(self.var_qq_listen_port.get(), 3002)
        ep.launcherPath = self.var_qq_launcher.get().strip()
        ep.validate()

    def _on_qq_framework_changed(self):
        # Persist the current framework's edits, then show the new one's values.
        self._store_qq_endpoint_widgets(getattr(self, '_qq_editing_framework', 'snowluma'))
        fw = ('napcat' if str(self.var_qq_framework.get()).strip().lower().startswith('nap')
              else 'snowluma')
        self._qq_editing_framework = fw
        self.controller.config.qq.framework = fw
        self._load_qq_endpoint_widgets(fw)
        try:
            self.controller.config.save()
        except Exception:  # noqa: BLE001
            pass
        self._append_log(
            f'QQ 框架已切换为 {self.var_qq_framework.get()}（该框架的路径/地址/Token 独立保存）。', 'info')

    def _on_qq_launch_framework(self):
        self._gather_config()
        self.controller.start_qq_framework()

    def _on_qq_quick_login(self):
        self._gather_config()
        if self.controller.config.qq.framework != 'napcat':
            messagebox.showinfo(
                'aafm',
                '当前启动框架为 SnowLuma，不需要「快速登录」。\n'
                '请在 SnowLuma 的 WebUI 里完成 QQ 登录与 OneBot 连接配置。')
            return
        choices = self.controller.qq_account_choices()  # [(uin, label), ...]
        labels = [label for _, label in choices]
        last = (self.controller.config.qq.lastLoginUin or '').strip()

        dlg = tk.Toplevel(self.root)
        dlg.title('快速登录 QQ')
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text='选择要快速登录的 QQ 号（★ 为上次登录）：').pack(
            anchor='w', padx=12, pady=(12, 4))
        lb = tk.Listbox(dlg, height=8, width=34)
        for label in labels:
            lb.insert('end', label)
        lb.pack(padx=12, fill='x')

        def uin_from_list():
            sel = lb.curselection()
            if not sel:
                return ''
            return choices[sel[0]][0]

        last_idx = next((i for i, (u, _) in enumerate(choices) if u == last), None)
        if last_idx is not None:
            lb.selection_set(last_idx)
            lb.see(last_idx)
        elif choices:
            lb.selection_set(0)

        ttk.Label(dlg, text='或手动输入 QQ 号：').pack(anchor='w', padx=12, pady=(8, 2))
        var = tk.StringVar(value=last)
        entry = ttk.Entry(dlg, textvariable=var, width=34)
        entry.pack(padx=12, fill='x')

        def on_select(_e=None):
            u = uin_from_list()
            if u:
                var.set(u)
        lb.bind('<<ListboxSelect>>', on_select)

        def confirm(_e=None):
            uin = var.get().strip() or uin_from_list()
            if not uin:
                messagebox.showwarning('快速登录', '请选择或输入一个 QQ 号。', parent=dlg)
                return
            dlg.destroy()
            self.controller.start_napcat(uin)
            self._append_log(f'已请求快速登录 QQ {uin}（若会话失效会自动转为扫码）。', 'info')

        btns = ttk.Frame(dlg)
        btns.pack(fill='x', padx=12, pady=12)
        ttk.Button(btns, text='快速登录', command=confirm).pack(side='right')
        ttk.Button(btns, text='取消', command=dlg.destroy).pack(side='right', padx=6)
        entry.bind('<Return>', confirm)
        entry.focus_set()

        dlg.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
            dlg.geometry(f'+{max(0, x)}+{max(0, y)}')
        except Exception:  # noqa: BLE001
            pass

    def _on_qq_start(self):
        self.var_qq_enabled.set(True)
        self._gather_config()
        self.controller.start_qq()
        self._append_log('正在连接 QQ 网关…', 'info')

    def _on_qq_reconnect(self):
        self.var_qq_enabled.set(True)
        self._gather_config()
        self.controller.restart_qq()
        self._append_log('正在重连 QQ 网关…', 'info')

    def _on_qq_stop(self):
        self.var_qq_enabled.set(False)
        try:
            self.controller.config.qq.enabled = False
            self.controller.config.save()
        except Exception:  # noqa: BLE001
            pass
        self.controller.stop_qq()
        self._append_log('已断开 QQ 转述。', 'info')

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
        self._from_backend = True
        try:
            for ev in self.controller.drain_ui():
                self._handle_ui_event(ev)
        finally:
            self._from_backend = False
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
            elif state == 'restricted':
                text, color = 'QQ 受限/离线（已停止重连）', '#ff6b6b'
            else:
                text, color = 'QQ 未连接', '#999999'
            self.qq_status_label.config(text=text, foreground=color)
        elif kind == 'reloginNeeded':
            self._append_log(f'LittleSkin 登录失效: {ev.get("message", "")}', 'error')
            self._start_relogin()
        elif kind == 'bookProgress':
            try:
                self.book_progress_label.config(
                    text=f'进度：已写到第 {ev.get("nextPage", 0)}/{ev.get("totalPages", 0)} 页，'
                         f'下一本第 {ev.get("nextBook", 1)} 本（本次 {ev.get("done", 0)}/{ev.get("total", 0)} 本）')
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _log_tag(level):
        return {'error': 'error', 'ai': 'ai', 'quiz': 'quiz', 'sent': 'sent',
                'warn': 'warn', 'msa': 'msa', 'qq': 'qq', 'song': 'song',
                'server': 'server'}.get(level, 'info')

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
