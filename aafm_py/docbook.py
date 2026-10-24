"""Turn document files (.txt / .docx / .doc) into Minecraft book pages.

The result of :func:`load_pages` is a list of plain strings, each one a page of
an in-game *book and quill* (书与笔).  A Java Edition page holds up to 1024
characters (100 pages per book), so long documents are split automatically.

``.txt`` and ``.docx`` are parsed with the standard library only (``zipfile``
for the Office Open XML package).  Legacy ``.doc`` (the binary OLE compound
format) is handled best-effort by scanning the raw bytes for readable text, so
the output may contain some noise.
"""
from __future__ import annotations

import os
import zipfile
from typing import List, Optional
from xml.etree import ElementTree as ET

# Java Edition "book and quill" limits.
MAX_PAGE_CHARS = 1024
MAX_PAGES_PER_BOOK = 100

_W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

_TEXT_ENCODINGS = ('utf-8-sig', 'utf-8', 'gb18030', 'utf-16', 'big5', 'latin-1')


# ---------------------------------------------------------------------------
# Plain text (.txt)
# ---------------------------------------------------------------------------
def read_text(path: str) -> str:
    with open(path, 'rb') as f:
        data = f.read()
    for enc in _TEXT_ENCODINGS:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# Word .docx (Office Open XML)
