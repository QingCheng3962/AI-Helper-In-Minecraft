"""CustomTkinter 版主界面（新的默认入口）。

**CustomTkinter** 主界面：功能已与原 tkinter 界面同步（v0.3.6-26.11.14 起），
安装包里的 .exe 默认就是它，不再需要 --legacy 才能用全功能：

- 设置：服务器地址/端口/版本、登录方式、机器人名字、连接/断开、AI 开关、发聊天
- AI 配置：提供方 / 地址 / 密钥 / 模型 / 温度 / 最大 token / 联网搜索 / 文生图
- 玩家配置：系统提示词 / 触发正则 / 冷却 / 自动·定时回复 / 上下文 / 黑名单
- 点歌：点歌（含倍速）、停止、一起听名单、当前播放/队列
- 文档转书：文档 → 游戏内书与笔（分页预览 / 成书署名 / 暂停停止续写清空）
- 玩家资料库：xlsx/csv 资料表、性格研究、别名分析
- QQ 转述：SnowLuma / NapCat 网关（启动/连接/重连/断开、双向转发）
- 重连·踢出：自动重连、被踢后 /lobby、主动重连、在线检测
- 工具：端口扫描（常见/自定义/全端口 + Minecraft status 识别）、视频推荐、幸运值、签到、群发
- 日志：实时日志 + 自己往游戏里发消息
- 关于：支持与工单、开发者博客、仓库链接、版本信息

原 tkinter 界面仍保留（python main.py --legacy），两边共用同一个 Controller 与 data.db。
"""
from __future__ import annotations

import os
import re
import threading
import time
from tkinter import filedialog
from typing import Any, Dict, List

try:
    import customtkinter as ctk
except Exception as exc:  # noqa: BLE001
    raise SystemExit('缺少 customtkinter：pip install customtkinter（或 python main.py --legacy 用旧界面）') from exc

from tkinter import messagebox

from . import docbook, links, portscan, storage
from .config import PromptTemplateManager
from .controller import Controller

APP_TITLE = 'aafm AI 玩家'
VERSION = 'v0.3.6-26.11.14'

LEVEL_COLORS = {
    'error': '#ff6b6b',
    'warn': '#ffb74d',
    'song': '#4dd0e1',
    'server': '#90caf9',
    'chat': '#e0e0e0',
    'private': '#ce93d8',
    'info': '#b0bec5',
}


def _fmt_time() -> str:
    return time.strftime('[%H:%M:%S] ')


