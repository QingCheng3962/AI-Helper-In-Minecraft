# -*- coding: utf-8 -*-
"""B 站「视频推荐」：抓一批视频给玩家。

五种来源（``kind``）：

- ``anime``（默认）—— 番剧**追番榜**（``pgc/web/rank/list?season_type=1``，更新多、适合追番）
- ``home``        —— B 站**首页推荐**（``x/web-interface/index/top/rcmd``，就是 App/网页首页那排）
- ``hot``         —— B 站**首页热门**（``x/web-interface/popular``）
- ``rank``        —— B 站**全站排行**（``x/web-interface/ranking/v2?rid=0``）
- ``fav``         —— **自己的收藏夹**（``x/v3/fav/resource/list``，随机翻页抽几条；需要登录 Cookie）

每次都会抓一个**大池子**（几十条），再从中随机挑几条，并能排除最近推过的（``exclude``）——
这样「冷却过后再点」就能**换一批新的**，不会每次都推同 5 条。

注意点：

- ``home`` 直接干调会被风控（HTTP 412 ``request was banned``），所以**先访问一次
  ``https://www.bilibili.com/`` 拿 ``buvid3`` cookie**，之后同一会话里连续调都正常（实测 3/3 成功）。
- ``fav`` 需要 ``SESSDATA``（登录后才有）：``cookie`` 可以是整条 Cookie 串，
  也可以只填 ``SESSDATA`` 的值（自动补前缀）。
- ⚠ B 站接口对 UA / Referer 敏感，必须带；返回 ``code != 0`` 视为失败。
"""
from __future__ import annotations

import math
import random
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
BASE = 'https://api.bilibili.com'

SOURCES = ('anime', 'home', 'hot', 'rank', 'fav')

_client_lock = threading.Lock()
_client_obj = None
_warmed = False


def _client():
    """共享的 httpx.Client（带 UA / Referer，保留 cookie）。"""
    global _client_obj
    with _client_lock:
        if _client_obj is None:
            import httpx
            _client_obj = httpx.Client(headers=_headers(), timeout=12.0,
                                       follow_redirects=True)
        return _client_obj


def _headers() -> Dict[str, str]:
    return {
        'User-Agent': UA,
        'Referer': 'https://www.bilibili.com/',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }


def cookie_header(cookie: str) -> str:
    """把「SESSDATA（随便粘贴一段 cookie）」统一成 Cookie 头。"""
    c = str(cookie or '').strip()
    if not c:
        return ''
    c = c.replace('\n', ';').replace('\r', ';')
    if '=' not in c:
        return 'SESSDATA=' + c.strip()
    return '; '.join(p.strip() for p in c.split(';') if p.strip())


def _warm() -> None:
    """访问一次首页拿 buvid3 等 cookie（首页推荐接口必须的「入场券」）。"""
    global _warmed
    if _warmed:
        return
    try:
        _client().get('https://www.bilibili.com/')
        _warmed = True
    except Exception:  # noqa: BLE001
        pass


def _get_json(url: str, cookie: str = '', warm: bool = False,
              timeout: float = 12.0) -> Optional[Dict[str, Any]]:
    if warm:
        _warm()
    headers = {}
    ck = cookie_header(cookie)
    if ck:
        headers['Cookie'] = ck
    try:
        r = _client().get(url, headers=headers or None, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict) or data.get('code') != 0:
            return None
        return data
    except Exception:  # noqa: BLE001
        return None


def _last_error(url: str, cookie: str = '', warm: bool = False) -> str:
    """失败时给个能看懂的原因（GUI 日志用）。"""
    if warm:
        _warm()
    headers = {}
    ck = cookie_header(cookie)
    if ck:
        headers['Cookie'] = ck
    try:
        r = _client().get(url, headers=headers or None, timeout=12.0)
        try:
            d = r.json()
        except Exception:  # noqa: BLE001
            return 'HTTP %s' % r.status_code
        return 'code=%s %s' % (d.get('code'), d.get('message'))
    except Exception as e:  # noqa: BLE001
        return '%s: %s' % (type(e).__name__, e)


