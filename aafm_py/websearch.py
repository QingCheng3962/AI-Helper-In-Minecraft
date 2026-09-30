"""Web search for the AI player.

Two backends:
  - ``duckduckgo`` : free HTML scraping, no API key.
  - ``custom``     : a configurable JSON search API (Tavily / Serper / Bing style).
"""
from __future__ import annotations

import html
import re
import urllib.parse
from typing import Dict, List

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36')

_TAG_RE = re.compile(r'<[^>]+>')


def _clean(s: str) -> str:
    return html.unescape(_TAG_RE.sub('', str(s or ''))).strip()


def _ddg_url(href: str) -> str:
    href = html.unescape(str(href or ''))
    if href.startswith('//'):
        href = 'https:' + href
    # DuckDuckGo wraps links as /l/?uddg=<encoded>
    try:
        q = urllib.parse.urlparse(href)
        params = urllib.parse.parse_qs(q.query)
        if 'uddg' in params and params['uddg']:
            return params['uddg'][0]
    except Exception:  # noqa: BLE001
        pass
    return href


def search_duckduckgo(query: str, max_results: int = 5, timeout: float = 12.0) -> List[Dict[str, str]]:
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return []
    try:
        r = httpx.get('https://html.duckduckgo.com/html/',
                      params={'q': query}, headers={'User-Agent': _UA},
                      timeout=timeout, follow_redirects=True)
    except Exception:  # noqa: BLE001
        return []
    if r.status_code != 200:
        return []
    text = r.text
    titles = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', text, re.S)
    out: List[Dict[str, str]] = []
    for i, (href, title) in enumerate(titles[:max_results]):
        out.append({
            'title': _clean(title),
            'url': _ddg_url(href),
            'snippet': _clean(snippets[i]) if i < len(snippets) else '',
        })
    return out


def search_bing(query: str, max_results: int = 5, timeout: float = 12.0) -> List[Dict[str, str]]:
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return []
    try:
        r = httpx.get('https://www.bing.com/search',
                      params={'q': query, 'setlang': 'zh-CN'},
                      headers={'User-Agent': _UA, 'Accept-Language': 'zh-CN,zh;q=0.9'},
                      timeout=timeout, follow_redirects=True)
        text = r.text
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, str]] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', text, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        p = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        out.append({
            'title': _clean(m.group(2)),
            'url': html.unescape(m.group(1)),
            'snippet': _clean(p.group(1)) if p else '',
        })
        if len(out) >= max_results:
            break
    return out


def search_custom(query: str, api_url: str, api_key: str,
                  max_results: int = 5, timeout: float = 12.0) -> List[Dict[str, str]]:
    if not api_url:
        return []
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return []
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    body = {'query': query, 'q': query, 'max_results': max_results}
    try:
        r = httpx.post(api_url, json=body, headers=headers,
                       timeout=timeout, follow_redirects=True)
        data = r.json()
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    items = (data.get('results') or data.get('organic')
             or data.get('data') or data.get('items') or [])
    if not isinstance(items, list):
        return []
    out: List[Dict[str, str]] = []
    for it in items[:max_results]:
        if not isinstance(it, dict):
            continue
        out.append({
            'title': str(it.get('title') or it.get('name') or ''),
            'url': str(it.get('url') or it.get('link') or ''),
            'snippet': str(it.get('content') or it.get('snippet') or it.get('description') or ''),
        })
    return out


def search(query: str, cfg) -> List[Dict[str, str]]:
    """Run a search using the AI player's search config ``cfg`` (AiPlayerConfig)."""
    query = (query or '').strip()
    if not query:
        return []
    provider = (getattr(cfg, 'searchProvider', 'duckduckgo') or 'duckduckgo').lower()
    n = max(1, int(getattr(cfg, 'searchMaxResults', 5) or 5))
    if provider == 'custom':
        return search_custom(query, getattr(cfg, 'searchApiUrl', ''),
                             getattr(cfg, 'searchApiKey', ''), n)
    if provider == 'bing':
        return search_bing(query, n)
    return search_duckduckgo(query, n)


def format_results(results: List[Dict[str, str]]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        t = r.get('title') or ''
        s = r.get('snippet') or ''
        u = r.get('url') or ''
        lines.append(f'{i}. {t} — {s} ({u})')
    return '\n'.join(lines)