# ---------------------------------------------------------------------------
def read_docx(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            try:
                xml = z.read('word/document.xml')
            except KeyError:
                # Some producers name it differently; fall back to the first part.
                names = [n for n in z.namelist() if n.endswith('.xml')]
                if not names:
                    return ''
                xml = z.read(names[0])
    except (zipfile.BadZipFile, OSError):
        return ''

    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ''

    lines: List[str] = []
    for para in root.iter(_W_NS + 'p'):
        parts: List[str] = []
        for node in para.iter():
            tag = node.tag
            if tag == _W_NS + 't':
                parts.append(node.text or '')
            elif tag == _W_NS + 'tab':
                parts.append('\t')
            elif tag in (_W_NS + 'br', _W_NS + 'cr'):
                parts.append('\n')
        text = ''.join(parts)
        lines.append(text)
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Legacy .doc (binary) - best effort
# ---------------------------------------------------------------------------
def _visible_char(ch: str) -> bool:
    code = ord(ch)
    if ch in '\n\r\t':
        return True
    if 0x20 <= code <= 0x7e:
        return True
    if 0x4e00 <= code <= 0x9fff:      # CJK unified ideographs
        return True
    if 0x3400 <= code <= 0x4dbf:      # CJK extension A
        return True
    if 0x3000 <= code <= 0x30ff:      # CJK punctuation / kana
        return True
    if 0xff00 <= code <= 0xffef:      # full-width forms
        return True
    if 0x2010 <= code <= 0x2027:      # dashes / quotes
        return True
    return False


def _readable_runs(text: str, min_run: int = 4) -> str:
    out: List[str] = []
    buf: List[str] = []
    for ch in text:
        if _visible_char(ch):
            buf.append(ch)
        else:
            if len(buf) >= min_run:
                out.append(''.join(buf))
            buf = []
    if len(buf) >= min_run:
        out.append(''.join(buf))
    return '\n'.join(out)


def _score(text: str) -> int:
    ascii_alnum = sum(1 for c in text if c.isascii() and c.isalnum())
    cjk = sum(1 for c in text if 0x3400 <= ord(c) <= 0x9fff)
    return ascii_alnum + cjk * 3


def read_doc(path: str) -> str:
    with open(path, 'rb') as f:
        data = f.read()

    candidates: List[str] = []
    try:
        candidates.append(_readable_runs(data.decode('utf-16-le', errors='ignore')))
    except Exception:  # noqa: BLE001
        pass
    try:
        candidates.append(_readable_runs(data.decode('cp1252', errors='ignore')))
    except Exception:  # noqa: BLE001
        pass
    try:
        candidates.append(_readable_runs(data.decode('gb18030', errors='ignore')))
    except Exception:  # noqa: BLE001
        pass

    if not candidates:
        return ''
    return max(candidates, key=_score)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def read_document(path: str) -> str:
    """Read a supported document and return its plain text."""
    ext = os.path.splitext(path)[1].lower()
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if ext == '.docx':
        text = read_docx(path)
    elif ext == '.doc':
        text = read_doc(path)
    else:
        text = read_text(path)
    return _normalize(text)


def _normalize(text: str) -> str:
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('\x0c', '\n').replace('\u3000', ' ')
    text = text.replace('\x00', '')
    # Normalise tabs (Minecraft book pages have no tab handling).
    text = text.replace('\t', '    ')
    return text


def _utf16_len(text: str) -> int:
    """Length the way Java's ``String.length()`` counts it (BMP = 1, astral = 2).

    Minecraft validates a book page against the UTF-16 length, so an emoji counts
    as two characters.
    """
    n = 0
    for ch in text:
        n += 2 if ord(ch) > 0xFFFF else 1
    return n


def _wrap_line(line: str, max_chars: int) -> List[str]:
    """Split one line into chunks whose UTF-16 length is at most ``max_chars``."""
    if line == '':
        return ['']
    chunks: List[str] = []
    buf: List[str] = []
    width = 0
    for ch in line:
        w = 2 if ord(ch) > 0xFFFF else 1
        if width + w > max_chars and buf:
            chunks.append(''.join(buf))
            buf = []
            width = 0
        buf.append(ch)
        width += w
    if buf:
        chunks.append(''.join(buf))
    return chunks


def split_pages(text: str, max_chars: int = MAX_PAGE_CHARS,
                max_pages: Optional[int] = None) -> List[str]:
    """Split ``text`` into book pages, each at most ``max_chars`` characters."""
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = MAX_PAGE_CHARS
    if max_chars < 1:
        max_chars = MAX_PAGE_CHARS

    text = _normalize(text).strip('\n')
    if not text:
        return []

    pages: List[str] = []
    current = ''
    for line in text.split('\n'):
        for seg in _wrap_line(line, max_chars):
            candidate = seg if not current else current + '\n' + seg
            if _utf16_len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    pages.append(current)
                current = seg
    if current:
        pages.append(current)

    if max_pages is not None:
        pages = pages[:max_pages]
    return pages


# ---------------------------------------------------------------------------
# Automatic page layout (auto wrap + auto page turn)
#
# Java Edition fits about 14 lines of text on a page and wraps a line once it
# gets wider than the page (114 px, default font). We reproduce that layout so
# the generated book looks like it was typed in-game instead of overflowing the
# page: each paragraph is word-wrapped to the page width, and once a page holds
# the maximum number of lines the remaining text flows onto the next page.
# ---------------------------------------------------------------------------
PAGE_LINE_WIDTH = 112        # wrap width: Java page text area is 114 px, we leave a 2 px safety margin
GAME_PAGE_WIDTH = 114        # px, Java Edition page text width (used for reference/preview)
MAX_LINES_PER_PAGE = 14      # lines that fit on one Java Edition page (hard limit)


# Advance width (px) of characters in Minecraft's default font. We deliberately
# OVER-estimate: if our width were smaller than the game's, the game would wrap
# our line again and the page would end up with more than 14 rendered lines
# (which a signed book silently truncates). Over-estimating only makes a line a
# little shorter, which is always safe.
#   - ASCII: real advances are at most 6 px (7 px for '@' and '~').
#   - Non-ASCII (CJK / full-width / symbols): the legacy unicode font renders
#     these at up to ~10 px, so we count them as 10.
_NARROW = frozenset("!.,:;'|i")
_WIDE_ASCII = frozenset("@~")   # the widest ASCII glyphs (7px)


def char_width(ch: str) -> int:
    code = ord(ch)
    if code < 0x80:
        if ch == ' ':
            return 4
        if ch in _WIDE_ASCII:
            return 7
        if ch in _NARROW:
            return 3
        return 6
    # Any non-ASCII character is treated as a full-width glyph (10px) so CJK,
    # full-width punctuation, emoji, ... can never overflow a line.
    return 10


def line_width(text: str) -> int:
    return sum(char_width(c) for c in text)


def wrap_line(text: str, max_width: int = PAGE_LINE_WIDTH) -> List[str]:
    """Word-wrap ``text`` into lines no wider than ``max_width`` pixels."""
    if text == '':
        return ['']
    out: List[str] = []
    cur = ''
    cur_w = 0
    last_space = -1
    for ch in text:
        w = char_width(ch)
        if cur and cur_w + w > max_width:
            if last_space > 0:
                out.append(cur[:last_space])
                cur = cur[last_space + 1:] + ch
                cur_w = line_width(cur)
            else:
                out.append(cur)
                cur = ch
                cur_w = w
            last_space = cur.rfind(' ')
        else:
            if ch == ' ':
                last_space = len(cur)
            cur += ch
            cur_w += w
    out.append(cur)
    return out


def paginate(text: str, max_chars: int = MAX_PAGE_CHARS,
             max_lines: int = MAX_LINES_PER_PAGE,
             max_width: int = PAGE_LINE_WIDTH,
             max_pages: Optional[int] = None) -> List[str]:
    """Lay out ``text`` into book pages, wrapping lines and turning pages.

    A page turns when it reaches ``max_lines`` rendered lines — the line count is
    authoritative. ``max_chars`` is only a safety ceiling (the game's hard limit,
    1024) so a page can never be rejected; it does not shorten pages. Long words
    are hard-broken so nothing overflows.
    """
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = MAX_PAGE_CHARS
    if max_chars < 1:
        max_chars = MAX_PAGE_CHARS
    try:
        max_lines = int(max_lines)
    except (TypeError, ValueError):
        max_lines = MAX_LINES_PER_PAGE
    if max_lines < 1:
        max_lines = MAX_LINES_PER_PAGE
    # Hard game limit: a Java book page only fits 14 lines, so never allow more
    # (a signed book would silently truncate the overflow).
    if max_lines > MAX_LINES_PER_PAGE:
        max_lines = MAX_LINES_PER_PAGE

    text = _normalize(text).strip('\n')
    if not text:
        return []

    layout: List[str] = []
    for raw_line in text.split('\n'):
        layout.extend(wrap_line(raw_line, max_width))

    # Single strict pass: append each rendered line to the current page until it
    # holds ``max_lines`` lines or would exceed ``max_chars`` characters, then
    # turn to a new page. This guarantees a page can never overflow in-game.
    pages: List[str] = []
    cur_lines: List[str] = []
    cur_len = 0
    for wl in layout:
        # A rendered line longer than the char budget can only happen with a
        # huge max_width; hard-split it by UTF-16 units (not Python code
        # points), otherwise an emoji counts as 1 here but 2 in Java and the
        # page can exceed the budget (#31).
        if _utf16_len(wl) > max_chars:
            pieces = _wrap_line(wl, max_chars)
        else:
            pieces = [wl]
        for piece in pieces:
            add = _utf16_len(piece) + (1 if cur_lines else 0)
            if cur_lines and (len(cur_lines) >= max_lines or cur_len + add > max_chars):
                pages.append('\n'.join(cur_lines))
                cur_lines = []
                cur_len = 0
                add = _utf16_len(piece)
            cur_lines.append(piece)
            cur_len += add
    if cur_lines:
        pages.append('\n'.join(cur_lines))

    if max_pages is not None and len(pages) > max_pages:
        pages = pages[:max_pages]
    return pages


def load_pages(path: str, max_chars: int = MAX_PAGE_CHARS,
               max_lines: int = MAX_LINES_PER_PAGE,
               max_pages: Optional[int] = None) -> List[str]:
    """Read a document and lay it out into book pages in one call."""
    return paginate(read_document(path), max_chars=max_chars,
                    max_lines=max_lines, max_pages=max_pages)