# ----------------------------------------------------------------------
# 各来源：把接口返回整理成统一的 {id, title, owner, link}
# ----------------------------------------------------------------------
def _from_video(raw: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """普通投稿（首页推荐 / 热门 / 排行）→ 统一格式。"""
    if not isinstance(raw, dict):
        return None
    goto = str(raw.get('goto') or '').strip()
    if goto and goto not in ('av', 'video'):
        return None                      # 直播 / 专栏 / 番剧|番剧走 _from_bangumi
    bvid = str(raw.get('bvid') or '').strip()
    title = str(raw.get('title') or '').strip()
    if not title:
        return None
    owner = ''
    o = raw.get('owner')
    if isinstance(o, dict):
        owner = str(o.get('name') or '').strip()
    if not owner:                        # 首页推荐的 up 信息有时在 upper 里
        u = raw.get('upper')
        if isinstance(u, dict):
            owner = str(u.get('name') or '').strip()
    short = str(raw.get('short_link_v2') or '').strip()
    if not short.startswith('http'):
        short = ('https://www.bilibili.com/video/' + bvid) if bvid else ''
    if not short:
        return None
    return {'id': bvid or short, 'title': title,
            'owner': f'（{owner}）' if owner else '', 'link': short}


def _from_bangumi(raw: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """番剧（追番榜）→ 统一格式；备注里带上「更新至…」或评分。"""
    if not isinstance(raw, dict):
        return None
    title = str(raw.get('title') or '').strip()
    if not title:
        return None
    season_id = raw.get('season_id') or 0
    url = str(raw.get('url') or '').strip()
    if not url and season_id:
        url = 'https://www.bilibili.com/bangumi/play/ss%d' % int(season_id)
    if not url:
        return None
    note = ''
    ne = raw.get('new_ep')
    if isinstance(ne, dict):
        note = str(ne.get('index_show') or '').strip()
    if not note:
        note = str(raw.get('rating') or '').strip()
    return {'id': 'ss%d' % int(season_id) if season_id else url,
            'title': title, 'owner': f'（{note}）' if note else '', 'link': url}


def _from_fav(raw: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """收藏夹条目 → 统一格式（跳过失效 / 非视频）。"""
    if not isinstance(raw, dict):
        return None
    title = str(raw.get('title') or '').strip()
    if not title or '已失效' in title or '已删除' in title:
        return None
    attr = raw.get('attr') or 0
    try:
        if int(attr) & 3:                # 失效/删除位
            return None
    except (TypeError, ValueError):
        pass
    try:
        if int(raw.get('type') or 2) not in (2, 21):
            return None                  # 2=视频、21=合集；其它（音频/专栏…）跳过
    except (TypeError, ValueError):
        pass
    bvid = str(raw.get('bvid') or '').strip()
    url = str(raw.get('link') or '').strip()
    if not url.startswith('http'):
        url = ('https://www.bilibili.com/video/' + bvid) if bvid else ''
    if not url:
        return None
    owner = ''
    up = raw.get('upper')
    if isinstance(up, dict):
        owner = str(up.get('name') or '').strip()
    return {'id': bvid or url, 'title': title,
            'owner': f'（{owner}）' if owner else '', 'link': url}


# ----------------------------------------------------------------------
# 池子
# ----------------------------------------------------------------------
def _pool(kind: str, size: int, cookie: str = '',
          media_id: int = 0) -> Tuple[List[Dict[str, str]], str]:
    """抓一个大池子（尽量多）。返回 ``(列表, 失败原因)``。"""
    out: List[Dict[str, str]] = []
    err = ''
    if kind == 'anime':
        # 追番榜：一天 99 条，先三日榜再日榜
        for day in (3, 1):
            url = BASE + '/pgc/web/rank/list?season_type=1&day=%d' % day
            data = _get_json(url)
            lst = ((data or {}).get('result') or {}).get('list') or []
            out = [x for x in (_from_bangumi(i) for i in lst[:size]) if x]
            if out:
                break
            err = err or _last_error(url)
    elif kind == 'home':
        # 首页推荐：一次 30 条，翻几页凑池子（必须先 _warm 拿 buvid3，否则 412；ps 只能 12）
        for idx in (1, 2, 3):
            url = (BASE + '/x/web-interface/index/top/rcmd'
                   '?fresh_type=3&ps=12&fresh_idx=%d&fresh_idx_1h=%d'
                   '&feed_version=V8&homepage_ver=1' % (idx, idx))
            data = _get_json(url, warm=True)
            lst = ((data or {}).get('data') or {}).get('item') or []
            out.extend(x for x in (_from_video(i) for i in lst) if x)
            if not lst:
                err = err or _last_error(url, warm=True)
            if len(out) >= size:
                break
    elif kind == 'hot':
        # 首页热门：ps 最大 20，翻几页凑池子
        for pn in (1, 2, 3):
            url = BASE + '/x/web-interface/popular?ps=20&pn=%d' % pn
            data = _get_json(url)
            lst = ((data or {}).get('data') or {}).get('list') or []
            out.extend(x for x in (_from_video(i) for i in lst) if x)
            if not lst:
                err = err or _last_error(url)
            if len(out) >= size:
                break
    elif kind == 'rank':
        url = BASE + '/x/web-interface/ranking/v2?rid=0&type=all'
        data = _get_json(url)
        lst = ((data or {}).get('data') or {}).get('list') or []
        out = [x for x in (_from_video(i) for i in lst[:size]) if x]
        if not out:
            err = _last_error(url)
    else:  # fav
        out, err = _fav_pool(cookie, int(media_id or 0), size)
    return (out[:size] if size else out), err


def _fav_pool(cookie: str, media_id: int, size: int) -> Tuple[List[Dict[str, str]], str]:
    """收藏夹里**随机翻页**抽一批（够随机、又不用把整个夹子拉下来）。"""
    if not cookie_header(cookie):
        return [], '没填 B 站 Cookie（SESSDATA）'
    if not media_id:
        return [], '没选收藏夹'
    first = BASE + ('/x/v3/fav/resource/list?media_id=%d&ps=20&pn=1'
                    '&order=mtime&platform=web' % media_id)
    data = _get_json(first, cookie=cookie)
    if not data:
        return [], _last_error(first, cookie=cookie)
    d = data.get('data') or {}
    info = d.get('info') or {}
    total = int(info.get('media_count') or info.get('count') or 0)
    pages = max(1, min(50, int(math.ceil(total / 20.0)))) if total else 1
    out = [x for x in (_from_fav(i) for i in (d.get('medias') or [])) if x]

    # 再随机翻 2 页（同一页只拉一次）
    picks: List[int] = []
    while len(picks) < 2 and pages > 1:
        pn = random.randint(1, pages)
        if pn not in picks:
            picks.append(pn)
    for pn in picks:
        if len(out) >= size:
            break
        url = BASE + ('/x/v3/fav/resource/list?media_id=%d&ps=20&pn=%d'
                      '&order=mtime&platform=web' % (media_id, pn))
        more = _get_json(url, cookie=cookie)
        ml = ((more or {}).get('data') or {}).get('medias') or []
        out.extend(x for x in (_from_fav(i) for i in ml) if x)
    return out, ('' if out else '收藏夹里没抽到可用视频')


# ----------------------------------------------------------------------
# 账号相关（GUI 用）
# ----------------------------------------------------------------------
def login_info(cookie: str) -> Dict[str, Any]:
    """用 Cookie 查账号：``{ok, message, name, mid, vip}``。"""
    data = _get_json(BASE + '/x/web-interface/nav', cookie=cookie)
    if not data:
        return {'ok': False, 'message': _last_error(BASE + '/x/web-interface/nav',
                                                    cookie=cookie),
                'name': '', 'mid': 0, 'vip': False}
    d = data.get('data') or {}
    if not d.get('isLogin'):
        return {'ok': False, 'message': 'Cookie 无效或已过期（未登录）',
                'name': '', 'mid': 0, 'vip': False}
    vip = bool((d.get('vipStatus') or 0)) or bool((d.get('vip')) or {}.get('status')
                                                  if isinstance(d.get('vip'), dict) else False)
    return {'ok': True, 'message': '已登录', 'name': str(d.get('uname') or ''),
            'mid': int(d.get('mid') or 0), 'vip': bool(vip)}


def fav_folders(cookie: str) -> Dict[str, Any]:
    """列出收藏夹（自建 + 收藏的）→ ``{ok, message, folders:[{media_id,title,count}]}``。"""
    info = login_info(cookie)
    if not info.get('ok'):
        return {'ok': False, 'message': info.get('message') or 'Cookie 无效', 'folders': []}
    mid = int(info.get('mid') or 0)
    out: List[Dict[str, Any]] = []
    for url in (BASE + '/x/v3/fav/folder/created/list-all?up_mid=%d&web_location=333.1387' % mid,
                BASE + '/x/v3/fav/folder/collected/list?up_mid=%d&pn=1&ps=50'
                       '&platform=web' % mid):
        data = _get_json(url, cookie=cookie)
        lst = ((data or {}).get('data') or {}).get('list') or []
        for f in lst:
            if not isinstance(f, dict):
                continue
            mid_id = f.get('id') or f.get('media_id') or 0
            try:
                mid_id = int(mid_id)
            except (TypeError, ValueError):
                continue
            if not mid_id:
                continue
            out.append({'media_id': mid_id,
                        'title': str(f.get('title') or '未命名收藏夹'),
                        'count': int(f.get('media_count') or 0),
                        'collected': url.endswith('platform=web') and 'collected' in url})
    if not out:
        return {'ok': False, 'message': '没读到收藏夹（Cookie 可能过期，或账号没有收藏夹）',
                'folders': []}
    return {'ok': True, 'message': f"{info.get('name') or ''}：{len(out)} 个收藏夹",
            'folders': out, 'name': info.get('name') or ''}


# ----------------------------------------------------------------------
# 对外的推荐入口
# ----------------------------------------------------------------------
def recommend(kind: str = 'anime', limit: int = 5,
              exclude: Optional[Iterable[str]] = None,
              size: int = 30, cookie: str = '',
              media_id: int = 0) -> List[Dict[str, str]]:
    """挑 ``limit`` 条推荐。

    ``exclude`` 里是最近推过的 id —— 会尽量避开（都推过一遍就允许重复）。
    """
    items, _err = recommend_ex(kind, limit, exclude, size, cookie, media_id)
    return items


def recommend_ex(kind: str = 'anime', limit: int = 5,
                 exclude: Optional[Iterable[str]] = None,
                 size: int = 30, cookie: str = '',
                 media_id: int = 0) -> Tuple[List[Dict[str, str]], str]:
    """同 ``recommend``，但把失败原因一起返回（``(列表, 原因)``）。"""
    kind = str(kind or 'anime').strip().lower()
    if kind not in SOURCES:
        kind = 'anime'
    limit = max(1, min(10, int(limit or 5)))
    size = max(limit, min(100, int(size or 30)))
    pool, err = _pool(kind, size, cookie, media_id)
    if not pool:
        return [], err or '没抓到数据'
    used = {str(x) for x in (exclude or ())}
    # 按 id 去重（翻页可能有重复条目）
    seen_ids: set = set()
    deduped_pool: List[Dict[str, str]] = []
    for it in pool:
        key = str(it.get('id'))
        if key in seen_ids:
            continue
        seen_ids.add(key)
        deduped_pool.append(it)
    # 新候选选优先先取没推过的，不够再从推过的补
    fresh = [x for x in deduped_pool if str(x.get('id')) not in used]
    stale = [x for x in deduped_pool if str(x.get('id')) in used]
    random.shuffle(fresh)
    random.shuffle(stale)
    # 新候选不够时用旧的补（而不是整批退回旧池子）
    combined = fresh + stale
    picked: List[Dict[str, str]] = combined[:limit]
    return picked, ''


def popular(limit: int = 5) -> List[Dict[str, str]]:
    """兼容旧调用：首页热门前 ``limit`` 条。"""
    return recommend('hot', limit)
