"""Minecraft 聊天小票机：ESC/POS + Windows RAW 打印。

参考 ``Issues Or PR Print`` 项目的打印方式重写，保持本项目「不引入第三方依赖」：
- 热敏小票机走 ESC/POS（GBK），Windows 下用 ctypes 直接调 winspool.drv；
- 一条 MC 聊天打一张小票，也可以 ``dry_run`` 只生成预览字节文件。
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

IS_WINDOWS = sys.platform == "win32"

# ESC/POS 控制符
_ESC = 0x1B
_GS = 0x1D
_FS = 0x1C

# EnumPrinters flags
PRINTER_ENUM_LOCAL = 0x00000002
PRINTER_ENUM_CONNECTIONS = 0x00000004

# 打印机属性 / 状态位
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400
PRINTER_STATUS_OFFLINE = 0x00000080


class PrinterError(RuntimeError):
    """打印相关错误。"""


@dataclass(frozen=True)
class Command:
    """一条打印指令，例如 Command("TEXT", "标题 xxx")。"""

    op: str
    arg: str = ""


@dataclass(frozen=True)
class ChatMessage:
    """一条要打印的 MC 聊天。``time`` 是 Unix 时间戳，None 表示现在。"""

    sender: str
    text: str
    kind: str = "chat"          # chat / private / system / bot / actionbar
    time: Optional[float] = None


@dataclass(frozen=True)
class Ticket:
    name: str
    commands: List[Command]


@dataclass(frozen=True)
class PrintResult:
    dry_run: bool
    bytes_written: int
    printer_name: Optional[str] = None
    out_file: Optional[Path] = None


def _cmd(*values: int) -> bytes:
    return bytes(values)


def _qr(data: bytes) -> bytes:
    """生成二维码打印指令（GS ( k）。"""
    length = len(data) + 3
    return b"".join(
        [
            _cmd(_GS, 0x28, 0x6B, 0x04, 0x00, 0x31, 0x41, 0x32, 0x00),
            _cmd(_GS, 0x28, 0x6B, 0x03, 0x00, 0x31, 0x43, 0x06),
            _cmd(_GS, 0x28, 0x6B, 0x03, 0x00, 0x31, 0x45, 0x31),
            _cmd(_GS, 0x28, 0x6B, length & 0xFF, (length >> 8) & 0xFF, 0x31, 0x50, 0x30),
            data,
            _cmd(_GS, 0x28, 0x6B, 0x03, 0x00, 0x31, 0x51, 0x30),
        ]
    )


def render(commands: Iterable[Command], paper_width: int = 58) -> bytes:
    """把指令列表渲染成可直接发给打印机的字节流。"""
    columns = 48 if int(paper_width) == 80 else 32
    buf = bytearray()

    for command in commands:
        op = command.op.strip().upper()
        arg = command.arg

        if op == "INIT":
            buf += _cmd(_ESC, 0x40)
            buf += _cmd(_FS, 0x26)
            buf += _cmd(_ESC, 0x61, 0x00)
            buf += _cmd(_GS, 0x21, 0x00)
            buf += _cmd(_ESC, 0x45, 0x00)
        elif op == "JOBNAME":
            continue
        elif op == "ALIGN":
            mode = {"center": 1, "right": 2}.get(arg.strip().lower(), 0)
            buf += _cmd(_ESC, 0x61, mode)
        elif op == "SIZE":
            try:
                size = int(arg)
            except (TypeError, ValueError):
                size = 1
            size = max(1, min(8, size))
            factor = ((size - 1) << 4) | (size - 1)
            buf += _cmd(_GS, 0x21, factor)
        elif op == "BOLD":
            buf += _cmd(_ESC, 0x45, 1 if arg.strip() == "1" else 0)
        elif op == "TEXT":
            buf += arg.encode("gbk", errors="replace") + b"\n"
        elif op == "HR":
            buf += (b"-" * columns) + b"\n"
        elif op == "QR":
            data = arg.encode("gbk", errors="replace")
            if data:
                buf += _qr(data)
        elif op == "FEED":
            try:
                lines = int(arg)
            except (TypeError, ValueError):
                lines = 3
            lines = max(0, min(255, lines))
            buf += _cmd(_ESC, 0x64, lines)
        elif op == "CUT":
            if arg.strip().lower() == "full":
                buf += _cmd(_GS, 0x56, 0x41, 0x00)
            else:
                buf += _cmd(_GS, 0x56, 0x42, 0x00)
        else:
            raise ValueError(f"未知的打印指令: {op}")

    return bytes(buf)


def encode_text(text: str) -> bytes:
    """按打印机常用的 GBK 编码文本（测试和调试用）。"""
    return text.encode("gbk", errors="replace")


if IS_WINDOWS:  # pragma: no cover - 平台相关，测试里用 dry-run
    from ctypes import wintypes

    _winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)

    class DOC_INFO_1W(ctypes.Structure):
        _fields_ = [
            ("pDocName", wintypes.LPWSTR),
            ("pOutputFile", wintypes.LPWSTR),
            ("pDatatype", wintypes.LPWSTR),
        ]

    class PRINTER_INFO_2W(ctypes.Structure):
        _fields_ = [
            ("pServerName", wintypes.LPWSTR),
            ("pPrinterName", wintypes.LPWSTR),
            ("pShareName", wintypes.LPWSTR),
            ("pPortName", wintypes.LPWSTR),
            ("pDriverName", wintypes.LPWSTR),
            ("pComment", wintypes.LPWSTR),
            ("pLocation", wintypes.LPWSTR),
            ("pDevMode", ctypes.c_void_p),
            ("pSepFile", wintypes.LPWSTR),
            ("pPrintProcessor", wintypes.LPWSTR),
            ("pDatatype", wintypes.LPWSTR),
            ("pParameters", wintypes.LPWSTR),
            ("pSecurityDescriptor", ctypes.c_void_p),
            ("Attributes", wintypes.DWORD),
            ("Priority", wintypes.DWORD),
            ("DefaultPriority", wintypes.DWORD),
            ("StartTime", wintypes.DWORD),
            ("UntilTime", wintypes.DWORD),
            ("Status", wintypes.DWORD),
            ("cJobs", wintypes.DWORD),
            ("AveragePPM", wintypes.DWORD),
        ]

    _winspool.OpenPrinterW.argtypes = [
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.c_void_p,
    ]
    _winspool.OpenPrinterW.restype = wintypes.BOOL
    _winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
    _winspool.ClosePrinter.restype = wintypes.BOOL
    _winspool.StartDocPrinterW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(DOC_INFO_1W),
    ]
    _winspool.StartDocPrinterW.restype = wintypes.DWORD
    _winspool.EndDocPrinter.argtypes = [wintypes.HANDLE]
    _winspool.EndDocPrinter.restype = wintypes.BOOL
    _winspool.StartPagePrinter.argtypes = [wintypes.HANDLE]
    _winspool.StartPagePrinter.restype = wintypes.BOOL
    _winspool.EndPagePrinter.argtypes = [wintypes.HANDLE]
    _winspool.EndPagePrinter.restype = wintypes.BOOL
    _winspool.WritePrinter.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _winspool.WritePrinter.restype = wintypes.BOOL
    _winspool.EnumPrintersW.argtypes = [
        wintypes.DWORD,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _winspool.EnumPrintersW.restype = wintypes.BOOL
    _winspool.GetDefaultPrinterW.argtypes = [
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _winspool.GetDefaultPrinterW.restype = wintypes.BOOL


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise PrinterError("打印功能仅支持 Windows")


def get_default_printer() -> Optional[str]:
    """返回系统默认打印机名字，没有则返回 None。"""
    if not IS_WINDOWS:
        return None
    size = ctypes.wintypes.DWORD(0)
    _winspool.GetDefaultPrinterW(None, ctypes.byref(size))
    if size.value <= 0:
        return None
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _winspool.GetDefaultPrinterW(buffer, ctypes.byref(size)):
        return None
    return buffer.value or None


def list_printers() -> List[Dict]:
    """列出已安装的打印机（含默认、离线标记）。"""
    _require_windows()
    flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    needed = ctypes.wintypes.DWORD(0)
    returned = ctypes.wintypes.DWORD(0)
    _winspool.EnumPrintersW(
        flags, None, 2, None, 0, ctypes.byref(needed), ctypes.byref(returned)
    )
    if needed.value == 0:
        return []

    buffer = None
    for _ in range(3):
        buffer = ctypes.create_string_buffer(needed.value)
        ok = _winspool.EnumPrintersW(
            flags,
            None,
            2,
            ctypes.cast(buffer, ctypes.c_void_p),
            needed.value,
            ctypes.byref(needed),
            ctypes.byref(returned),
        )
        if ok:
            break
        if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise PrinterError(f"枚举打印机失败（错误码 {ctypes.get_last_error()}）")
    else:
        raise PrinterError("枚举打印机失败：缓冲区大小反复变化")
    if buffer is None:
        return []

    array = ctypes.cast(buffer, ctypes.POINTER(PRINTER_INFO_2W))
    default = get_default_printer()
    result: List[Dict] = []
    for index in range(returned.value):
        info = array[index]
        offline = bool(info.Attributes & PRINTER_ATTRIBUTE_WORK_OFFLINE) or bool(
            info.Status & PRINTER_STATUS_OFFLINE
        )
        result.append({
            "name": info.pPrinterName or "",
            "driver": info.pDriverName or "",
            "port": info.pPortName or "",
            "default": info.pPrinterName == default,
            "offline": offline,
        })
    return result


def send_raw(printer_name: str, data: bytes) -> int:
    """以 RAW 方式把字节直接写进 Windows 打印队列，返回写入字节数。"""
    _require_windows()
    name = (printer_name or "").strip()
    if not name:
        raise PrinterError("打印机名字为空")
    if not data:
        raise PrinterError("打印数据为空")

    handle = ctypes.wintypes.HANDLE()
    if not _winspool.OpenPrinterW(name, ctypes.byref(handle), None):
        raise PrinterError(f"打开打印机失败：{name}（错误码 {ctypes.get_last_error()}）")
    try:
        doc = DOC_INFO_1W("Minecraft Chat", None, "RAW")
        if not _winspool.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            raise PrinterError(f"创建打印任务失败（错误码 {ctypes.get_last_error()}）")
        try:
            if not _winspool.StartPagePrinter(handle):
                raise PrinterError(f"开始打印页失败（错误码 {ctypes.get_last_error()}）")
            try:
                written = ctypes.wintypes.DWORD(0)
                buffer = ctypes.create_string_buffer(data, len(data))
                if not _winspool.WritePrinter(
                    handle, buffer, len(data), ctypes.byref(written)
                ):
                    raise PrinterError(f"写入打印机失败（错误码 {ctypes.get_last_error()}）")
                if written.value != len(data):
                    raise PrinterError(
                        f"数据未完整写入：{written.value}/{len(data)} 字节"
                    )
                return written.value
            finally:
                _winspool.EndPagePrinter(handle)
        finally:
            _winspool.EndDocPrinter(handle)
    finally:
        _winspool.ClosePrinter(handle)


def resolve_printer_name(preferred: str = "") -> str:
    """优先用配置里的打印机；否则自动找名字含 XP-58 的小票机（优先在线），再退回系统默认。"""
    name = (preferred or "").strip()
    if name:
        return name
    try:
        candidates = [
            item for item in list_printers()
            if "XP-58" in str(item.get("name") or "").upper()
        ]
        for item in candidates:
            if not item.get("offline"):
                return str(item.get("name") or "")
        if candidates:
            return str(candidates[0].get("name") or "")
    except Exception:  # noqa: BLE001
        pass
    return get_default_printer() or ""


def printer_list_text() -> str:
    try:
        printers = list_printers()
    except Exception as exc:  # noqa: BLE001
        return f"读取打印机失败：{exc}"
    if not printers:
        return "没有检测到打印机，请先安装小票机驱动。"
    lines = []
    for item in printers:
        mark = "*" if item.get("default") else " "
        offline = " [离线]" if item.get("offline") else ""
        lines.append(f"{mark} {item.get('name', '')}{offline}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 文本宽度 / 排版
# ----------------------------------------------------------------------
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\u2600-\u27bf"
    "\u2b00-\u2bff"
    "\ufe0f\u200d"
    "\U0001f1e6-\U0001f1ff"
    "]+"
)
_MC_CODE_RE = re.compile(r"§[0-9a-fk-orA-FK-OR]")


def clean_chat_text(text: str) -> str:
    """清掉 MC 颜色码 / emoji / 控制字符，留下热敏机可打印的文本。"""
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = _MC_CODE_RE.sub("", value)
    value = _EMOJI_RE.sub("", value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or "（空消息）"


_WIDE_RANGES = (
    (0x1100, 0x115F),
    (0x2E80, 0x303E),
    (0x3041, 0x33FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA000, 0xA4CF),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7A3),
    (0xF900, 0xFAFF),
    (0xFE10, 0xFE19),
    (0xFE30, 0xFE6F),
    (0xFF00, 0xFF60),
    (0xFFE0, 0xFFE6),
    (0x1F300, 0x1FAFF),
    (0x20000, 0x3FFFD),
)


def char_width(ch: str) -> int:
    """单个字符在热敏纸上的显示宽度（中文/全角 = 2）。"""
    cp = ord(ch)
    if cp == 0x09:
        return 1
    if cp < 32:
        return 0
    if 0x00B7 <= cp <= 0x00F7:
        return 2
    if cp < 0x1100:
        return 1
    for start, end in _WIDE_RANGES:
        if start <= cp <= end:
            return 2
    return 1


def display_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def wrap_text(text: str, width: int) -> List[str]:
    """按显示宽度换行；尽量在空格/标点处断开。"""
    width = max(1, int(width))
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    out: List[str] = []
    for paragraph in normalized.split("\n"):
        chars = list(re.sub(r"[ \t]+", " ", paragraph).strip())
        if not chars:
            out.append("")
            continue
        line: List[str] = []
        line_width = 0
        last_space = -1

        def flush() -> None:
            nonlocal line, line_width, last_space
            out.append("".join(line))
            line = []
            line_width = 0
            last_space = -1

        for ch in chars:
            w = char_width(ch)
            if line_width + w > width and line:
                if last_space > 0 and last_space >= len(line) * 0.5:
                    tail = line[last_space + 1:]
                    out.append("".join(line[:last_space]))
                    line = tail
                    line_width = sum(char_width(c) for c in tail)
                    last_space = -1
                else:
                    flush()
                if ch == " ":
                    continue
            if ch == " ":
                last_space = len(line)
            line.append(ch)
            line_width += w
        if line:
            out.append("".join(line))
    return out or [""]


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    text = str(text)
    if display_width(text) <= width:
        return text
    limit = max(1, width - display_width(ellipsis))
    out: List[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


def build_chat_ticket(message: ChatMessage, paper_width: int = 58) -> Ticket:
    """把一条 MC 聊天排版成「聊天日志」小票指令。

    只打印聊天数据本身，不加标题 / 分隔线 / 切纸，效果相当于把 chat.log
    直接打到热敏纸上：``[21:20:00] <Steve> 内容``。
    """
    width = 48 if int(paper_width) == 80 else 32
    when = datetime.fromtimestamp(float(message.time)) if message.time else datetime.now()
    sender = truncate(str(message.sender or "").strip() or "未知", 14)
    text = clean_chat_text(message.text)
    if (message.kind or "").strip().lower() == "private":
        text = "[私聊] " + text
    line = f"[{when.strftime('%H:%M:%S')}] <{sender}> {text}"
    lines = wrap_text(line, width)

    commands: List[Command] = [
        Command("JOBNAME", f"MC Chat {sender}"),
        Command("INIT"),
        Command("ALIGN", "left"),
    ]
    for item in lines:
        commands.append(Command("TEXT", item))
    return Ticket(name=f"MC Chat {sender}", commands=commands)


def render_preview(commands: List[Command], width: int = 32) -> str:
    """把指令还原成终端里的排版预览（GUI 测试/排错用）。"""
    align = "left"
    size = 1
    bold = False
    out: List[str] = []
    for command in commands:
        op = command.op.upper()
        arg = command.arg
        if op == "ALIGN":
            align = arg.strip().lower()
        elif op == "SIZE":
            try:
                size = int(arg)
            except (TypeError, ValueError):
                size = 1
        elif op == "BOLD":
            bold = arg.strip() == "1"
        elif op == "TEXT":
            text = arg
            if size >= 2:
                text = "".join(ch + ch for ch in text)
            if align == "center":
                pad = max(0, (width - display_width(text)) // 2)
                text = " " * pad + text
            elif align == "right":
                pad = max(0, width - display_width(text))
                text = " " * pad + text
            out.append(f"**{text}**" if bold else text)
        elif op == "HR":
            out.append("-" * width)
        elif op == "QR":
            out.append(f"[QR] {arg}")
        elif op == "FEED":
            out.append(f"[进纸 {arg} 行]")
        elif op == "CUT":
            out.append("[切纸]")
    return "\n".join(out)


def print_bytes(data: bytes, printer_name: str = "", dry_run: bool = False,
                preview_dir: Optional[Path] = None,
                filename: str = "mcchat-preview.bin") -> PrintResult:
    """打印原始 ESC/POS 字节；``dry_run`` 时写到 preview 目录。"""
    if dry_run:
        target_dir = Path(preview_dir) if preview_dir else Path("preview")
        out_file = target_dir / filename
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(data)
        return PrintResult(dry_run=True, bytes_written=len(data), out_file=out_file)

    name = resolve_printer_name(printer_name)
    if not name:
        raise PrinterError("未指定打印机，也找不到系统默认打印机（请在 GUI 里选择小票机）")
    try:
        written = send_raw(name, data)
    except PrinterError as exc:
        installed = ""
        if IS_WINDOWS:
            try:
                installed = ", ".join(item["name"] for item in list_printers())
            except Exception:  # noqa: BLE001
                installed = ""
        suffix = f"。已安装的打印机：{installed}" if installed else ""
        raise PrinterError(f"{exc}{suffix}") from exc
    return PrintResult(dry_run=False, bytes_written=written, printer_name=name)


def print_chat_ticket(message: ChatMessage, paper_width: int = 58,
                      printer_name: str = "",
                      dry_run: bool = False,
                      preview_dir: Optional[Path] = None) -> PrintResult:
    """打印一条 MC 聊天（只有聊天数据本身）。"""
    ticket = build_chat_ticket(message, paper_width)
    data = render(ticket.commands, paper_width)
    return print_bytes(data, printer_name=printer_name, dry_run=dry_run,
                       preview_dir=preview_dir)


def sample_chat() -> ChatMessage:
    return ChatMessage(
        sender="Steve",
        text="今天挖到钻石啦！有没有人来主城看烟花？",
        kind="chat",
    )


def test_print(paper_width: int = 58,
               printer_name: str = "", dry_run: bool = False,
               preview_dir: Optional[Path] = None) -> PrintResult:
    """打一行示例聊天，给 GUI「测试打印」用。"""
    return print_chat_ticket(sample_chat(), paper_width=paper_width,
                             printer_name=printer_name,
                             dry_run=dry_run, preview_dir=preview_dir)


__all__ = [
    "ChatMessage",
    "Command",
    "PrintResult",
    "PrinterError",
    "Ticket",
    "build_chat_ticket",
    "clean_chat_text",
    "display_width",
    "encode_text",
    "get_default_printer",
    "list_printers",
    "print_bytes",
    "print_chat_ticket",
    "printer_list_text",
    "render",
    "render_preview",
    "resolve_printer_name",
    "sample_chat",
    "send_raw",
    "test_print",
    "truncate",
    "wrap_text",
]