class CtkApp:
    """CustomTkinter 主窗口。"""

    def __init__(self) -> None:
        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')
        self.root = ctk.CTk()
        self.root.title(APP_TITLE + ' ' + VERSION)
        self.root.geometry('980x680')
        self.root.minsize(880, 600)
        self.controller = Controller()
        self._closed = False
        self._scan_stop = False
        self._scanning = False
        self._last_song_text = ''
        self._last_scan_mc: List[Dict[str, Any]] = []

        self._build_topbar()
        self._build_tabs()
        self._load_config()
        try:
            ctk.set_appearance_mode('dark')
        except Exception:  # noqa: BLE001
            pass
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.after(200, self._poll)

    # ------------------------------------------------------------------
    def _build_topbar(self) -> None:
        bar = ctk.CTkFrame(self.root, height=54, corner_radius=0)
        bar.pack(fill='x', side='top')
        self.status_label = ctk.CTkLabel(bar, text='未连接', anchor='w',
                                         font=ctk.CTkFont(size=14, weight='bold'))
        self.status_label.pack(side='left', padx=16, pady=12)
        ctk.CTkButton(bar, text='断开', width=82, fg_color='#5c6bc0', hover_color='#3f51b5',
                      command=self._on_disconnect).pack(side='right', padx=(6, 16), pady=10)
        ctk.CTkButton(bar, text='连接', width=82, fg_color='#2e7d32', hover_color='#1b5e20',
                      command=self._on_connect).pack(side='right', padx=6, pady=10)
        ctk.CTkButton(bar, text='保存设置', width=100,
                      command=self._on_save).pack(side='right', padx=6, pady=10)

    # ------------------------------------------------------------------
    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(self.root)
        self.tabs.pack(fill='both', expand=True, padx=12, pady=(10, 12))
        for name in ('设置', 'AI 配置', '玩家配置', '点歌', '文档转书',
                     '资料库', 'QQ 转述', '重连', '工具', '日志', '关于'):
            self.tabs.add(name)
        self._build_settings_tab(self.tabs.tab('设置'))
        self._build_ai_tab(self.tabs.tab('AI 配置'))
        self._build_player_tab(self.tabs.tab('玩家配置'))
        self._build_song_tab(self.tabs.tab('点歌'))
        self._build_docbook_tab(self.tabs.tab('文档转书'))
        self._build_roster_tab(self.tabs.tab('资料库'))
        self._build_qq_tab(self.tabs.tab('QQ 转述'))
        self._build_reconnect_tab(self.tabs.tab('重连'))
        self._build_tools_tab(self.tabs.tab('工具'))
        self._build_log_tab(self.tabs.tab('日志'))
        self._build_about_tab(self.tabs.tab('关于'))

    # ---------------------------- 设置 ----------------------------
    def _build_settings_tab(self, tab) -> None:
        frame = ctk.CTkFrame(tab)
        frame.pack(fill='x', padx=8, pady=8)

        self.var_host = ctk.StringVar(value='localhost')
        self.var_port = ctk.StringVar(value='25565')
        self.var_version = ctk.StringVar(value='1.20')
        self.var_auth = ctk.StringVar(value='offline')
        self.var_username = ctk.StringVar(value='AI_Bot')

        rows = [
            ('服务器地址', self.var_host, 26),
            ('端口', self.var_port, 10),
            ('游戏版本', self.var_version, 12),
            ('机器人名字', self.var_username, 20),
        ]
        for index, (label, var, width) in enumerate(rows):
            ctk.CTkLabel(frame, text=label, width=90, anchor='e').grid(
                row=index, column=0, padx=(12, 8), pady=6, sticky='e')
            ctk.CTkEntry(frame, textvariable=var, width=width * 9).grid(
                row=index, column=1, padx=8, pady=6, sticky='w')
            if label == '端口':
                ctk.CTkButton(frame, text='端口扫描', width=100,
                              command=lambda: self.tabs.set('工具')).grid(
                    row=index, column=2, padx=8, pady=6, sticky='w')

        ctk.CTkLabel(frame, text='登录方式', width=90, anchor='e').grid(
            row=4, column=0, padx=(12, 8), pady=6, sticky='e')
        ctk.CTkOptionMenu(frame, values=['offline', 'littleskin', 'microsoft'],
                          variable=self.var_auth, width=180).grid(
            row=4, column=1, padx=8, pady=6, sticky='w')
        ctk.CTkLabel(frame, text='offline=离线 / littleskin=皮肤站 / microsoft=正版',
                     text_color='#90a4ae').grid(row=4, column=2, columnspan=2,
                                                padx=8, pady=6, sticky='w')

        ai_frame = ctk.CTkFrame(tab)
        ai_frame.pack(fill='x', padx=8, pady=(0, 8))
        self.var_ai = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(ai_frame, text='启用 AI 回复',
                        variable=self.var_ai, command=self._on_ai_toggle).pack(
            side='left', padx=12, pady=12)
        ctk.CTkButton(ai_frame, text='清空 AI 上下文', width=130,
                      command=lambda: self.controller.clear_ai_context()).pack(
            side='left', padx=8, pady=12)
        ctk.CTkButton(ai_frame, text='打开旧界面（兼容）', width=160,
                      fg_color='#455a64', hover_color='#37474f',
                      command=self._open_legacy).pack(side='right', padx=12, pady=12)

        look = ctk.CTkFrame(tab)
        look.pack(fill='x', padx=8, pady=(0, 8))
        ctk.CTkLabel(look, text='界面主题', width=90, anchor='e').pack(
            side='left', padx=(12, 8), pady=12)
        self.var_theme = ctk.StringVar(value='深色')
        ctk.CTkOptionMenu(look, values=['深色', '浅色', '跟随系统'],
                          variable=self.var_theme, width=140,
                          command=self._on_theme_changed).pack(side='left', padx=8, pady=12)
        ctk.CTkButton(look, text='打开旧界面（兼容）', width=160,
                      fg_color='#455a64', hover_color='#37474f',
                      command=self._open_legacy).pack(side='right', padx=12, pady=12)

        chat_frame = ctk.CTkFrame(tab)
        chat_frame.pack(fill='x', padx=8, pady=(0, 8))
        ctk.CTkLabel(chat_frame, text='让机器人说话', width=90, anchor='e').pack(
            side='left', padx=(12, 8), pady=12)
        self.var_chat = ctk.StringVar(value='')
        entry = ctk.CTkEntry(chat_frame, textvariable=self.var_chat,
                             placeholder_text='输入要发到游戏公屏的一句话，回车发送')
        entry.pack(side='left', fill='x', expand=True, padx=8, pady=12)
        entry.bind('<Return>', lambda _e: self._on_send_chat())
        ctk.CTkButton(chat_frame, text='发送', width=80,
                      command=self._on_send_chat).pack(side='left', padx=(0, 12), pady=12)

    # ---------------------------- 点歌 ----------------------------
    def _build_song_tab(self, tab) -> None:
        top = ctk.CTkFrame(tab)
        top.pack(fill='x', padx=8, pady=8)
        self.song_now_label = ctk.CTkLabel(top, text='当前没有在放歌', anchor='w',
                                           font=ctk.CTkFont(size=15, weight='bold'))
        self.song_now_label.pack(side='left', padx=12, pady=12)

        line = ctk.CTkFrame(tab)
        line.pack(fill='x', padx=8, pady=(0, 8))
        self.var_song = ctk.StringVar(value='')
        song_entry = ctk.CTkEntry(line, textvariable=self.var_song,
                                  placeholder_text='歌名 / 网易云链接 / 歌曲ID')
        song_entry.pack(side='left', fill='x', expand=True, padx=(12, 8), pady=12)
        song_entry.bind('<Return>', lambda _e: self._on_play_song())
        self.var_speed = ctk.StringVar(value='1.0')
        ctk.CTkOptionMenu(line, values=['0.5', '0.75', '1.0', '1.5', '2.0'],
                          variable=self.var_speed, width=90).pack(side='left', padx=6, pady=12)
        ctk.CTkButton(line, text='点歌', width=84, command=self._on_play_song).pack(
            side='left', padx=6, pady=12)
        ctk.CTkButton(line, text='停止', width=84, fg_color='#b71c1c', hover_color='#7f0000',
                      command=self._on_song_stop).pack(side='left', padx=(6, 12), pady=12)

        line2 = ctk.CTkFrame(tab)
        line2.pack(fill='x', padx=8, pady=(0, 8))
        ctk.CTkButton(line2, text='一起听名单', width=110,
                      command=self._on_listen_list).pack(side='left', padx=12, pady=12)
        ctk.CTkLabel(line2, text='（在游戏里对某个玩家发 点歌触发词+一起听 玩家 就能跟听）',
                     text_color='#90a4ae').pack(side='left', padx=6, pady=12)
        ctk.CTkButton(line2, text='点歌帮助（私聊我）', width=150,
                      command=self._on_song_help).pack(side='right', padx=12, pady=12)

        ctk.CTkLabel(tab, text='机器人播放队列：', anchor='w').pack(fill='x', padx=20, pady=(6, 0))
        self.song_queue_box = ctk.CTkTextbox(tab, height=260)
        self.song_queue_box.pack(fill='both', expand=True, padx=12, pady=8)
        self.song_queue_box.configure(state='disabled')

    # ---------------------------- 工具 ----------------------------
    def _build_tools_tab(self, tab) -> None:
        scan = ctk.CTkFrame(tab)
        scan.pack(fill='x', padx=8, pady=8)
        ctk.CTkLabel(scan, text='端口扫描', anchor='w',
                     font=ctk.CTkFont(size=16, weight='bold')).grid(
            row=0, column=0, columnspan=6, padx=12, pady=(10, 2), sticky='w')
        self.var_scan_host = ctk.StringVar(value='')
        self.var_scan_mode = ctk.StringVar(value='常见端口')
        self.var_scan_start = ctk.StringVar(value='25500')
        self.var_scan_end = ctk.StringVar(value='25600')
        ctk.CTkLabel(scan, text='地址', width=50, anchor='e').grid(
            row=1, column=0, padx=(12, 6), pady=6, sticky='e')
        ctk.CTkEntry(scan, textvariable=self.var_scan_host, width=240,
                     placeholder_text='域名或 IP（空=用设置里的地址）').grid(
            row=1, column=1, padx=6, pady=6, sticky='w')
        ctk.CTkOptionMenu(scan, values=['常见端口', '自定义范围', '全端口(慢)'],
                          variable=self.var_scan_mode, width=120).grid(
            row=1, column=2, padx=6, pady=6, sticky='w')
        ctk.CTkEntry(scan, textvariable=self.var_scan_start, width=90).grid(
            row=1, column=3, padx=4, pady=6, sticky='w')
        ctk.CTkEntry(scan, textvariable=self.var_scan_end, width=90).grid(
            row=1, column=4, padx=4, pady=6, sticky='w')
        ctk.CTkButton(scan, text='开始扫描', width=100, command=self._on_scan).grid(
            row=1, column=5, padx=(6, 12), pady=6, sticky='w')
        self.scan_status = ctk.CTkLabel(scan, text='', anchor='w', text_color='#90a4ae')
        self.scan_status.grid(row=2, column=0, columnspan=6, padx=12, pady=(0, 4), sticky='w')
        self.scan_box = ctk.CTkTextbox(scan, height=190, font=ctk.CTkFont(family='Consolas', size=12))
        self.scan_box.grid(row=3, column=0, columnspan=6, padx=12, pady=(0, 8), sticky='ew')
        self.scan_box.configure(state='disabled')
        buttons = ctk.CTkFrame(scan)
        buttons.grid(row=4, column=0, columnspan=6, padx=12, pady=(0, 10), sticky='w')
        ctk.CTkButton(buttons, text='把扫到的 MC 端口填到设置里', width=230,
                      command=self._apply_scanned_port).pack(side='left', padx=(0, 8))
        ctk.CTkButton(buttons, text='停止扫描', width=100, fg_color='#5c6bc0',
                      command=self._stop_scan).pack(side='left')

        extra = ctk.CTkFrame(tab)
        extra.pack(fill='x', padx=8, pady=8)
        ctk.CTkLabel(extra, text='常用指令（发给机器人，走 AI 提示词）',
                     font=ctk.CTkFont(size=15, weight='bold')).pack(anchor='w', padx=12, pady=(10, 4))
        row = ctk.CTkFrame(extra)
        row.pack(fill='x', padx=8, pady=4)
        for text, handler in (
            ('视频推荐', self._on_video),
            ('今日幸运值', self._on_lucky),
            ('签到', self._on_checkin),
            ('谁在听歌', self._on_listen_list),
        ):
            ctk.CTkButton(row, text=text, width=110, command=handler).pack(
                side='left', padx=6, pady=6)
        mass = ctk.CTkFrame(extra)
        mass.pack(fill='x', padx=8, pady=(4, 6))
        self.var_mass = ctk.StringVar(value='')
        ctk.CTkEntry(mass, textvariable=self.var_mass,
                     placeholder_text='群发私聊内容（逐个 /msg 在线玩家）').pack(
            side='left', fill='x', expand=True, padx=(6, 8), pady=6)
        ctk.CTkButton(mass, text='群发', width=84, command=self._on_mass).pack(
            side='left', padx=(0, 6), pady=6)

        # ---- 杂项：视频推荐 / 幸运值 / 签到 / 小票机 ----
        self.var_misc_video_enabled = ctk.BooleanVar(value=True)
        self.var_misc_video_source = ctk.StringVar(value='番剧追番榜')
        self.var_misc_video_count = ctk.StringVar(value='3')
        self.var_misc_video_cooldown = ctk.StringVar(value='30')
        self.var_misc_luck_enabled = ctk.BooleanVar(value=True)
        self.var_misc_checkin_enabled = ctk.BooleanVar(value=True)
        self.var_mass_gap = ctk.StringVar(value='0.5')
        self.var_receipt_enabled = ctk.BooleanVar(value=False)
        self.var_receipt_printer = ctk.StringVar(value='')
        self.var_receipt_paper = ctk.StringVar(value='58')
        self.var_receipt_maxper = ctk.StringVar(value='20')
        self.var_receipt_dryrun = ctk.BooleanVar(value=False)

        misc = self._section(tab, '杂项：视频推荐 / 幸运值 / 签到 / 小票机')
        row = ctk.CTkFrame(misc)
        row.pack(fill='x', padx=12, pady=2)
        ctk.CTkLabel(row, text='视频来源', width=96, anchor='e').pack(side='left')
        ctk.CTkOptionMenu(row, values=['番剧追番榜', 'B 站首页推荐', 'B 站首页热门',
                                       'B 站排行榜', 'B 站收藏夹'],
                          variable=self.var_misc_video_source, width=150).pack(
            side='left', padx=8, pady=2)
        self._check(misc, '启用视频推荐', self.var_misc_video_enabled)
        self._field(misc, '每次推荐条数', self.var_misc_video_count, 90)
        self._field(misc, '推荐冷却 (秒)', self.var_misc_video_cooldown, 90)
        self._check(misc, '启用今日幸运值', self.var_misc_luck_enabled)
        self._check(misc, '启用每日签到', self.var_misc_checkin_enabled)
        self._field(misc, '群发间隔 (秒)', self.var_mass_gap, 90)
        ctk.CTkLabel(misc, text='— Minecraft 聊天小票机（ESC/POS）—',
                     text_color='#90a4ae').pack(anchor='w', padx=16, pady=(8, 0))
        self._check(misc, '打印玩家聊天到小票机', self.var_receipt_enabled)
        self._field(misc, '打印机名', self.var_receipt_printer, 240)
        self._field(misc, '纸宽 (58/80)', self.var_receipt_paper, 90)
        self._field(misc, '每分钟上限', self.var_receipt_maxper, 90)
        self._check(misc, '仅生成预览不真打印（dry-run）', self.var_receipt_dryrun)

    # ---------------------------- 日志 ----------------------------
    def _build_log_tab(self, tab) -> None:
        bar = ctk.CTkFrame(tab)
        bar.pack(fill='x', padx=8, pady=(8, 0))
        ctk.CTkButton(bar, text='清空日志', width=100,
                      command=self._clear_log).pack(side='left', padx=8, pady=8)
        ctk.CTkLabel(bar, text='（日志会按天自动保存到 logs/）', text_color='#90a4ae').pack(
            side='left', padx=4, pady=8)
        self.log_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family='Consolas', size=12))
        self.log_box.pack(fill='both', expand=True, padx=12, pady=8)
        self.log_box.configure(state='disabled')
        send = ctk.CTkFrame(tab)
        send.pack(fill='x', padx=8, pady=(0, 8))
        self.var_chat2 = ctk.StringVar(value='')
        entry = ctk.CTkEntry(send, textvariable=self.var_chat2, placeholder_text='发到游戏公屏')
        entry.pack(side='left', fill='x', expand=True, padx=(12, 8), pady=10)
        entry.bind('<Return>', lambda _e: self._on_send_chat(self.var_chat2))
        ctk.CTkButton(send, text='发送', width=84,
                      command=lambda: self._on_send_chat(self.var_chat2)).pack(
            side='left', padx=(0, 12), pady=10)

    # ---------------------------- 关于 ----------------------------
    def _build_about_tab(self, tab) -> None:
        frame = ctk.CTkFrame(tab)
        frame.pack(fill='both', expand=True, padx=8, pady=8)
        ctk.CTkLabel(frame, text=APP_TITLE + ' ' + VERSION, anchor='w',
                     font=ctk.CTkFont(size=20, weight='bold')).pack(
            anchor='w', padx=16, pady=(16, 4))
        ctk.CTkLabel(frame, text='无客户端的 Minecraft AI 玩家 + 游戏内点歌 Mod + QQ 转述',
                     text_color='#90a4ae').pack(anchor='w', padx=16, pady=(0, 16))
        ctk.CTkButton(frame, text='支持与工单  ' + links.SUPPORT_URL, width=420,
                      command=lambda: self._open_link(links.SUPPORT_URL)).pack(
            anchor='w', padx=16, pady=6)
        ctk.CTkButton(frame, text='开发者博客  ' + links.BLOG_URL, width=420,
                      fg_color='#5c6bc0', hover_color='#3f51b5',
                      command=lambda: self._open_link(links.BLOG_URL)).pack(
            anchor='w', padx=16, pady=6)
        ctk.CTkButton(frame, text='GitHub 仓库', width=420,
                      fg_color='#37474f', hover_color='#263238',
                      command=lambda: self._open_link(links.REPO_URL)).pack(
            anchor='w', padx=16, pady=6)
        ctk.CTkLabel(frame, text=('新界面已包含全部功能（AI/玩家配置、文档转书、玩家资料库、QQ 转述、重连踢出、端口扫描等）；'
                                  '旧 tkinter 界面仍可用 python main.py --legacy 打开，两边共用同一份 data.db 数据。'),
                     justify='left', text_color='#78909c').pack(anchor='w', padx=16, pady=(20, 16))

    # ------------------------------------------------------------------
    # 通用小工具
    # ------------------------------------------------------------------
    def _section(self, parent, title: str):
        frame = ctk.CTkFrame(parent)
        frame.pack(fill='x', padx=8, pady=6)
        ctk.CTkLabel(frame, text=title, anchor='w',
                     font=ctk.CTkFont(size=14, weight='bold')).pack(
            anchor='w', padx=12, pady=(8, 2))
        return frame

    def _field(self, parent, label: str, var, width: int = 260, show=None):
        row = ctk.CTkFrame(parent)
        row.pack(fill='x', padx=12, pady=2)
        ctk.CTkLabel(row, text=label, width=96, anchor='e').pack(side='left')
        ctk.CTkEntry(row, textvariable=var, width=width, show=show).pack(
            side='left', padx=8, pady=2)
        return row

    def _check(self, parent, text: str, var):
        box = ctk.CTkCheckBox(parent, text=text, variable=var)
        box.pack(anchor='w', padx=14, pady=3)
        return box

    def _save_all(self, message: str = '设置已保存。') -> None:
        if self._gather_config():
            self._append_log(message)

    # ------------------------------------------------------------------
    # AI 配置
    # ------------------------------------------------------------------
    def _build_ai_tab(self, tab) -> None:
        self.var_ai_provider = ctk.StringVar(value='openai')
        self.var_ai_base = ctk.StringVar(value='')
        self.var_ai_key = ctk.StringVar(value='')
        self.var_ai_model = ctk.StringVar(value='gpt-4o-mini')
        self.var_ai_temp = ctk.StringVar(value='0.7')
        self.var_ai_maxtok = ctk.StringVar(value='1024')
        self.var_ai_timeout = ctk.StringVar(value='30')
        self.var_ai_search = ctk.BooleanVar(value=False)
        self.var_ai_image = ctk.BooleanVar(value=False)

        api = self._section(tab, '模型 API（OpenAI 兼容，也支持 Anthropic）')
        row = ctk.CTkFrame(api)
        row.pack(fill='x', padx=12, pady=2)
        ctk.CTkLabel(row, text='提供方', width=96, anchor='e').pack(side='left')
        ctk.CTkOptionMenu(row, values=['openai', 'deepseek', 'moonshot', 'ollama',
                                       'anthropic', 'custom'],
                          variable=self.var_ai_provider, width=160).pack(
            side='left', padx=8, pady=2)
        self._field(api, 'API 地址', self.var_ai_base, 360)
        self._field(api, 'API Key', self.var_ai_key, 360, show='*')
        self._field(api, '模型', self.var_ai_model, 240)
        self._field(api, '温度 (0-2)', self.var_ai_temp, 100)
        self._field(api, '最大 tokens', self.var_ai_maxtok, 100)
        self._field(api, '超时 (秒)', self.var_ai_timeout, 100)
        ctk.CTkLabel(api, text='地址留空 = 用提供方默认；API Key 只存在本机 config/mcai_chat.json',
                     text_color='#90a4ae').pack(anchor='w', padx=16, pady=(2, 8))

        extra = self._section(tab, '扩展能力')
        self._check(extra, '联网搜索（遇到需要新鲜信息的问题时自动搜）', self.var_ai_search)
        self._check(extra, '文生图（把图片 URL 发到聊天）', self.var_ai_image)

        buttons = ctk.CTkFrame(tab)
        buttons.pack(fill='x', padx=8, pady=8)
        ctk.CTkButton(buttons, text='保存 AI 配置', width=130,
                      command=lambda: self._save_all('AI 配置已保存。')).pack(
            side='left', padx=12, pady=8)
        ctk.CTkButton(buttons, text='测试连接', width=110,
                      fg_color='#455a64', hover_color='#37474f',
                      command=self._on_test_ai).pack(side='left', padx=6, pady=8)

    def _on_test_ai(self) -> None:
        self._gather_config()
        try:
            from .llm import LLMClient  # noqa: F401
        except Exception:  # noqa: BLE001
            pass
        self._append_log('已保存配置。连接服务器后可用「让机器人说话」验证模型是否正常。', 'info')

    # ------------------------------------------------------------------
    # 玩家配置
    # ------------------------------------------------------------------
    def _build_player_tab(self, tab) -> None:
        self.var_trigger = ctk.StringVar(value='')
        self.var_cooldown = ctk.StringVar(value='10')
        self.var_ctx_enabled = ctk.BooleanVar(value=True)
        self.var_ctx_len = ctk.StringVar(value='20')
        self.var_ctx_server = ctk.BooleanVar(value=True)
        self.var_auto_reply = ctk.BooleanVar(value=False)
        self.var_schedule = ctk.BooleanVar(value=False)
        self.var_schedule_interval = ctk.StringVar(value='30')
        self.var_ai_enabled_cfg = ctk.BooleanVar(value=False)
        self.var_blocked_regex = ctk.StringVar(value='')

        prompt = self._section(tab, '系统提示词（决定 AI 的说话风格与人设）')
        self.prompt_box = ctk.CTkTextbox(prompt, height=170)
        self.prompt_box.pack(fill='x', padx=12, pady=(2, 10))

        tpl = ctk.CTkFrame(tab)
        tpl.pack(fill='x', padx=8, pady=(0, 6))
        self.var_template = ctk.StringVar(value='')
        ctk.CTkLabel(tpl, text='提示词模板', width=96, anchor='e').pack(side='left', padx=(12, 4))
        self.template_menu = ctk.CTkOptionMenu(tpl, values=['（无）'], width=180,
                                               variable=self.var_template)
        self.template_menu.pack(side='left', padx=6, pady=6)
        ctk.CTkButton(tpl, text='应用模板', width=90, command=self._on_apply_template).pack(
            side='left', padx=4, pady=6)
        ctk.CTkButton(tpl, text='保存为模板', width=100, command=self._on_save_template).pack(
            side='left', padx=4, pady=6)
        ctk.CTkButton(tpl, text='删除模板', width=90, command=self._on_delete_template).pack(
            side='left', padx=4, pady=6)

        trig = self._section(tab, '触发与回复')
        self._field(trig, '触发正则', self.var_trigger, 380)
        self._field(trig, '触发冷却 (秒)', self.var_cooldown, 100)
        self._check(trig, '启用「自动回复」（有人在聊天里说话就回）', self.var_auto_reply)
        self._check(trig, '启用「定时回复」（每隔一段时间主动说一句）', self.var_schedule)
        self._field(trig, '定时间隔 (秒)', self.var_schedule_interval, 100)
        self._check(trig, 'AI 玩家总开关（等同顶部工具栏的 AI 开关）', self.var_ai_enabled_cfg)
        ctk.CTkLabel(trig, text='触发正则说明：[DiaoHelper] 这种整体是正则/字面量，多个用英文逗号分隔',
                     text_color='#90a4ae').pack(anchor='w', padx=16, pady=(2, 8))

        ctx = self._section(tab, '上下文')
        self._check(ctx, '启用对话上下文', self.var_ctx_enabled)
        self._field(ctx, '上下文条数', self.var_ctx_len, 100)
        self._check(ctx, '服务器公告/指令输出也进上下文', self.var_ctx_server)

        blocked = self._section(tab, '过滤与黑名单')
        self._field(blocked, '过滤正则', self.var_blocked_regex, 380)
        ctk.CTkLabel(blocked, text='黑名单玩家（每行一个名字）',
                     text_color='#90a4ae').pack(anchor='w', padx=16)
        self.blocked_box = ctk.CTkTextbox(blocked, height=90)
        self.blocked_box.pack(fill='x', padx=12, pady=(2, 10))

        buttons = ctk.CTkFrame(tab)
        buttons.pack(fill='x', padx=8, pady=8)
        ctk.CTkButton(buttons, text='保存玩家配置', width=130,
                      command=lambda: self._save_all('玩家配置已保存。')).pack(
            side='left', padx=12, pady=8)
        ctk.CTkButton(buttons, text='清空 AI 上下文', width=130,
                      fg_color='#455a64', hover_color='#37474f',
                      command=lambda: self.controller.clear_ai_context()).pack(
            side='left', padx=6, pady=8)

    # ------------------------------------------------------------------
    # 文档转书
    # ------------------------------------------------------------------
    def _build_docbook_tab(self, tab) -> None:
        self.var_book_file = ctk.StringVar(value='')
        self.var_book_lines = ctk.StringVar(value='14')
        self.var_book_pages = ctk.StringVar(value='100')
        self.var_book_title = ctk.StringVar(value='')
        self.var_book_start = ctk.StringVar(value='')
        self.var_book_end = ctk.StringVar(value='')
        self.var_book_startbook = ctk.StringVar(value='1')
        self.var_book_sign = ctk.BooleanVar(value=False)
        self.var_book_drop = ctk.BooleanVar(value=False)
        self.var_book_autoget = ctk.BooleanVar(value=True)

        src = self._section(tab, '文档')
        row = ctk.CTkFrame(src)
        row.pack(fill='x', padx=12, pady=2)
        ctk.CTkLabel(row, text='文件', width=96, anchor='e').pack(side='left')
        ctk.CTkEntry(row, textvariable=self.var_book_file, width=380).pack(
            side='left', padx=8, pady=2)
        ctk.CTkButton(row, text='选择文件…', width=100,
                      command=self._on_choose_book_file).pack(side='left', padx=4)
        self._field(src, '每页最大行数', self.var_book_lines, 100)
        self._field(src, '每本书最大页数', self.var_book_pages, 100)
        self._check(src, '编辑为成书（署名）', self.var_book_sign)
        self._field(src, '书名模板 ({number})', self.var_book_title, 240)
        self._field(src, '开始提示', self.var_book_start, 320)
        self._field(src, '结束提示', self.var_book_end, 320)
        self._check(src, '写完后把书丢出', self.var_book_drop)
        self._check(src, '创造模式下缺书时自动获取', self.var_book_autoget)

        ctl = ctk.CTkFrame(tab)
        ctl.pack(fill='x', padx=8, pady=6)
        ctk.CTkButton(ctl, text='预览分页', width=100,
                      command=self._on_preview_book).pack(side='left', padx=8, pady=6)
        ctk.CTkButton(ctl, text='写入书与笔', width=110,
                      command=self._on_write_book).pack(side='left', padx=6, pady=6)
        ctk.CTkButton(ctl, text='从第', width=50,
                      fg_color='#455a64', command=self._on_start_from_book).pack(
            side='left', padx=(12, 0), pady=6)
        ctk.CTkEntry(ctl, textvariable=self.var_book_startbook, width=50).pack(
            side='left', padx=2, pady=6)
        ctk.CTkLabel(ctl, text='册开始').pack(side='left', padx=(0, 6))
        for text, handler in (('暂停', lambda: self._on_book_control('pause')),
                              ('停止', lambda: self._on_book_control('stop')),
                              ('继续', self._on_continue_book),
                              ('重写', self._on_rewrite_book),
                              ('清空书页', self._on_clear_book)):
            ctk.CTkButton(ctl, text=text, width=70, fg_color='#37474f',
                          hover_color='#263238', command=handler).pack(
                side='left', padx=4, pady=6)
        self.book_preview = ctk.CTkTextbox(tab, height=220)
        self.book_preview.pack(fill='both', expand=True, padx=12, pady=(0, 8))

    def _on_choose_book_file(self) -> None:
        path = filedialog.askopenfilename(
            title='选择要转换的文档',
            filetypes=[('文档', '*.txt *.docx *.doc'), ('所有文件', '*.*')])
        if not path:
            return
        self.var_book_file.set(path)
        self._on_preview_book()

    def _book_pages(self):
        path = (self.var_book_file.get() or '').strip()
        if not path:
            messagebox.showwarning(APP_TITLE, '请先选择要转换的文档。')
            return None
        if not os.path.exists(path):
            messagebox.showerror(APP_TITLE, '文件不存在：\n' + path)
            return None
        try:
            lines = int((self.var_book_lines.get() or '14').strip())
        except ValueError:
            lines = 14
        lines = max(1, min(docbook.MAX_LINES_PER_PAGE, lines))
        try:
            return docbook.load_pages(path, max_chars=docbook.MAX_PAGE_CHARS,
                                      max_lines=lines)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, '读取文档失败：\n' + str(exc))
            return None

    def _on_preview_book(self) -> None:
        pages = self._book_pages()
        if pages is None:
            return
        self.book_preview.configure(state='normal')
        self.book_preview.delete('1.0', 'end')
        if not pages:
            self.book_preview.insert('end', '（没有解析到文本内容）')
        else:
            total = sum(len(p) for p in pages)
            self.book_preview.insert(
                'end', '共 %d 页，%d 字符（每页 ≤ %d 行）\n%s\n' % (
                    len(pages), total, docbook.MAX_LINES_PER_PAGE, '=' * 48))
            for i, page in enumerate(pages, 1):
                self.book_preview.insert('end', '—— 第 %d 页 ——\n%s\n\n' % (i, page))
        self.book_preview.configure(state='disabled')
        self._append_log('已预览分页：%d 页。' % len(pages), 'info')

    def _on_write_book(self) -> None:
        pages = self._book_pages()
        if pages is None:
            return
        if not pages:
            messagebox.showwarning(APP_TITLE, '没有解析到可写入的文本内容。')
            return
        if not self._gather_config():
            return
        cfg = self.controller.config.docBook
        ok = self.controller.write_book(
            pages,
            start_text=cfg.startText, end_text=cfg.endText,
            end_drop=cfg.endDrop, auto_get_book=cfg.autoGetBook,
            pages_per_book=cfg.pagesPerBook, sign_book=cfg.signBook,
            title_template=cfg.titleTemplate,
            doc_file=(self.var_book_file.get() or '').strip(),
            start_page=0, book_start=1)
        self._append_log('已发送写入指令：%d 页。' % len(pages) if ok
                         else '写入指令发送失败（未连接或引擎未就绪）。',
                         'song' if ok else 'error')

    def _on_start_from_book(self) -> None:
        pages = self._book_pages()
        if pages is None or not pages:
            return
        if not self._gather_config():
            return
        cfg = self.controller.config.docBook
        per_book = max(1, int(cfg.pagesPerBook or 100))
        total_books = max(1, (len(pages) + per_book - 1) // per_book)
        try:
            book_no = int((self.var_book_startbook.get() or '1').strip())
        except ValueError:
            book_no = 1
        if book_no < 1 or book_no > total_books:
            messagebox.showwarning(APP_TITLE, '本文档约 %d 册，起始册号超出范围。' % total_books)
            return
        start_page = (book_no - 1) * per_book
        ok = self.controller.write_book(
            pages,
            start_text=cfg.startText, end_text=cfg.endText,
            end_drop=cfg.endDrop, auto_get_book=cfg.autoGetBook,
            pages_per_book=per_book, sign_book=cfg.signBook,
            title_template=cfg.titleTemplate,
            doc_file=(self.var_book_file.get() or '').strip(),
            start_page=start_page, book_start=book_no)
        self._append_log('已发送写入指令：从第 %d 册开始。' % book_no if ok
                         else '写入指令发送失败。', 'song' if ok else 'error')

    def _on_book_control(self, action: str) -> None:
        self._gather_config()
        self.controller.pause_book(action)

    def _on_continue_book(self) -> None:
        if self._gather_config():
            self.controller.continue_book()

    def _on_rewrite_book(self) -> None:
        if not messagebox.askyesno(APP_TITLE, '从第一页重新写入？（已记录的进度将被覆盖）'):
            return
        if self._gather_config():
            self.controller.rewrite_book()

    def _on_clear_book(self) -> None:
        if not messagebox.askyesno(APP_TITLE, '清空机器人背包里所有「书与笔」的内容？'):
            return
        ok = self.controller.clear_book()
        self._append_log('已发送清空书与笔指令。' if ok else '清空指令发送失败。',
                         'song' if ok else 'error')

    # ------------------------------------------------------------------
    # 玩家资料库
    # ------------------------------------------------------------------
    def _build_roster_tab(self, tab) -> None:
        self.var_roster_enabled = ctk.BooleanVar(value=True)
        self.var_roster_persona = ctk.BooleanVar(value=True)
        self.var_roster_alias = ctk.BooleanVar(value=True)
        self.var_roster_interval = ctk.StringVar(value='180')
        box = self._section(tab, '资料表（xlsx / csv：玩家名 | 玩家描述 | 别名 | AI 记录）')
        self._check(box, '启用玩家资料库（「xxx是谁」按表回答、聊天自动学习）',
                    self.var_roster_enabled)
        self._check(box, '启用性格研究（AI 读聊天后写 AI 记录）', self.var_roster_persona)
        self._field(box, '研究间隔 (秒)', self.var_roster_interval, 100)
        self._check(box, '启用别名分析', self.var_roster_alias)
        ctk.CTkLabel(box, text='已加载资料表：', text_color='#90a4ae').pack(
            anchor='w', padx=16, pady=(6, 0))
        self.roster_box = ctk.CTkTextbox(tab, height=160)
        self.roster_box.pack(fill='x', padx=12, pady=6)
        self.roster_box.configure(state='disabled')
        bar = ctk.CTkFrame(tab)
        bar.pack(fill='x', padx=8, pady=6)
        for text, handler in (('添加文件…', self._on_roster_add),
                              ('移除选中', self._on_roster_remove),
                              ('导出模板…', self._on_roster_template),
                              ('刷新列表', self._refresh_roster_list)):
            ctk.CTkButton(bar, text=text, width=110, command=handler).pack(
                side='left', padx=6, pady=6)
        ctk.CTkButton(bar, text='保存资料库设置', width=150,
                      command=lambda: self._save_all('资料库设置已保存。')).pack(
            side='right', padx=6, pady=6)

    def _refresh_roster_list(self) -> None:
        paths = []
        try:
            paths = list(self.controller.roster.file_paths())
        except Exception:  # noqa: BLE001
            paths = list(getattr(self.controller.config.roster, 'files', []) or [])
        self.roster_box.configure(state='normal')
        self.roster_box.delete('1.0', 'end')
        self.roster_box.insert('end', '\n'.join(paths) if paths else '（还没有资料表）')
        self.roster_box.configure(state='disabled')

    def _on_roster_add(self) -> None:
        path = filedialog.askopenfilename(
            title='选择玩家资料表',
            filetypes=[('表格', '*.xlsx *.csv'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            count = self.controller.roster_add_file(path)
            self._refresh_roster_list()
            self._append_log('已添加资料表：%s（%s 行）' % (path, count), 'info')
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, '添加失败：\n' + str(exc))

    def _on_roster_remove(self) -> None:
        try:
            indexes = self.roster_box.tag_ranges('sel')
            if not indexes:
                messagebox.showinfo(APP_TITLE, '先在列表里选中要移除的文件。')
                return
            selected = self.roster_box.get('sel.first', 'sel.last')
        except Exception:  # noqa: BLE001
            return
        try:
            self.controller.roster_remove_file(selected.strip())
            self._refresh_roster_list()
            self._append_log('已移除资料表：' + selected.strip(), 'info')
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, '移除失败：\n' + str(exc))

    def _on_roster_template(self) -> None:
        path = filedialog.asksaveasfilename(
            title='导出玩家资料模板', defaultextension='.xlsx',
            filetypes=[('Excel', '*.xlsx')])
        if not path:
            return
        try:
            self.controller.export_roster_template(path)
            self._append_log('已导出资料模板：' + path, 'info')
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, '导出失败：\n' + str(exc))

    # ------------------------------------------------------------------
    # QQ 转述
    # ------------------------------------------------------------------
    def _build_qq_tab(self, tab) -> None:
        self.var_qq_framework = ctk.StringVar(value='snowluma')
        self.var_qq_path = ctk.StringVar(value='')
        self.var_qq_mode = ctk.StringVar(value='forward')
        self.var_qq_url = ctk.StringVar(value='ws://127.0.0.1:3001')
        self.var_qq_token = ctk.StringVar(value='')
        self.var_qq_groups = ctk.StringVar(value='')
        self.var_qq_to_mc = ctk.BooleanVar(value=True)
        self.var_mc_to_qq = ctk.BooleanVar(value=False)
        self.var_mc_trigger = ctk.StringVar(value='[sentQ]')

        net = self._section(tab, 'OneBot 11 网关（SnowLuma / NapCat）')
        row = ctk.CTkFrame(net)
        row.pack(fill='x', padx=12, pady=2)
        ctk.CTkLabel(row, text='框架', width=96, anchor='e').pack(side='left')
        ctk.CTkOptionMenu(row, values=['snowluma', 'napcat'],
                          variable=self.var_qq_framework, width=140,
                          command=lambda _v: self._load_qq_widgets()).pack(
            side='left', padx=8, pady=2)
        self._field(net, '启动路径', self.var_qq_path, 380)
        self._field(net, '连接模式', self.var_qq_mode, 120)
        self._field(net, '正向地址', self.var_qq_url, 300)
        self._field(net, 'Access Token', self.var_qq_token, 300, show='*')
        self.qq_status = ctk.CTkLabel(net, text='QQ 未连接', text_color='#90a4ae')
        self.qq_status.pack(anchor='w', padx=16, pady=(2, 8))

        bar = ctk.CTkFrame(tab)
        bar.pack(fill='x', padx=8, pady=4)
        for text, handler in (('启动框架', self._on_qq_launch),
                              ('连接', self._on_qq_start),
                              ('重连', self._on_qq_restart),
                              ('断开', self._on_qq_stop)):
            ctk.CTkButton(bar, text=text, width=100, command=handler).pack(
                side='left', padx=6, pady=6)

        relay = self._section(tab, '双向转发')
        self._check(relay, 'QQ → MC（群消息发到游戏公屏）', self.var_qq_to_mc)
        self._field(relay, '群号(逗号分隔)', self.var_qq_groups, 300)
        self._check(relay, 'MC → QQ（公屏以触发词开头时转发到群）', self.var_mc_to_qq)
        self._field(relay, 'MC 触发词', self.var_mc_trigger, 160)
        ctk.CTkButton(tab, text='保存 QQ 设置', width=130,
                      command=lambda: self._save_all('QQ 设置已保存。')).pack(
            anchor='w', padx=20, pady=10)

    def _load_qq_widgets(self) -> None:
        cfg = self.controller.config.qq
        endpoint = cfg.active()
        self.var_qq_path.set(endpoint.launcherPath or '')
        self.var_qq_mode.set(endpoint.mode or 'forward')
        self.var_qq_url.set(endpoint.url or 'ws://127.0.0.1:3001')
        self.var_qq_token.set(endpoint.accessToken or '')
        self.var_qq_groups.set(','.join(cfg.groups or []))
        self.var_qq_to_mc.set(bool(cfg.qqToMcEnabled))
        self.var_mc_to_qq.set(bool(cfg.mcToQqEnabled))
        self.var_mc_trigger.set(cfg.mcTrigger or '[sentQ]')
        self.var_qq_framework.set(cfg.framework or 'snowluma')

    def _on_qq_launch(self) -> None:
        if not self._gather_config():
            return
        try:
            self.controller.start_qq_framework()
            self._append_log('已请求启动 QQ 框架（详见日志页输出）。', 'info')
        except Exception as exc:  # noqa: BLE001
            self._append_log('启动 QQ 框架失败：' + str(exc), 'error')

    def _on_qq_start(self) -> None:
        if not self._gather_config():
            return
        try:
            self.controller.start_qq()
            self._append_log('正在连接 QQ 网关…', 'info')
        except Exception as exc:  # noqa: BLE001
            self._append_log('连接 QQ 失败：' + str(exc), 'error')

    def _on_qq_restart(self) -> None:
        if not self._gather_config():
            return
        try:
            self.controller.restart_qq()
            self._append_log('正在重连 QQ 网关…', 'info')
        except Exception as exc:  # noqa: BLE001
            self._append_log('重连 QQ 失败：' + str(exc), 'error')

    def _on_qq_stop(self) -> None:
        try:
            self.controller.stop_qq()
            self._append_log('已断开 QQ 网关。', 'info')
        except Exception as exc:  # noqa: BLE001
            self._append_log('断开 QQ 失败：' + str(exc), 'error')

    # ------------------------------------------------------------------
    # 重连·踢出
    # ------------------------------------------------------------------
    def _build_reconnect_tab(self, tab) -> None:
        self.var_rc_enabled = ctk.BooleanVar(value=True)
        self.var_rc_delay = ctk.StringVar(value='5')
        self.var_rc_max = ctk.StringVar(value='5')
        self.var_rc_lobby = ctk.BooleanVar(value=True)
        self.var_rc_lobby_delay = ctk.StringVar(value='2')
        self.var_rc_proactive = ctk.BooleanVar(value=False)
        self.var_rc_proactive_interval = ctk.StringVar(value='300')
        self.var_rc_keep = ctk.BooleanVar(value=False)
        self.var_rc_keep_minutes = ctk.StringVar(value='0')
        self.var_rc_autolobby = ctk.BooleanVar(value=False)
        self.var_rc_autolobby_interval = ctk.StringVar(value='300')
        self.var_rc_online = ctk.BooleanVar(value=False)
        self.var_rc_online_interval = ctk.StringVar(value='60')

        base = self._section(tab, '掉线 / 被踢自动重连')
        self._check(base, '连接意外断开时自动重连（手动断开不触发）', self.var_rc_enabled)
        self._field(base, '重连延迟 (秒)', self.var_rc_delay, 100)
        self._field(base, '最多尝试次数', self.var_rc_max, 100)
        self._check(base, '被踢后重连成功时自动发 /lobby', self.var_rc_lobby)
        self._field(base, '/lobby 延迟 (秒)', self.var_rc_lobby_delay, 100)
        self._check(base, '一直重试直到服务器回来', self.var_rc_keep)
        self._field(base, '最长重试 (分钟, 0=不限)', self.var_rc_keep_minutes, 120)

        extra = self._section(tab, '主动保活')
        self._check(extra, '在线时主动掉线重连（刷新会话）', self.var_rc_proactive)
        self._field(extra, '主动重连间隔 (秒)', self.var_rc_proactive_interval, 120)
        self._check(extra, '在线时定时发 /lobby', self.var_rc_autolobby)
        self._field(extra, '/lobby 间隔 (秒)', self.var_rc_autolobby_interval, 120)
        self._check(extra, '定时检查在线状态，掉线自动重连', self.var_rc_online)
        self._field(extra, '在线检测间隔 (秒)', self.var_rc_online_interval, 120)

        ctk.CTkButton(tab, text='保存重连设置', width=140,
                      command=lambda: self._save_all('重连设置已保存。')).pack(
            anchor='w', padx=20, pady=10)

    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        cfg = self.controller.config
        self.var_host.set(cfg.server.host or 'localhost')
        self.var_port.set(str(cfg.server.port or 25565))
        self.var_version.set(cfg.server.version or '1.20')
        self.var_auth.set(cfg.auth.type or 'offline')
        self.var_username.set(cfg.auth.username or 'AI_Bot')
        self.var_scan_host.set(cfg.server.host or '')
        self._load_extended()

    def _gather_config(self) -> bool:
        cfg = self.controller.config
        cfg.server.host = (self.var_host.get() or '').strip() or 'localhost'
        try:
            cfg.server.port = int((self.var_port.get() or '').strip())
        except ValueError:
            cfg.server.port = 25565
        cfg.server.version = (self.var_version.get() or '').strip() or '1.20'
        cfg.auth.type = self.var_auth.get()
        cfg.auth.username = (self.var_username.get() or '').strip() or 'AI_Bot'
        cfg.validate()
        if not cfg.save():
            messagebox.showerror(APP_TITLE, '设置保存失败（磁盘/权限问题？）')
            return False
        if not self._gather_extended():
            messagebox.showerror(APP_TITLE, '部分设置保存失败（AI / QQ / 重连 / 资料库），请检查 data.db 写入权限。')
            return False
        reload_ai = getattr(self.controller, 'reload_ai_player', None)
        if callable(reload_ai):
            try:
                reload_ai()
            except Exception:  # noqa: BLE001
                pass
        return True

    # ------------------------------------------------------------------
    # 外观主题
    # ------------------------------------------------------------------
    def _on_theme_changed(self, label: str) -> None:
        mode = {'深色': 'dark', '浅色': 'light', '跟随系统': 'system'}.get(label, 'dark')
        try:
            ctk.set_appearance_mode(mode)
        except Exception:  # noqa: BLE001
            pass
        self._append_log('界面主题已切换：' + label, 'info')

    # ------------------------------------------------------------------
    # 扩展配置：AI / 玩家 / 文档转书 / 资料库 / QQ / 重连 / 杂项
    # ------------------------------------------------------------------
    @staticmethod
    def _as_int(var, default: int) -> int:
        try:
            return int(str(var.get()).strip())
        except (TypeError, ValueError, AttributeError):
            return default

    @staticmethod
    def _as_float(var, default: float) -> float:
        try:
            return float(str(var.get()).strip())
        except (TypeError, ValueError, AttributeError):
            return default

    def _refresh_templates(self) -> None:
        try:
            names = [str(t.get('name') or '') for t in PromptTemplateManager.get_all()]
        except Exception:  # noqa: BLE001
            names = []
        names = [n for n in names if n]
        try:
            self.template_menu.configure(values=['（无）'] + names)
        except Exception:  # noqa: BLE001
            pass

    def _on_apply_template(self) -> None:
        name = (self.var_template.get() or '').strip()
        if not name or name == '（无）':
            return
        for item in PromptTemplateManager.get_all():
            if str(item.get('name')) == name:
                self.prompt_box.delete('1.0', 'end')
                self.prompt_box.insert('end', str(item.get('prompt') or ''))
                self._append_log('已应用提示词模板：' + name, 'info')
                return
        messagebox.showinfo(APP_TITLE, '模板不存在：' + name)

    def _on_save_template(self) -> None:
        dialog = ctk.CTkInputDialog(text='给这个提示词起个名字：', title='保存提示词模板')
        name = (dialog.get_input() or '').strip()
        if not name:
            return
        prompt = self.prompt_box.get('1.0', 'end').strip()
        if PromptTemplateManager.save(name, prompt):
            self._refresh_templates()
            self.var_template.set(name)
            self._append_log('提示词模板已保存：' + name, 'info')
        else:
            messagebox.showerror(APP_TITLE, '模板保存失败（data.db 写入失败）。')

    def _on_delete_template(self) -> None:
        name = (self.var_template.get() or '').strip()
        if not name or name == '（无）':
            return
        if not messagebox.askyesno(APP_TITLE, '删除提示词模板「' + name + '」？'):
            return
        if PromptTemplateManager.delete(name):
            self._refresh_templates()
            self.var_template.set('（无）')
            self._append_log('已删除提示词模板：' + name, 'info')

    def _load_extended(self) -> None:
        cfg = self.controller.config
        ai = self.controller.ai_config
        # --- AI 配置 ---
        self.var_ai_provider.set(ai.provider or 'openai')
        self.var_ai_base.set(ai.baseUrl or '')
        self.var_ai_key.set(ai.apiKey or '')
        self.var_ai_model.set(ai.model or 'gpt-4o-mini')
        self.var_ai_temp.set(str(ai.temperature))
        self.var_ai_maxtok.set(str(ai.maxTokens))
        self.var_ai_timeout.set(str(ai.timeoutSeconds))
        self.var_ai_search.set(bool(ai.searchEnabled))
        self.var_ai_image.set(bool(ai.imageGenerationEnabled))
        # --- 玩家配置 ---
        self.var_trigger.set(ai.triggerRegex or '')
        self.var_cooldown.set(str(ai.triggerCooldownSeconds))
        self.var_ctx_enabled.set(bool(ai.contextEnabled))
        self.var_ctx_len.set(str(ai.contextLength))
        self.var_ctx_server.set(bool(ai.serverMessagesInContext))
        self.var_auto_reply.set(bool(ai.autoReplyEnabled))
        self.var_schedule.set(bool(ai.scheduleEnabled))
        self.var_schedule_interval.set(str(ai.scheduleIntervalSeconds))
        self.var_ai_enabled_cfg.set(bool(ai.enabled))
        self.prompt_box.delete('1.0', 'end')
        self.prompt_box.insert('end', ai.systemPrompt or '')
        self.var_blocked_regex.set(', '.join(ai.blockedRegexPatterns or []))
        names = []
        for item in ai.blockedPlayers or []:
            if isinstance(item, dict) and item.get('name'):
                names.append(str(item['name']))
        self.blocked_box.delete('1.0', 'end')
        self.blocked_box.insert('end', '\n'.join(names))
        self._refresh_templates()
        # --- 文档转书 ---
        db = cfg.docBook
        self.var_book_lines.set(str(db.pageLines or 14))
        self.var_book_pages.set(str(db.pagesPerBook or 100))
        self.var_book_sign.set(bool(db.signBook))
        self.var_book_title.set(db.titleTemplate or '')
        self.var_book_start.set(db.startText or '')
        self.var_book_end.set(db.endText or '')
        self.var_book_drop.set(bool(db.endDrop))
        self.var_book_autoget.set(bool(db.autoGetBook))
        if db.lastFile:
            self.var_book_file.set(db.lastFile)
        # --- 资料库 ---
        roster = cfg.roster
        self.var_roster_enabled.set(bool(roster.enabled))
        self.var_roster_persona.set(bool(roster.personalityEnabled))
        self.var_roster_alias.set(bool(roster.aliasEnabled))
        self.var_roster_interval.set(str(roster.researchIntervalSeconds))
        self._refresh_roster_list()
        # --- QQ / 重连 ---
        self._load_qq_widgets()
        rc = cfg.reconnect
        self.var_rc_enabled.set(bool(rc.enabled))
        self.var_rc_delay.set(str(rc.delaySeconds))
        self.var_rc_max.set(str(rc.maxAttempts))
        self.var_rc_lobby.set(bool(rc.kickLobbyEnabled))
        self.var_rc_lobby_delay.set(str(rc.kickLobbyDelaySeconds))
        self.var_rc_proactive.set(bool(rc.proactiveEnabled))
        self.var_rc_proactive_interval.set(str(rc.proactiveIntervalSeconds))
        self.var_rc_keep.set(bool(rc.keepRetrying))
        self.var_rc_keep_minutes.set(str(rc.keepRetryMaxMinutes))
        self.var_rc_autolobby.set(bool(rc.autoLobbyEnabled))
        self.var_rc_autolobby_interval.set(str(rc.autoLobbyIntervalSeconds))
        self.var_rc_online.set(bool(rc.onlineCheckEnabled))
        self.var_rc_online_interval.set(str(rc.onlineCheckIntervalSeconds))
        # --- 杂项 / 外观 ---
        misc = cfg.misc
        source_labels = {'anime': '番剧追番榜', 'home': 'B 站首页推荐',
                         'hot': 'B 站首页热门', 'rank': 'B 站排行榜',
                         'fav': 'B 站收藏夹'}
        self.var_misc_video_enabled.set(bool(misc.videoEnabled))
        self.var_misc_video_source.set(source_labels.get(misc.videoSource, '番剧追番榜'))
        self.var_misc_video_count.set(str(misc.videoCount))
        self.var_misc_video_cooldown.set(str(misc.videoCooldownSeconds))
        self.var_misc_luck_enabled.set(bool(misc.luckEnabled))
        self.var_misc_checkin_enabled.set(bool(misc.checkinEnabled))
        self.var_mass_gap.set(str(misc.massGapSeconds))
        self.var_receipt_enabled.set(bool(misc.mcChatPrintEnabled))
        self.var_receipt_printer.set(misc.mcChatPrinterName or '')
        self.var_receipt_paper.set(str(misc.mcChatPaperWidth or 58))
        self.var_receipt_maxper.set(str(misc.mcChatPrintMaxPerMinute or 20))
        self.var_receipt_dryrun.set(bool(misc.mcChatPrintDryRun))
        theme = str(getattr(cfg.appearance, 'textTheme', 'auto') or 'auto')
        self.var_theme.set({'dark': '深色', 'light': '浅色'}.get(theme, '跟随系统'))

    def _gather_extended(self) -> bool:
        cfg = self.controller.config
        ai = self.controller.ai_config
        # --- AI ---
        ai.provider = (self.var_ai_provider.get() or 'openai').strip() or 'openai'
        ai.baseUrl = (self.var_ai_base.get() or '').strip()
        ai.apiKey = (self.var_ai_key.get() or '').strip()
        ai.model = (self.var_ai_model.get() or 'gpt-4o-mini').strip() or 'gpt-4o-mini'
        ai.temperature = max(0.0, min(2.0, self._as_float(self.var_ai_temp, 0.7)))
        ai.maxTokens = max(16, self._as_int(self.var_ai_maxtok, 1024))
        ai.timeoutSeconds = max(5, self._as_int(self.var_ai_timeout, 30))
        ai.searchEnabled = bool(self.var_ai_search.get())
        ai.imageGenerationEnabled = bool(self.var_ai_image.get())
        # --- 玩家 ---
        ai.triggerRegex = (self.var_trigger.get() or '').strip()
        ai.triggerCooldownSeconds = max(0, self._as_int(self.var_cooldown, 10))
        ai.contextEnabled = bool(self.var_ctx_enabled.get())
        ai.contextLength = max(0, self._as_int(self.var_ctx_len, 20))
        ai.serverMessagesInContext = bool(self.var_ctx_server.get())
        ai.autoReplyEnabled = bool(self.var_auto_reply.get())
        ai.scheduleEnabled = bool(self.var_schedule.get())
        ai.scheduleIntervalSeconds = max(5, self._as_int(self.var_schedule_interval, 30))
        ai.systemPrompt = self.prompt_box.get('1.0', 'end').strip()
        regex_text = (self.var_blocked_regex.get() or '').strip()
        ai.blockedRegexPatterns = [p.strip() for p in re.split(r'[,，\n]', regex_text) if p.strip()]
        blocked_names = [line.strip() for line in
                         (self.blocked_box.get('1.0', 'end') or '').splitlines()
                         if line.strip()]
        ai.blockedPlayers = [{'name': name} for name in blocked_names]
        enabled = bool(self.var_ai_enabled_cfg.get())
        ai.enabled = enabled
        # --- 文档转书 ---
        db = cfg.docBook
        db.pageLines = max(1, min(14, self._as_int(self.var_book_lines, 14)))
        db.pagesPerBook = max(1, min(100, self._as_int(self.var_book_pages, 100)))
        db.signBook = bool(self.var_book_sign.get())
        db.titleTemplate = (self.var_book_title.get() or '').strip()
        db.startText = (self.var_book_start.get() or '').strip()
        db.endText = (self.var_book_end.get() or '').strip()
        db.endDrop = bool(self.var_book_drop.get())
        db.autoGetBook = bool(self.var_book_autoget.get())
        if self.var_book_file.get():
            db.lastFile = self.var_book_file.get().strip()
        # --- 资料库 ---
        roster = cfg.roster
        roster.enabled = bool(self.var_roster_enabled.get())
        roster.personalityEnabled = bool(self.var_roster_persona.get())
        roster.aliasEnabled = bool(self.var_roster_alias.get())
        roster.researchIntervalSeconds = max(10, self._as_int(self.var_roster_interval, 180))
        # --- QQ ---
        qq = cfg.qq
        framework = (self.var_qq_framework.get() or 'snowluma').strip()
        if framework not in ('snowluma', 'napcat'):
            framework = 'snowluma'
        qq.framework = framework
        endpoint = qq.active()
        endpoint.launcherPath = (self.var_qq_path.get() or '').strip()
        mode = (self.var_qq_mode.get() or 'forward').strip()
        endpoint.mode = mode if mode in ('forward', 'reverse') else 'forward'
        endpoint.url = (self.var_qq_url.get() or '').strip() or 'ws://127.0.0.1:3001'
        endpoint.accessToken = (self.var_qq_token.get() or '').strip()
        qq.groups = [g.strip() for g in re.split(r'[,，\s]+',
                                                self.var_qq_groups.get() or '') if g.strip()]
        qq.qqToMcEnabled = bool(self.var_qq_to_mc.get())
        qq.mcToQqEnabled = bool(self.var_mc_to_qq.get())
        qq.mcTrigger = (self.var_mc_trigger.get() or '[sentQ]').strip() or '[sentQ]'
        # --- 重连 ---
        rc = cfg.reconnect
        rc.enabled = bool(self.var_rc_enabled.get())
        rc.delaySeconds = max(0, self._as_int(self.var_rc_delay, 5))
        rc.maxAttempts = max(1, self._as_int(self.var_rc_max, 5))
        rc.kickLobbyEnabled = bool(self.var_rc_lobby.get())
        rc.kickLobbyDelaySeconds = max(0, self._as_int(self.var_rc_lobby_delay, 2))
        rc.proactiveEnabled = bool(self.var_rc_proactive.get())
        rc.proactiveIntervalSeconds = max(1, self._as_int(self.var_rc_proactive_interval, 300))
        rc.keepRetrying = bool(self.var_rc_keep.get())
        rc.keepRetryMaxMinutes = max(0, self._as_int(self.var_rc_keep_minutes, 0))
        rc.autoLobbyEnabled = bool(self.var_rc_autolobby.get())
        rc.autoLobbyIntervalSeconds = max(1, self._as_int(self.var_rc_autolobby_interval, 300))
        rc.onlineCheckEnabled = bool(self.var_rc_online.get())
        rc.onlineCheckIntervalSeconds = max(1, self._as_int(self.var_rc_online_interval, 60))
        # --- 杂项 ---
        misc = cfg.misc
        misc.videoEnabled = bool(self.var_misc_video_enabled.get())
        keys = {'番剧追番榜': 'anime', 'B 站首页推荐': 'home', 'B 站首页热门': 'hot',
                'B 站排行榜': 'rank', 'B 站收藏夹': 'fav'}
        misc.videoSource = keys.get(self.var_misc_video_source.get(), 'anime')
        misc.videoCount = max(1, min(10, self._as_int(self.var_misc_video_count, 3)))
        misc.videoCooldownSeconds = max(0, self._as_int(self.var_misc_video_cooldown, 30))
        misc.luckEnabled = bool(self.var_misc_luck_enabled.get())
        misc.checkinEnabled = bool(self.var_misc_checkin_enabled.get())
        misc.massGapSeconds = max(0.0, self._as_float(self.var_mass_gap, 0.5))
        misc.mcChatPrintEnabled = bool(self.var_receipt_enabled.get())
        misc.mcChatPrinterName = (self.var_receipt_printer.get() or '').strip()
        misc.mcChatPaperWidth = 80 if self._as_int(self.var_receipt_paper, 58) == 80 else 58
        misc.mcChatPrintMaxPerMinute = max(0, self._as_int(self.var_receipt_maxper, 20))
        misc.mcChatPrintDryRun = bool(self.var_receipt_dryrun.get())
        # --- 外观（只存主题偏好；CTk 界面用深色系布局）---
        theme = {'深色': 'dark', '浅色': 'light', '跟随系统': 'auto'}.get(
            self.var_theme.get(), 'auto')
        cfg.appearance.textTheme = theme

        ai.validate()
        cfg.validate()
        ok = ai.save()
        ok = cfg.save() and ok
        try:
            self.controller.sync_roster_runtime()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.controller.set_ai_enabled(enabled)
        except Exception:  # noqa: BLE001
            pass
        reload_ai = getattr(self.controller, 'reload_ai_player', None)
        if callable(reload_ai):
            try:
                reload_ai()
            except Exception:  # noqa: BLE001
                pass
        return bool(ok)

    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        if self._gather_config():
            self._append_log('设置已保存。')

    def _on_connect(self) -> None:
        if not self._gather_config():
            return
        self.controller.sync_roster_runtime()
        self.controller.connect()
        self.status_label.configure(text='正在连接…', text_color='#4fc3f7')

    def _on_disconnect(self) -> None:
        self.controller.disconnect()
        self.status_label.configure(text='已断开', text_color='#b0bec5')

    def _on_ai_toggle(self) -> None:
        if not self._gather_config():
            return
        self.controller.set_ai_enabled(bool(self.var_ai.get()))

    def _on_send_chat(self, var=None) -> None:
        source = var or self.var_chat
        text = (source.get() or '').strip()
        if not text:
            return
        self.controller.send_chat(text)
        source.set('')

    def _on_play_song(self) -> None:
        query = (self.var_song.get() or '').strip()
        if not query:
            return
        speed = (self.var_speed.get() or '1.0').strip()
        line = query + ('' if speed in ('', '1.0') else ' ' + speed)
        if not self.controller.connected:
            messagebox.showwarning(APP_TITLE, '还没连上服务器，先点「连接」。')
            return
        self.controller.send_chat(self._song_trigger() + line)
        self.controller.enqueue_song(query, '我', 'gui')
        self.var_song.set('')

    def _song_trigger(self) -> str:
        try:
            return (self.controller.config.song.trigger or '[点歌]').strip() or '[点歌]'
        except Exception:  # noqa: BLE001
            return '[点歌]'

    def _on_song_stop(self) -> None:
        self.controller.clear_song_queue()

    def _on_listen_list(self) -> None:
        self.controller.send_chat(self._song_trigger() + '一起听')

    def _on_song_help(self) -> None:
        self.controller.send_chat(self._song_trigger() + 'help')

    def _on_video(self) -> None:
        self.controller.send_chat(self._bot_trigger() + '视频推荐')

    def _on_lucky(self) -> None:
        self.controller.send_chat(self._bot_trigger() + '今日幸运值')

    def _on_checkin(self) -> None:
        self.controller.send_chat(self._bot_trigger() + '签到')

    def _bot_trigger(self) -> str:
        try:
            regex = (self.controller.ai_config.triggerRegex or '').strip()
            if regex:
                first = regex.split(',')[0].strip().strip('[]')
                if first:
                    return '[' + first + ']'
        except Exception:  # noqa: BLE001
            pass
        return self._song_trigger()

    def _on_mass(self) -> None:
        text = (self.var_mass.get() or '').strip()
        if not text:
            return
        self.controller.mass_message(text)
        self.var_mass.set('')

    def _open_link(self, url: str) -> None:
        if not links.open_url(url):
            messagebox.showwarning(APP_TITLE, '打开浏览器失败，请手动访问：' + url)

    def _open_legacy(self) -> None:
        messagebox.showinfo(APP_TITLE, '旧界面请用命令打开：python main.py --legacy')

    # ------------------------------------------------------------------
    def _set_scan_text(self, text: str) -> None:
        self.scan_box.configure(state='normal')
        self.scan_box.delete('1.0', 'end')
        self.scan_box.insert('end', text)
        self.scan_box.configure(state='disabled')

    def _on_scan(self) -> None:
        if self._scanning:
            return
        host = (self.var_scan_host.get() or self.var_host.get() or '').strip()
        if not host:
            messagebox.showwarning(APP_TITLE, '先填服务器地址')
            return
        mode = self.var_scan_mode.get()
        try:
            start = int((self.var_scan_start.get() or '25500').strip())
            end = int((self.var_scan_end.get() or '25600').strip())
        except ValueError:
            start, end = 25500, 25600
        self._scanning = True
        self._scan_stop = False
        self.scan_status.configure(text='正在扫描 ' + host + ' …')
        self._set_scan_text('')
        threading.Thread(target=self._scan_worker, args=(host, mode, start, end),
                         daemon=True).start()

    def _stop_scan(self) -> None:
        self._scan_stop = True

    def _scan_worker(self, host: str, mode: str, start: int, end: int) -> None:
        started = time.time()
        try:
            if mode == '自定义范围':
                ports: List[int] = list(range(max(1, start), min(65535, end) + 1))
            elif mode.startswith('全端口'):
                ports = list(range(1, 65536))
            else:
                ports = list(portscan.COMMON_PORTS)
            open_ports = portscan.scan(host, ports, timeout=0.35, workers=64,
                                       should_stop=lambda: self._scan_stop or self._closed)
            self.root.after(0, lambda: self.scan_status.configure(
                text='开放端口 %d 个，正在识别 Minecraft…' % len(open_ports)))
            rows: List[str] = []
            mc_rows: List[Dict[str, Any]] = []
            for port in open_ports:
                if self._scan_stop or self._closed:
                    break
                info = portscan.minecraft_status(host, port, timeout=2.0)
                if info:
                    mc_rows.append({'port': port, 'info': info})
                    rows.append('%-6s  [MC]  %-12s %s/%s  %s' % (
                        port, info.get('version') or '?', info.get('online'), info.get('max'),
                        (info.get('motd') or '').replace('\n', ' ')[:60]))
                else:
                    rows.append('%-6s  端口开放（不是 MC 或未响应 status）' % port)
            elapsed = time.time() - started
            summary = '扫描完成：%d 个开放端口，其中 %d 个是 Minecraft（%.1fs）' % (
                len(open_ports), len(mc_rows), elapsed)
            self.root.after(0, lambda: self._show_scan_results(summary, rows, mc_rows))
        except Exception as exc:  # noqa: BLE001
            message = '扫描出错：' + str(exc)
            self.root.after(0, lambda: self._show_scan_results(message, [], []))
        finally:
            self._scanning = False

    def _show_scan_results(self, summary: str, rows: List[str],
                           mc_rows: List[Dict[str, Any]]) -> None:
        self._last_scan_mc = mc_rows
        self.scan_status.configure(text=summary)
        self._set_scan_text('\n'.join(rows) + '\n' if rows else '（没有扫描到开放端口）\n')

    def _apply_scanned_port(self) -> None:
        rows = self._last_scan_mc or []
        if not rows:
            messagebox.showinfo(APP_TITLE, '还没扫到 Minecraft 端口')
            return
        port = rows[0]['port']
        self.var_port.set(str(port))
        messagebox.showinfo(APP_TITLE, '已把端口 ' + str(port) + ' 填到设置里')

    # ------------------------------------------------------------------
    def _append_log(self, text: str, level: str = 'info') -> None:
        self.log_box.configure(state='normal')
        self.log_box.insert('end', _fmt_time() + text + '\n')
        self.log_box.configure(state='disabled')
        self.log_box.see('end')

    def _clear_log(self) -> None:
        self.log_box.configure(state='normal')
        self.log_box.delete('1.0', 'end')
        self.log_box.configure(state='disabled')

    def _handle_event(self, ev: Dict[str, Any]) -> None:
        kind = ev.get('kind')
        if kind == 'log':
            self._append_log(str(ev.get('message') or ''), str(ev.get('level') or 'info'))
        elif kind == 'chat':
            prefix = '[私聊] ' if ev.get('private') else ''
            self._append_log(prefix + str(ev.get('player') or '?') + ': '
                             + str(ev.get('content') or ''),
                             'private' if ev.get('private') else 'chat')
        elif kind == 'status':
            self.status_label.configure(text=str(ev.get('text') or ''), text_color='#4fc3f7')
        elif kind == 'spawn':
            who = ev.get('username') or ''
            self.status_label.configure(text=('已连接（%s）' % who).strip(),
                                        text_color='#7ddb7d')
        elif kind == 'disconnected':
            self.status_label.configure(text='已断开', text_color='#ff8a80')
        elif kind == 'reconnect':
            state = ev.get('state')
            if state == 'scheduled':
                self.status_label.configure(
                    text='掉线，%ss 后自动重连（%s/%s）' % (
                        ev.get('delay', 0), ev.get('attempt'), ev.get('max')),
                    text_color='#ffb74d')
            elif state == 'giveup':
                self.status_label.configure(text='自动重连已放弃', text_color='#ff6b6b')

    def _refresh_song(self) -> None:
        try:
            state = self.controller.song_state()
        except Exception:  # noqa: BLE001
            return
        current = state.get('current') or {}
        if current:
            self.song_now_label.configure(
                text='正在播放：%s - %s（%s 点）' % (
                    current.get('title') or '?', current.get('artist') or '?',
                    current.get('who') or '?'))
        else:
            self.song_now_label.configure(text='当前没有在放歌')
        queue = state.get('queue') or []
        lines = []
        for index, item in enumerate(queue[:30]):
            lines.append('%2d. %s - %s（%s 点）' % (
                index + 1, item.get('title') or '?', item.get('artist') or '?',
                item.get('who') or '?'))
        text = '\n'.join(lines) if lines else '（队列为空）'
        if text != self._last_song_text:
            self._last_song_text = text
            self.song_queue_box.configure(state='normal')
            self.song_queue_box.delete('1.0', 'end')
            self.song_queue_box.insert('end', text)
            self.song_queue_box.configure(state='disabled')

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            for ev in self.controller.drain_ui(200):
                self._handle_event(ev)
            self._refresh_song()
        except Exception as exc:  # noqa: BLE001
            print('[ctk] poll error:', exc)
        self.root.after(200, self._poll)

    def _on_close(self) -> None:
        self._closed = True
        self._scan_stop = True
        try:
            self.controller.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        self._append_log('欢迎使用 ' + APP_TITLE + ' ' + VERSION)
        self._append_log('支持与工单：' + links.SUPPORT_URL
                         + '    开发者博客：' + links.BLOG_URL)
        self.root.mainloop()


def main() -> None:
    app = CtkApp()
    app.run()
