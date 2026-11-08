"""Low-level persistence of the in-app log.

All log / chat / server lines are buffered in memory and flushed to disk under
``logs/`` as ``<yyyy>_<mm>_<dd>_第N次.txt``:

- once whenever the calendar day rolls over while the app is running;
- once when the app closes.

``N`` is the sequence number for that date (第1次, 第2次, ...), derived from the
files already present so nothing is ever overwritten.

This module is pure standard library and has no UI dependency; the controller
owns a single :class:`LogSaver` instance.
"""
from __future__ import annotations

import datetime
import os
import re
import threading
from typing import List, Optional

DEFAULT_LOG_DIR = 'logs'


class LogSaver:
    def __init__(self, base_dir: str, log_dir: str = DEFAULT_LOG_DIR,
                 enabled: bool = True):
        self.base_dir = base_dir
        self.enabled = bool(enabled)
        self.dir = log_dir if os.path.isabs(log_dir) else os.path.join(base_dir, log_dir)
        self._lock = threading.Lock()
        self._buffer: List[str] = []
        # Date the current buffer started on (i.e. which day these lines belong
        # to). ``None`` means the buffer is empty.
        self._buffer_date: Optional[datetime.date] = None

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def add(self, text) -> None:
        """Append one log line (with a timestamp) to the in-memory buffer."""
        if not self.enabled:
            return
        text = str(text if text is not None else '').rstrip('\n')
        if not text:
            return
        now = datetime.datetime.now()
        stamp = now.strftime('%Y-%m-%d %H:%M:%S')
        with self._lock:
            if self._buffer_date is None:
                self._buffer_date = now.date()
            self._buffer.append(f'[{stamp}] {text}')

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------
    def maybe_roll(self) -> Optional[str]:
        """Flush the buffer if the calendar day changed. Returns the file path."""
        if not self.enabled:
            return None
        today = datetime.date.today()
        with self._lock:
            if self._buffer_date is None or self._buffer_date == today:
                return None
            date = self._buffer_date
        return self._save(date)

    def flush(self) -> Optional[str]:
        """Flush the current buffer (called on shutdown). Returns the file path."""
        if not self.enabled:
            return None
        with self._lock:
            date = self._buffer_date
        if date is None:
            return None
        return self._save(date)

    def _save(self, date: datetime.date) -> Optional[str]:
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError:
            return None

        with self._lock:
            lines = self._buffer
            self._buffer = []
            self._buffer_date = None
        if not lines:
            return None

        prefix = date.strftime('%Y_%m_%d')
        index = self._next_index(prefix)
        try:
            while True:
                path = os.path.join(self.dir, f'{prefix}_第{index}次.txt')
                try:
                    # Reserve the name atomically, including across instances.
                    with open(path, 'x', encoding='utf-8') as f:
                        f.write('\n'.join(lines) + '\n')
                    break
                except FileExistsError:
                    index += 1
        except OSError:
            # Writing failed: put the lines back so nothing is lost.
            with self._lock:
                self._buffer[:0] = lines
                if self._buffer_date is None:
                    self._buffer_date = date
            return None
        return path

    def _next_index(self, prefix: str) -> int:
        pattern = re.compile(r'^' + re.escape(prefix) + r'_第(\d+)次\.txt$')
        highest = 0
        try:
            for name in os.listdir(self.dir):
                m = pattern.match(name)
                if m:
                    highest = max(highest, int(m.group(1)))
        except OSError:
            pass
        return highest + 1
