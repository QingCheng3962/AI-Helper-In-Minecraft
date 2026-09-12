"""Local "who is this player?" fact table, loaded from .xlsx / .csv files.

Column model (by header / position)
-----------------------------------
* Column 1      : 玩家名 (player name)
* Column 2      : 玩家描述 (authoritative description; may be empty)
* Columns 3+    : 描述1 / 描述2 / ... (fixed random answers, optional)
* Trailing cols : AI记录1 .. AI记录5 (personality notes written by the LLM
                  after it reads chat history; newest note goes in AI记录1 and
                  older ones roll right / get dropped past five).

Answers to "xxx是谁" use the authoritative 玩家描述 when set, otherwise one of
描述1..N at random. AI记录 only stores personality research and never affects
those answers. When a server player tells the bot ``X是Y`` the statement is
written back into the authoritative 玩家描述 cell and persists across restarts.
"""
from __future__ import annotations

import csv
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

# Serialises file writes (learn / AI-record) from several bot threads.
_FILE_LOCK = threading.RLock()

_HEADER_HINTS = {
    '玩家', '玩家名', '玩家名称', '玩家名字', '玩家id', '玩家username',
    '名字', '名称', '昵称', '姓名', '用户名', 'id', 'name',
}

_HEADER_LOWER = {str(h).casefold() for h in _HEADER_HINTS}

# Column headers that are treated as the authoritative 玩家描述 column.
_MAIN_HEADERS = {
    '玩家描述', '主要描述', '主描述', '真实身份', '身份', '人物描述',
    '描述', '介绍', 'playerdesc', 'description',
}

_MAIN_HEADER_NAME = '玩家描述'  # used when the sheet has no authoritative column
_MAIN_HEADER_LOWER = {h.casefold() for h in _MAIN_HEADERS}

# Three alias columns right after 玩家描述 (别名1 | 别名2 | 别名3). A plain
# 玩家别名 / 别名 header is also accepted as a single alias column.
_ALIAS_HDR_RE = re.compile(r'^(?:玩家)?别名(?:[1-3])?$')
_ALIAS_SLOTS = 3
_ALIAS_SEP = '、'
_ALIAS_SPLIT_RE = re.compile(r'[、，,;；/|]+')

_AI_HEADER_RE = re.compile(r'^ai记录[1-5]$')
_AI_COUNT = 5

_MAX_NAME_LEN = 64  # sanity guard; longer first cells are treated as headers


class RosterError(Exception):
    """Raised when a spreadsheet cannot be parsed or written."""


def _normalize_name(name: str) -> str:
    return re.sub(r'\s+', ' ', str(name or '')).strip()


def _to_text(v) -> str:
    if v is None:
        return ''
    return str(v).strip()


def _alias_cols_from_header(header: Optional[List[str]]) -> List[int]:
    """Column indexes (0-based) that hold aliases (别名1..别名3)."""
    if header is None:
        return []
    cols = [i for i in range(1, len(header))
            if _ALIAS_HDR_RE.match(str(header[i]).strip().casefold())]
    return cols[:_ALIAS_SLOTS]


def split_aliases(text) -> List[str]:
    """Split a cell (or list) of aliases into trimmed single aliases."""
    out: List[str] = []
    for part in _ALIAS_SPLIT_RE.split(str(text or '')):
        p = part.strip(' \u3000')
        if p:
            out.append(p)
    return out


def _is_header_row(row: List[str]) -> bool:
    """Heuristic: is this row a column header (玩家名 / 描述1 / ...)?"""
    if not row:
        return True
    if row[0].casefold() in _HEADER_LOWER:
        return True
    if len(row[0]) > _MAX_NAME_LEN:
        return True
    return False


