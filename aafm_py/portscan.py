"""服务器端口扫描（找 Minecraft 服务器真正在听的端口）。

为什么需要：很多服务器不是标准 25565（群组服/面板服/多开服会给不同的端口），
填错端口连不上又不提示原因。这里做两件事：

1. **扫端口**：并发 TCP connect，先扫「常见端口」，也可以自定义范围/全端口；
2. **认服务器**：对打开着的端口发 Minecraft 1.7+ 的 status 握手（Server List Ping），
   能拿到版本号 / MOTD / 在线人数 —— 一眼就能看出哪个端口才是 MC。

纯标准库（socket/struct/json），不新增依赖。
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

#: 常见端口：25565 前后的一段 + 一些面板/插件常用端口。
COMMON_PORTS: List[int] = [
    25565, 25566, 25567, 25568, 25569, 25570, 25571, 25572, 25573, 25574, 25575,
    25576, 25577, 25578, 25579, 25580, 25581, 25582, 25583, 25584, 25585,
    19132, 19133,            # 基岩版（TCP 上一般不开，扫到会标成非 MC）
    8123, 8080, 3000, 3001,  # dynmap / 面板
    22, 80, 443, 3306, 6379,  # 常见运维端口，方便确认机器在线
]

#: 默认并发数（太高容易被防火墙当扫描；64 在本地网络很快也很温和）
DEFAULT_WORKERS = 64


def _scan_one(host: str, port: int, timeout: float) -> bool:
    """TCP connect 一次：通就是 open。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(max(0.05, timeout))
    try:
        return sock.connect_ex((host, int(port))) == 0
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def scan(host: str, ports: Iterable[int], timeout: float = 0.35,
         workers: int = DEFAULT_WORKERS,
         should_stop: Optional[Callable[[], bool]] = None) -> List[int]:
    """并发扫描一组端口，返回**开放**的端口（升序）。"""
    host = (host or '').strip()
    if not host:
        return []
    todo = [int(p) for p in ports if 0 < int(p) < 65536]
    if not todo:
        return []
    open_ports: List[int] = []
    lock = threading.Lock()
    index = [0]

    def worker() -> None:
        while True:
            with lock:
                if index[0] >= len(todo):
                    return
                port = todo[index[0]]
                index[0] += 1
            if should_stop is not None and should_stop():
                return
            if _scan_one(host, port, timeout):
                with lock:
                    open_ports.append(port)

    threads = []
    for _ in range(max(1, min(workers, len(todo)))):
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return sorted(open_ports)


def scan_range(host: str, start: int, end: int, timeout: float = 0.35,
               workers: int = DEFAULT_WORKERS,
               should_stop: Optional[Callable[[], bool]] = None) -> List[int]:
    """扫描 [start, end] 闭区间。"""
    lo = max(1, min(int(start), int(end)))
    hi = min(65535, max(int(start), int(end)))
    return scan(host, range(lo, hi + 1), timeout=timeout, workers=workers,
                should_stop=should_stop)


# ----------------------------------------------------------------------
# Minecraft Server List Ping（1.7+ 的 status 协议）
# ----------------------------------------------------------------------

def _encode_varint(value: int) -> bytes:
    out = b''
    value &= 0xFFFFFFFF
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out += bytes((byte | 0x80,))
        else:
            out += bytes((byte,))
            return out


def _read_varint(sock: socket.socket) -> int:
    value = 0
    for shift in range(0, 35, 7):
        raw = sock.recv(1)
        if not raw:
            raise OSError('连接被关闭')
        byte = raw[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
    raise OSError('varint 太长')


def minecraft_status(host: str, port: int,
                     timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """对端口发 status 握手；是 MC 服务器就返回版本/MOTD/人数，否则 None。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(max(0.3, timeout))
    try:
        if sock.connect_ex((host, int(port))) != 0:
            return None
        addr = host.encode('utf-8')
        payload = (b'\x00' + _encode_varint(-1 & 0xFFFFFFFF)
                   + _encode_varint(len(addr)) + addr
                   + struct.pack('>H', int(port)) + _encode_varint(1))
        sock.sendall(_encode_varint(len(payload)) + payload)
        sock.sendall(_encode_varint(1) + b'\x00')
        _read_varint(sock)                    # 包长度
        packet_id = _read_varint(sock)
        if packet_id != 0:
            return None
        length = _read_varint(sock)
        if length <= 0 or length > 4 << 20:
            return None
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = sock.recv(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = json.loads(b''.join(chunks).decode('utf-8', 'replace'))
        version = data.get('version') or {}
        players = data.get('players') or {}
        return {
            'port': int(port),
            'version': str(version.get('name') or ''),
            'protocol': version.get('protocol'),
            'online': players.get('online'),
            'max': players.get('max'),
            'motd': _motd_text(data.get('description')),
        }
    except (OSError, ValueError, struct.error):
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _motd_text(description: Any) -> str:
    """MOTD 可能是字符串，也可能是组件树，拍平成纯文本。"""
    if description is None:
        return ''
    if isinstance(description, str):
        return description
    if isinstance(description, dict):
        text = str(description.get('text') or '')
        for child in description.get('extra') or []:
            text += _motd_text(child)
        return text
    if isinstance(description, list):
        return ''.join(_motd_text(item) for item in description)
    return str(description)


def scan_with_identify(host: str, ports: Iterable[int], timeout: float = 0.35,
                       identify_timeout: float = 2.0) -> List[Dict[str, Any]]:
    """先扫端口，再对开放端口认 MC（能认出来的带版本/MOTD/人数）。"""
    result: List[Dict[str, Any]] = []
    for port in scan(host, ports, timeout=timeout):
        info = minecraft_status(host, port, timeout=identify_timeout)
        result.append({'port': port, 'minecraft': bool(info), 'info': info or {}})
    return result


def scan_common(host: str, timeout: float = 0.35) -> List[Dict[str, Any]]:
    """扫常见端口并认 MC。"""
    return scan_with_identify(host, COMMON_PORTS, timeout=timeout)


if __name__ == '__main__':  # 手动跑：python -m aafm_py.portscan <host> [start end]
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    started = time.time()
    if len(sys.argv) >= 4:
        rows = scan_with_identify(target, range(int(sys.argv[2]), int(sys.argv[3]) + 1))
    else:
        rows = scan_common(target)
    for row in rows:
        info = row['info']
        if row['minecraft']:
            print("%s:%s  Minecraft %s  %s/%s  %s" % (
                target, row['port'], info.get('version'), info.get('online'),
                info.get('max'), info.get('motd')))
        else:
            print("%s:%s  open" % (target, row['port']))
    print('耗时 %.1fs' % (time.time() - started))
