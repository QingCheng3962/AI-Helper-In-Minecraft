"""Minecraft text budgets measured in Java UTF-16 code units."""


def mc_len(text) -> int:
    s = '' if text is None else str(text)
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in s)


def mc_clip(text, limit: int) -> str:
    """Keep whole characters within a UTF-16 budget, including astral emoji."""
    s = '' if text is None else str(text)
    out = []
    used = 0
    for ch in s:
        width = 2 if ord(ch) > 0xFFFF else 1
        if used + width > limit:
            break
        out.append(ch)
        used += width
    return ''.join(out)