def _read_rows(path: str) -> List[List[str]]:
    """Read a sheet into padded rows of trimmed strings (empties preserved)."""
    ext = os.path.splitext(path)[1].lower()
    if not os.path.exists(path):
        raise RosterError('文件不存在: ' + path)
    if ext == '.xlsx':
        try:
            from openpyxl import load_workbook
        except Exception as e:  # noqa: BLE001
            raise RosterError('读取 xlsx 需要安装 openpyxl: ' + str(e))
        try:
            wb = load_workbook(path, read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001
            raise RosterError(f'无法打开 {os.path.basename(path)}: {e}')
        try:
            ws = wb.worksheets[0] if wb.worksheets else None
            if ws is None:
                raise RosterError('文件中没有工作表。')
            raw_rows: List[List[str]] = []
            for row in ws.iter_rows(values_only=True):
                vals = [_to_text(v) for v in row]
                if any(vals):
                    raw_rows.append(vals)
        finally:
            wb.close()
    elif ext == '.csv':
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            raise RosterError(f'无法读取 {os.path.basename(path)}: {e}')
        text = None
        for enc in ('utf-8-sig', 'gb18030', 'utf-8'):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            raise RosterError('无法识别 CSV 编码（尝试 UTF-8 / GB18030）。')
        raw_rows = [[c.strip() for c in row]
                    for row in csv.reader(text.splitlines())]
        raw_rows = [r for r in raw_rows if any(r)]
    else:
        raise RosterError('仅支持 .xlsx 或 .csv 文件。')

    width = max((len(r) for r in raw_rows), default=0)
    return [r + [''] * (width - len(r)) for r in raw_rows]


def _classify_headers(header: Optional[List[str]],
                      width: int) -> Tuple[Optional[int], List[int], Optional[int]]:
    """Map columns to (main_index, fixed_indices, ai_base_index).

    Column 0 is always the name. The authoritative 玩家描述 column is picked
    by its header text when present. AI记录1..5 and 别名1..3 columns are
    recognised by header and are never part of the fixed random answers.
    Header-less sheets behave like before (a 2-column sheet => name | 玩家描述;
    wider => random answers only). ``ai_base`` is the column of AI记录1.
    """
    if header is None:
        ai_base = None
        if width <= 2:
            return 1, [], None
        return None, list(range(1, width)), None

    lower = [str(h).strip().casefold() for h in header]
    ai_base = None
    for i in range(1, len(lower)):
        if _AI_HEADER_RE.match(lower[i]):
            ai_base = i
            break
    alias_cols = _alias_cols_from_header(header)
    alias_set = set(alias_cols)

    def is_special(i: int) -> bool:
        return i in alias_set or (ai_base is not None
                                  and ai_base <= i < ai_base + _AI_COUNT)

    main_idx: Optional[int] = None
    for i in range(1, len(lower)):
        if is_special(i):
            continue
        if lower[i] in _MAIN_HEADERS:
            main_idx = i
            break

    if main_idx is not None:
        fixed = [i for i in range(1, len(lower))
                 if i != main_idx and not is_special(i)]
        return main_idx, fixed, ai_base

    # No authoritative header.
    if len(lower) > 1 and any(lower[1:]):
        fixed = [i for i in range(1, len(lower)) if not is_special(i)]
        return None, fixed, ai_base
    return None, list(range(1, width)), ai_base


def parse_file(path: str) -> List[Dict[str, Any]]:
    """Parse a spreadsheet into records
    ``{'name', 'main', 'aliases', 'fixed', 'ai'}`` (fixed = 描述1..N answers,
    aliases = up to three 别名 cells, ai = up to five AI记录 slots)."""
    grid = _read_rows(path)
    if not grid:
        return []
    header = grid[0] if _is_header_row(grid[0]) else None
    rows = grid[1:] if header is not None else grid
    main_idx, fixed_idx, ai_base = _classify_headers(header, len(grid[0]))
    alias_cols = _alias_cols_from_header(header)

    def at(row: List[str], i: Optional[int]) -> str:
        return row[i].strip() if i is not None and 0 <= i < len(row) else ''

    entries: List[Dict[str, Any]] = []
    for row in rows:
        name = _normalize_name(row[0]) if row else ''
        if not name:
            continue
        main = at(row, main_idx)
        aliases = [at(row, i) for i in alias_cols]
        fixed = [at(row, i) for i in fixed_idx if at(row, i)]
        ai = []
        if ai_base is not None:
            ai = [at(row, ai_base + k) for k in range(_AI_COUNT)]
        entries.append({'name': name, 'main': main, 'aliases': aliases,
                        'fixed': fixed, 'ai': ai})
    return entries


# ---------------------------------------------------------------------------
# Helpers to locate / create a header row and columns in an openpyxl sheet
# ---------------------------------------------------------------------------
def _find_header_in_ws(ws) -> Tuple[Optional[int], Optional[List[str]]]:
    for r, row in enumerate(ws.iter_rows(min_row=1,
                                         max_row=min(ws.max_row, 3),
                                         values_only=True), start=1):
        cells = [_to_text(v) for v in row]
        if _is_header_row(cells):
            return r, cells
    return None, None


def _ensure_header_xlsx(ws) -> int:
    """Make sure row 1 of ``ws`` is a header row; return its row number."""
    header_row_no, header_cells = _find_header_in_ws(ws)
    if header_row_no is not None:
        return header_row_no
    # header-less sheet: synthesise labels from the current columns.
    ws.insert_rows(1)
    header_row_no = 1
    width = max(ws.max_column, 1)
    ws.cell(row=1, column=1, value='玩家名')
    if width >= 2:
        if width == 2:
            ws.cell(row=1, column=2, value=_MAIN_HEADER_NAME)
        else:
            for col in range(2, width + 1):
                ws.cell(row=1, column=col, value=f'描述{col - 1}')
    return 1


def _find_ai_start_xlsx(ws, header_row_no: int) -> int:
    """Return the (1-based) column of AI记录1, appending it when missing."""
    ai_start: Optional[int] = None
    for col in range(1, ws.max_column + 1):
        v = str(ws.cell(row=header_row_no, column=col).value or '').strip()
        if _AI_HEADER_RE.match(v.casefold()):
            ai_start = col
            break
    if ai_start is None:
        ai_start = max(ws.max_column, 2) + 1
        for k in range(_AI_COUNT):
            ws.cell(row=header_row_no, column=ai_start + k,
                    value=f'AI记录{k + 1}')
    return ai_start


def _find_main_col_xlsx(ws, header_row_no: int) -> Optional[int]:
    for col in range(2, ws.max_column + 1):
        h = str(ws.cell(row=header_row_no, column=col).value or '').strip()
        if h.casefold() in _MAIN_HEADER_LOWER:
            return col
    return None


def _write_xlsx(path: str, name: str, note: str, ai: bool = True,
                main: Optional[str] = None) -> None:
    """Shared writer: set the authoritative main text and/or roll an AI note."""
    try:
        from openpyxl import load_workbook
    except Exception as e:  # noqa: BLE001
        raise RosterError('写入 xlsx 需要安装 openpyxl: ' + str(e))
    if not os.path.exists(path):
        raise RosterError('文件不存在: ' + path)
    try:
        wb = load_workbook(path)
    except Exception as e:  # noqa: BLE001
        raise RosterError(f'无法打开 {os.path.basename(path)}: {e}')
    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            raise RosterError('文件中没有工作表。')
        header_row_no = _ensure_header_xlsx(ws)
        target = _normalize_name(name)
        found = None
        for r in range(header_row_no + 1, ws.max_row + 1):
            if _normalize_name(ws.cell(row=r, column=1).value) == target:
                found = r
                break
        if found is None:
            found = ws.max_row + 1
            ws.cell(row=found, column=1, value=name)

        if main is not None:
            main_col = _find_main_col_xlsx(ws, header_row_no)
            if main_col is None:
                # No authoritative column: insert one as column 2 (B) so the
                # existing 描述1..N columns shift right but are never deleted.
                ws.insert_cols(2)
                main_col = 2
                ws.cell(row=header_row_no, column=main_col,
                        value=_MAIN_HEADER_NAME)
            ws.cell(row=found, column=main_col, value=main)

        if ai:
            ai_start = _find_ai_start_xlsx(ws, header_row_no)
            old = [str(ws.cell(row=found, column=ai_start + k).value or '')
                   for k in range(_AI_COUNT)]
            new = ([note] + [o for o in old if o.strip()])[:_AI_COUNT]
            new += [''] * (_AI_COUNT - len(new))
            for k in range(_AI_COUNT):
                ws.cell(row=found, column=ai_start + k, value=new[k])

        wb.save(path)
    except PermissionError:
        raise RosterError(f'无法写入 {os.path.basename(path)}（文件可能正被占用）。')
    except RosterError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RosterError(f'写入 {os.path.basename(path)} 失败: {e}')
    finally:
        wb.close()


def write_main(path: str, name: str, text: str) -> None:
    """Write ``text`` into the 玩家描述 column of the matching row.

    描述1..N and AI记录1..5 columns are never removed. If no row matches a new
    row is appended. A header-less sheet gets a header row first.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        _write_csv(path, name=name, main=text, ai=False, note='')
    elif ext == '.xlsx':
        _write_xlsx(path, name, '', ai=False, main=text)
    else:
        raise RosterError('仅支持写入 .xlsx 或 .csv 文件。')


def write_ai_record(path: str, name: str, note: str) -> None:
    """Roll ``note`` into AI记录1 of the matching row (old ones shift right,
    overflow past five is dropped). Appends the row when missing."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        _write_csv(path, name=name, main=None, ai=True, note=note)
    elif ext == '.xlsx':
        _write_xlsx(path, name, note, ai=True, main=None)
    else:
        raise RosterError('仅支持写入 .xlsx 或 .csv 文件。')


def _alias_dup_ok(existing_cells: List[str], token: str) -> bool:
    """True when ``token`` is not already present in any existing alias cell."""
    t = _normalize_name(token).casefold()
    for cell in existing_cells:
        for part in split_aliases(cell):
            if _normalize_name(part).casefold() == t:
                return False
    return True


def write_aliases(path: str, name: str, new_tokens) -> List[str]:
    """Fill new aliases into the first empty 别名 column of the matching row.

    Manually filled cells are never overwritten. Returns the final alias cell
    list (up to three entries, '' for empty slots).
    """
    tokens = []
    seen = set()
    for t in (new_tokens or []):
        s = str(t or '').strip()
        k = _normalize_name(s).casefold()
        if s and k not in seen:
            seen.add(k)
            tokens.append(s)

    ext = os.path.splitext(path)[1].lower()
    if ext == '.csv':
        return _write_csv_aliases(path, name, tokens)
    if ext == '.xlsx':
        return _write_xlsx_aliases(path, name, tokens)
    raise RosterError('仅支持写入 .xlsx 或 .csv 文件。')


def _alias_join(cells: List[str]) -> str:
    return _ALIAS_SEP.join(c for c in cells if c.strip())


def _find_alias_cols_xlsx(ws, header_row_no: int) -> List[int]:
    cols = []
    for col in range(2, ws.max_column + 1):
        v = str(ws.cell(row=header_row_no, column=col).value or '').strip()
        if _ALIAS_HDR_RE.match(v.casefold()):
            cols.append(col)
    return cols[:_ALIAS_SLOTS]


def _ensure_alias_cols_xlsx(ws, header_row_no: int) -> List[int]:
    cols = _find_alias_cols_xlsx(ws, header_row_no)
    if cols:
        return cols
    # Insert three empty alias columns right after the 玩家描述 column.
    main_col = _find_main_col_xlsx(ws, header_row_no) or 2
    ws.insert_cols(main_col + 1, amount=_ALIAS_SLOTS)
    for k in range(_ALIAS_SLOTS):
        ws.cell(row=header_row_no, column=main_col + 1 + k,
                value=f'别名{k + 1}')
    return [main_col + 1 + k for k in range(_ALIAS_SLOTS)]


def _write_xlsx_aliases(path: str, name: str, tokens: List[str]) -> List[str]:
    try:
        from openpyxl import load_workbook
    except Exception as e:  # noqa: BLE001
        raise RosterError('写入 xlsx 需要安装 openpyxl: ' + str(e))
    if not os.path.exists(path):
        raise RosterError('文件不存在: ' + path)
    try:
        wb = load_workbook(path)
    except Exception as e:  # noqa: BLE001
        raise RosterError(f'无法打开 {os.path.basename(path)}: {e}')
    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            raise RosterError('文件中没有工作表。')
        header_row_no = _ensure_header_xlsx(ws)
        alias_cols = _ensure_alias_cols_xlsx(ws, header_row_no)

        target = _normalize_name(name)
        found = None
        for r in range(header_row_no + 1, ws.max_row + 1):
            if _normalize_name(ws.cell(row=r, column=1).value) == target:
                found = r
                break
        if found is None:
            found = ws.max_row + 1
            ws.cell(row=found, column=1, value=name)

        cells = [str(ws.cell(row=found, column=c).value or '')
                 for c in alias_cols]
        for token in tokens:
            if not _alias_dup_ok(cells, token):
                continue
            for i in range(len(cells)):
                if not cells[i].strip():
                    cells[i] = token
                    break
        for k, col in enumerate(alias_cols):
            ws.cell(row=found, column=col, value=cells[k])
        wb.save(path)
        return cells
    except PermissionError:
        raise RosterError(f'无法写入 {os.path.basename(path)}（文件可能正被占用）。')
    except RosterError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RosterError(f'写入 {os.path.basename(path)} 失败: {e}')
    finally:
        wb.close()


def _write_csv_aliases(path: str, name: str, tokens: List[str]) -> List[str]:
    grid = _read_rows(path)
    if not grid:
        raise RosterError('CSV 文件为空。')
    header = grid[0] if _is_header_row(grid[0]) else None
    if header is None:
        width = len(grid[0])
        if width >= 2:
            labels = (['玩家名', _MAIN_HEADER_NAME] if width == 2
                      else ['玩家名'] + [f'描述{i}' for i in range(1, width)])
        else:
            labels = ['玩家名']
        grid.insert(0, labels)
        header = labels
    header_low = [str(h).strip().casefold() for h in header]
    alias_cols = [i for i in range(1, len(header_low))
                  if _ALIAS_HDR_RE.match(header_low[i])][:_ALIAS_SLOTS]

    if not alias_cols:
        # Insert three empty alias columns right after the 玩家描述 column.
        main_i = next((i for i, h in enumerate(header_low)
                       if h in _MAIN_HEADER_LOWER), 1)
        for r in range(len(grid)):
            grid[r] = grid[r][:main_i + 1] + [''] * _ALIAS_SLOTS + \
                grid[r][main_i + 1:]
        header_low = header_low[:main_i + 1] + \
            [f'别名{k + 1}' for k in range(_ALIAS_SLOTS)] + \
            header_low[main_i + 1:]
        alias_cols = [main_i + 1 + k for k in range(_ALIAS_SLOTS)]

    target = _normalize_name(name)
    found = None
    for r in range(1, len(grid)):
        if _normalize_name(grid[r][0]) == target:
            found = r
            break
    if found is None:
        grid.append([''] * len(header_low))
        found = len(grid) - 1
        grid[found][0] = name

    cells = []
    for i in alias_cols:
        row = grid[found]
        cells.append(row[i] if i < len(row) else '')
    for token in tokens:
        if not _alias_dup_ok(cells, token):
            continue
        for i in range(len(cells)):
            if not cells[i].strip():
                cells[i] = token
                break
    for k, i in enumerate(alias_cols):
        while len(grid[found]) <= i:
            grid[found].append('')
        grid[found][i] = cells[k]

    try:
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            for row in grid:
                writer.writerow([c if c is not None else '' for c in row])
    except OSError as e:
        raise RosterError(f'写入 {os.path.basename(path)} 失败: {e}')
    return cells


def _write_csv(path: str, name: str, note: str = '', ai: bool = False,
               main: Optional[str] = None) -> None:
    """CSV counterpart of the xlsx writer (re-encodes as UTF-8)."""
    grid = _read_rows(path)  # padded rows, may raise RosterError
    if not grid:
        raise RosterError('CSV 文件为空。')
    width = len(grid[0])
    header = grid[0] if _is_header_row(grid[0]) else None
    if header is None:
        if width >= 2:
            if width == 2:
                labels = ['玩家名', _MAIN_HEADER_NAME]
            else:
                labels = ['玩家名'] + [f'描述{i}' for i in range(1, width)]
        else:
            labels = ['玩家名']
        grid.insert(0, labels)
        header = labels
    # pad all rows to the header length
    hlen = len(header)
    for i, row in enumerate(grid):
        if len(row) < hlen:
            grid[i] = row + [''] * (hlen - len(row))
    header_low = [h.strip().casefold() for h in header]

    ai_base = None
    for i, h in enumerate(header_low):
        if _AI_HEADER_RE.match(h):
            ai_base = i
            break
    if ai is True and ai_base is None:
        ai_base = len(header)
        for k in range(_AI_COUNT):
            header.append(f'AI记录{k + 1}')
            header_low.append(f'ai记录{k + 1}')
            hlen += 1

    if main is not None:
        # Locate the authoritative 玩家描述 column by header text.
        main_i: Optional[int] = None
        for i, h in enumerate(header_low):
            if h in _MAIN_HEADER_LOWER:
                main_i = i
                break
        if main_i is None:
            # Insert an empty authoritative column right after the name.
            for r in range(len(grid)):
                grid[r] = grid[r][:1] + [''] + grid[r][1:]
            header.insert(1, _MAIN_HEADER_NAME)
            header_low.insert(1, _MAIN_HEADER_NAME)
            if ai_base is not None:
                ai_base += 1
            main_i = 1

    target = _normalize_name(name)
    found = None
    for r in range(1, len(grid)):
        if _normalize_name(grid[r][0]) == target:
            found = r
            break
    if found is None:
        row = [''] * max(len(grid[0]) if grid else 0,
                         (ai_base + _AI_COUNT if ai_base is not None else 2))
        row[0] = name
        grid.append(row)
        found = len(grid) - 1

    if main is not None and main_i is not None:
        row = grid[found]
        while len(row) <= main_i:
            row.append('')
        row[main_i] = main
    if ai and ai_base is not None:
        old = [grid[found][ai_base + k]
               for k in range(_AI_COUNT)
               if ai_base + k < len(grid[found])]
        while len(old) < _AI_COUNT:
            old.append('')
        new = ([note] + [o for o in old if o.strip()])[:_AI_COUNT]
        new += [''] * (_AI_COUNT - len(new))
        for k in range(_AI_COUNT):
            grid[found][ai_base + k] = new[k]

    try:
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            for row in grid:
                writer.writerow([c if c is not None else '' for c in row])
    except OSError as e:
        raise RosterError(f'写入 {os.path.basename(path)} 失败: {e}')


def make_template(path: str) -> None:
    """Export a starter xlsx with the full column model + one sample row.

    The template is meant to be saved and managed by the user (as their data
    table); AI-learned personality / alias / identity is written back into the
    imported table that already contains the player.
    """
    try:
        from openpyxl import Workbook
    except Exception as e:  # noqa: BLE001
        raise RosterError('导出模板需要安装 openpyxl: ' + str(e))
    wb = Workbook()
    ws = wb.active
    ws.title = '玩家资料'
    headers = (['玩家名', '玩家描述']
               + [f'别名{i}' for i in range(1, _ALIAS_SLOTS + 1)]
               + ['描述1', '描述2', '描述3']
               + [f'AI记录{i}' for i in range(1, _AI_COUNT + 1)])
    ws.append(headers)
    ws.append(['示例玩家', '（可选）该玩家的权威身份描述，可留空',
               '示例别名1', '示例别名2', '',
               '示例：该玩家是B站MC实况主。', '示例：该玩家擅长红石。', '示例：该玩家外号小明天。',
               '（AI记录，自动生成：该玩家性格开朗爱开玩笑。）', '', '', '', ''])
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = cell.font.copy(bold=True)
    wb.save(path)


class Roster:
    """In-memory store of player records merged from imported files.

    Each record: ``{'name', 'main', 'fixed', 'ai', 'source'}``. Later imports
    override earlier ones when the same (case-insensitive, whitespace-
    collapsed) name appears in several files.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.personality_enabled = True
        self.alias_enabled = True
        self.research_interval_ms = 180 * 1000
        self.research_min_messages = 6
        self._entries: List[Dict[str, Any]] = []
        self._index: Dict[str, Dict[str, Any]] = {}
        self._alias_index: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self._entries)

    def count_for(self, path: str) -> int:
        key = os.path.normcase(os.path.abspath(path))
        return sum(1 for e in self._entries if e.get('source') == key)

    def file_paths(self) -> List[str]:
        seen: List[str] = []
        for e in self._entries:
            src = e.get('source')
            if src and src not in seen:
                seen.append(src)
        return seen

    def entry_for_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Entry currently in use for a name (last loaded wins)."""
        return self.find(name)

    # ------------------------------------------------------------------
    # Load / remove
    # ------------------------------------------------------------------
    def add_file(self, path: str) -> int:
        """Parse and merge one file. Re-importing the same path replaces it."""
        path = os.path.abspath(path)
        records = parse_file(path)  # may raise RosterError
        self.remove_source(path)
        key = os.path.normcase(path)
        for rec in records:
            self._entries.append({
                'name': rec['name'],
                'main': rec.get('main', ''),
                'aliases': list(rec.get('aliases', [])),
                'fixed': list(rec.get('fixed', [])),
                'ai': list(rec.get('ai', [])),
                'source': key,
            })
        self._rebuild_index()
        return len(records)

    def remove_source(self, path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        self._entries = [e for e in self._entries if e.get('source') != key]
        self._rebuild_index()

    def clear(self) -> None:
        self._entries = []
        self._rebuild_index()

    def load_files(self, paths) -> List[str]:
        """Load a list of paths; returns human-readable errors for failures."""
        errors = []
        for p in paths or []:
            try:
                self.add_file(p)
            except RosterError as e:
                errors.append(str(e))
        return errors

    # ------------------------------------------------------------------
    # Learning & personality writes (serialised by a lock)
    # ------------------------------------------------------------------
    def learn_identity(self, source_path: str, name: str, text: str) -> None:
        """Persist ``text`` as the authoritative description of ``name``."""
        with _FILE_LOCK:
            path = os.path.abspath(source_path)
            write_main(path, name, text)  # raises RosterError on failure
            self._update_memory(path, name, main=text)

    def add_ai_record(self, source_path: str, name: str, note: str) -> None:
        """Roll ``note`` into the AI记录1..5 slots of ``name``."""
        note = str(note or '').strip()
        if not note:
            raise RosterError('AI 记录内容为空。')
        with _FILE_LOCK:
            path = os.path.abspath(source_path)
            write_ai_record(path, name, note)  # raises RosterError on failure
            self._update_memory(path, name, ai=note)

    def add_aliases(self, source_path: str, name: str, new_tokens) -> List[str]:
        """Fill new aliases into the first empty 别名 cell of ``name`` (manual
        cells are never overwritten). Returns the final alias cell list."""
        with _FILE_LOCK:
            path = os.path.abspath(source_path)
            cells = write_aliases(path, name, new_tokens)  # raises RosterError
            self._update_memory(path, name, aliases=list(cells))
            return list(cells)

    def _update_memory(self, path: str, name: str, main: Optional[str] = None,
                       ai: Optional[str] = None,
                       aliases: Optional[List[str]] = None) -> None:
        key = os.path.normcase(os.path.abspath(path))
        target = _normalize_name(name).casefold()
        matched = False
        for e in self._entries:
            if e.get('source') == key and \
                    _normalize_name(e['name']).casefold() == target:
                if main is not None:
                    e['main'] = main
                if ai is not None:
                    old = [x for x in (e.get('ai') or []) if str(x).strip()]
                    e['ai'] = ([ai] + old)[:_AI_COUNT]
                if aliases is not None:
                    e['aliases'] = list(aliases)
                matched = True
        if not matched:
            self._entries.append({'name': _normalize_name(name),
                                  'main': main if main is not None else '',
                                  'aliases': list(aliases or []),
                                  'fixed': [],
                                  'ai': [ai] if ai is not None else [],
                                  'source': key})
        self._rebuild_index()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def _rebuild_index(self) -> None:
        self._index = {}
        self._alias_index = {}
        for e in self._entries:
            key = _normalize_name(e['name']).casefold()
            if key:
                self._index[key] = e
            for cell in (e.get('aliases') or []):
                for alias in split_aliases(cell):
                    k = _normalize_name(alias).casefold()
                    if k:
                        self._alias_index.setdefault(k, e)

    def find(self, name: str) -> Optional[Dict[str, Any]]:
        return self._index.get(_normalize_name(name).casefold())

    def find_alias(self, alias: str) -> Optional[Dict[str, Any]]:
        """Return the entry whose alias matches ``alias`` exactly."""
        return self._alias_index.get(_normalize_name(alias).casefold())

    def match_alias_at_end(self, prefix: str) -> Optional[Dict[str, Any]]:
        """Return the entry whose alias forms the tail of ``prefix``."""
        prefix = _normalize_name(prefix).casefold()
        best = None
        best_len = -1
        for alias, entry in self._alias_index.items():
            if not alias or len(alias) <= best_len:
                continue
            try:
                pattern = re.compile(r'(?<![^\W_])' + re.escape(alias) + r'$')
            except re.error:
                continue
            if pattern.search(prefix):
                best, best_len = entry, len(alias)
        return best

    def match_at_end(self, prefix: str) -> Optional[Dict[str, Any]]:
        """Return the entry whose name forms the tail of ``prefix``.

        ``prefix`` is the part of a message that precedes ``是``. Names are
        tried longest-first and must start at a word boundary so a name like
        ``王`` doesn't match inside ``小明王``.
        """
        prefix = _normalize_name(prefix).casefold()
        ordered = sorted(self._entries,
                         key=lambda e: len(_normalize_name(e['name'])), reverse=True)
        for e in ordered:
            nm = _normalize_name(e['name']).casefold()
            if not nm:
                continue
            try:
                pattern = re.compile(r'(?<![^\W_])' + re.escape(nm) + r'$')
            except re.error:
                continue
            if pattern.search(prefix):
                return e
        return None
