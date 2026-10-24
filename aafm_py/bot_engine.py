"""Bridge to the headless mineflayer bot engine subprocess (bot_engine.js).

Communicates over stdio with one JSON object per line:
  Python -> Node: {"type": "chat" | "setAutoeat" | "setQuiz" | "ping" | "quit", ...}
  Node -> Python: {"event": ...}
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Any, Literal, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_CONFIG_FILE = os.path.join(BASE_DIR, 'runtime_config.json')

_EVENT_CALLBACK = Callable[[Dict[str, Any]], None]


@dataclass(frozen=True)
class SendResult:
    status: Literal['submitted', 'not_sent', 'unknown']
    error: str = ''


class BotEngine:
    def __init__(self, node_bin: str = 'node', cwd: Optional[str] = None,
                 on_event: Optional[_EVENT_CALLBACK] = None):
        self.node_bin = node_bin
        self.cwd = cwd or BASE_DIR
        self.on_event = on_event
        self.proc: Optional[subprocess.Popen] = None
        self._write_lock = threading.Lock()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running and self.proc is not None and self.proc.poll() is None

    def start(self, runtime_config: Dict[str, Any]) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.stop()

        with open(RUNTIME_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(runtime_config, f, ensure_ascii=False, indent=2)

        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            [self.node_bin, 'bot_engine.js', RUNTIME_CONFIG_FILE],
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            **kwargs,
        )
        self._running = True
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        # 固定住本线程对应的进程：之后 self.proc 可能已被新进程替换（#17）。
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if not line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    ev = {'event': 'log', 'level': 'info', 'message': line}
                if self.on_event:
                    try:
                        self.on_event(ev)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            # 旧进程的读取线程结束时，不能覆盖新进程的运行状态。
            if self.proc is proc:
                self._running = False

    def _read_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                if not line:
                    continue
                line = line.strip()
                if not line:
                    continue
                if self.on_event:
                    try:
                        self.on_event({'event': 'log', 'level': 'error',
                                       'message': '[engine] ' + line})
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

    def send(self, cmd: Dict[str, Any]) -> bool:
        return self.send_result(cmd).status == 'submitted'

    def send_result(self, cmd: Dict[str, Any]) -> SendResult:
        """區分未嘗試寫入與可能部分送出；submitted 不是伺服器回執。"""
        try:
            payload = json.dumps(cmd, ensure_ascii=False) + '\n'
        except Exception as exc:  # noqa: BLE001
            return SendResult('not_sent', type(exc).__name__)
        with self._write_lock:
            proc = self.proc
            try:
                if not self._running or proc is None or proc.poll() is not None or proc.stdin is None:
                    return SendResult('not_sent', 'disconnected')
            except Exception as exc:  # noqa: BLE001
                return SendResult('not_sent', type(exc).__name__)
            try:
                if proc.stdin.write(payload) != len(payload):
                    return SendResult('unknown', 'short_write')
                proc.stdin.flush()
                return SendResult('submitted')
            except Exception as exc:  # noqa: BLE001
                return SendResult('unknown', type(exc).__name__)

    def say(self, text: str) -> bool:
        return self.send({'type': 'chat', 'text': text})

    def say_result(self, text: str) -> SendResult:
        return self.send_result({'type': 'chat', 'text': text})

    def set_autoeat(self, enabled: bool) -> bool:
        return self.send({'type': 'setAutoeat', 'enabled': bool(enabled)})

    def set_quiz(self, enabled: bool) -> bool:
        return self.send({'type': 'setQuiz', 'enabled': bool(enabled)})

    def write_book(self, pages, start_text: str = '', end_text: str = '',
                   end_drop: bool = False, auto_get_book: bool = True,
                   pages_per_book: int = 100, start_page: int = 0,
                   book_start: int = 1, sign_book: bool = False,
                   title_template: str = '') -> bool:
        return self.send({
            'type': 'writeBook',
            'pages': [str(p) for p in (pages or [])],
            'startPage': int(start_page or 0),
            'bookStart': int(book_start or 1),
            'signBook': bool(sign_book),
            'titleTemplate': str(title_template or ''),
            'startText': str(start_text or ''),
            'endText': str(end_text or ''),
            'endDrop': bool(end_drop),
            'autoGetBook': bool(auto_get_book),
            'pagesPerBook': int(pages_per_book or 100),
        })

    def book_control(self, action: str = 'stop') -> bool:
        return self.send({'type': 'bookControl', 'action': str(action or 'stop')})

    def clear_book(self) -> bool:
        return self.send({'type': 'clearBook'})

    def ping(self) -> bool:
        return self.send({'type': 'ping'})

    def stop(self) -> None:
        try:
            self.send({'type': 'quit'})
        except Exception:  # noqa: BLE001
            pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
        self._running = False
