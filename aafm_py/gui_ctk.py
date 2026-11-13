"""CustomTkinter 版主界面（新的默认入口）。

原来的 tkinter 界面（aafm_py/gui.py）有 11 个标签页、功能非常完整；这里用
**CustomTkinter** 重做一个更现代、更省心的主界面，覆盖日常最常用的部分：

- 设置：服务器地址/端口/版本、登录方式、机器人名字、连接/断开、AI 开关、发聊天
- 点歌：点歌（含倍速）、停止、一起听名单、当前播放/队列
- 工具：端口扫描（常见端口 / 自定义范围 / 全端口，并能识别 Minecraft 版本与 MOTD）、
  视频推荐、今日幸运值、签到、群发私聊
- 日志：实时日志 + 自己往游戏里发消息
- 关于：支持与工单、开发者博客、仓库链接、版本信息

需要老界面的全部功能（文档转书 / 玩家资料库 / QQ 转述 / 界面美化 / 重连踢出等）时，
用 python main.py --legacy 打开原 tkinter 界面即可（两边共用同一个 Controller）。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

try:
    import customtkinter as ctk
except Exception as exc:  # noqa: BLE001
    raise SystemExit('缺少 customtkinter：pip install customtkinter（或 python main.py --legacy 用旧界面）') from exc

from tkinter import messagebox

from . import links, portscan
from .controller import Controller

APP_TITLE = 'aafm AI 玩家'
VERSION = 'v0.3.6-ctk'

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
        for name in ('设置', '点歌', '工具', '日志', '关于'):
            self.tabs.add(name)
        self._build_settings_tab(self.tabs.tab('设置'))
        self._build_song_tab(self.tabs.tab('点歌'))
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
        ctk.CTkButton(ai_frame, text='旧界面（全部功能）', width=160,
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
        mass.pack(fill='x', padx=8, pady=(4, 10))
        self.var_mass = ctk.StringVar(value='')
        ctk.CTkEntry(mass, textvariable=self.var_mass,
                     placeholder_text='群发私聊内容（逐个 /msg 在线玩家）').pack(
            side='left', fill='x', expand=True, padx=(6, 8), pady=6)
        ctk.CTkButton(mass, text='群发', width=84, command=self._on_mass).pack(
            side='left', padx=(0, 6), pady=6)

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
        ctk.CTkLabel(frame, text=('提示：这个 CustomTkinter 界面覆盖日常使用；文档转书 / 玩家资料库 / '
                                  'QQ 转述 / 界面美化 / 重连踢出等功能在旧界面里（设置页按钮，或 python main.py --legacy）。'),
                     justify='left', text_color='#78909c').pack(anchor='w', padx=16, pady=(20, 16))

    # ------------------------------------------------------------------
    def _load_config(self) -> None:
        cfg = self.controller.config
        self.var_host.set(cfg.server.host or 'localhost')
        self.var_port.set(str(cfg.server.port or 25565))
        self.var_version.set(cfg.server.version or '1.20')
        self.var_auth.set(cfg.auth.type or 'offline')
        self.var_username.set(cfg.auth.username or 'AI_Bot')
        self.var_scan_host.set(cfg.server.host or '')

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
        reload_ai = getattr(self.controller, 'reload_ai_player', None)
        if callable(reload_ai):
            try:
                reload_ai()
            except Exception:  # noqa: BLE001
                pass
        return True

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
        if not self.controller.connected():
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
