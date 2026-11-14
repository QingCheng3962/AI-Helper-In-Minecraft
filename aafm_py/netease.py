"""网易云（给机器人用）：搜索 / 取播放直链 / 扫码登录。

机器人持有会员 Cookie 后，可以替全服玩家解析 VIP 歌曲的可播放直链；
客户端 Mod 只负责播放（服务端零改动）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

BASE = 'https://music.163.com'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
# 必须带的基础 Cookie（否则 fee=1 的歌连试听都不给）
BASE_COOKIE = 'os=pc; appver=2.9.7; channel=netease;'

_ID_IN_URL = re.compile(r'[?&]id=(\d{4,})')
_PURE_ID = re.compile(r'^\d{6,}$')

# 搜索缓存 + 请求节流（被网易云限流的概率因此小很多）
_SEARCH_CACHE: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_SEARCH_TTL = 300.0          # 同一关键词 5 分钟内复用结果
_SEARCH_MIN_GAP = 0.8        # 两次搜索之间至少隔 0.8 秒
_LAST_SEARCH_AT = 0.0

# 最近一次接口请求失败的原因（给 UI 提示用）：'' / HTTP 403 / 搜索无结果 / 异常…
LAST_ERROR = ''


class SearchUnavailable(RuntimeError):
    """网易云接口请求失败（网络挂了 / 被限流）—— 跟「真的没这首歌」区分开。

    以前两者都返回 None，UI 一律显示「没找到「歌名」」，很容易误判成歌名问题。
    """


def _headers(cookie: str = '') -> Dict[str, str]:
    cookie = (cookie or '').strip()
    return {
        'User-Agent': UA,
        'Referer': BASE + '/',
        'Accept': 'application/json, text/plain, */*',
        'Cookie': BASE_COOKIE + ((' ' + cookie) if cookie else ''),
    }


def _get_json(url: str, cookie: str = '', timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    global LAST_ERROR
    try:
        import httpx
        r = httpx.get(url, headers=_headers(cookie), timeout=timeout, follow_redirects=True)
        if r.status_code != 200:
            LAST_ERROR = 'HTTP %s' % r.status_code
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001
        LAST_ERROR = repr(e)
        return None


def extract_id(text: str) -> Optional[str]:
    """从文本里抽出网易云歌曲 id（链接里的 id= / 纯数字 / 短链跟跳）。"""
    if not text:
        return None
    t = text.strip()
    m = _ID_IN_URL.search(t)
    if m:
        return m.group(1)
    if _PURE_ID.match(t):
        return t
    if t.startswith('http') and ('163.cn' in t or 'music.163.com' in t) and 'id=' not in t:
        try:
            import httpx
            r = httpx.get(t, headers=_headers(''), timeout=8.0, follow_redirects=True)
            m2 = _ID_IN_URL.search(str(r.url))
            if m2:
                return m2.group(1)
        except Exception:  # noqa: BLE001
            pass
    return None


def _song_from_obj(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = obj.get('id')
    if sid is None:
        return None
    artists: List[str] = []
    ar = obj.get('artists') or obj.get('ar') or []
    for a in ar:
        if isinstance(a, dict) and a.get('name'):
            artists.append(str(a['name']))
    album = None
    al = obj.get('album') or obj.get('al')
    if isinstance(al, dict) and al.get('name'):
        album = str(al['name'])
    return {
        'id': str(sid),
        'title': str(obj.get('name') or ''),
        'artists': artists,
        'album': album,
    }


def search(keyword: str, cookie: str = '') -> Optional[Dict[str, Any]]:
    """搜索第一条命中（返回 id/title/artists/album）。

    ① 5 分钟缓存（同一首歌短时间内反复点不重复打接口，减少被限流的概率）；
    ② 每次请求之间至少间隔 0.8 秒；
    ③ 请求失败**自动重试**（换一个搜索端点 + 退避），原因写进 ``LAST_ERROR`` ——
       「请求失败（限流/网络）」和「真没这首歌」要分得清，不然一律显示成「没找到」很难排查。
    """
    global LAST_ERROR, _LAST_SEARCH_AT
    kw = (keyword or '').strip()
    if not kw:
        return None
    import time as _t
    import urllib.parse
    cache_key = kw.lower() + '|' + str(abs(hash(cookie)))
    hit = _SEARCH_CACHE.get(cache_key)
    if hit and _t.time() - hit[0] < _SEARCH_TTL:
        return hit[1]
    wait = _SEARCH_MIN_GAP - (_t.time() - _LAST_SEARCH_AT)
    if wait > 0:
        _t.sleep(wait)
    _LAST_SEARCH_AT = _t.time()
    q = urllib.parse.quote(kw)
    urls = [
        BASE + '/api/search/get/web?s=' + q + '&type=1&offset=0&total=true&limit=1',
        BASE + '/api/search/get/?s=' + q + '&type=1&offset=0&total=true&limit=1',
    ]
    LAST_ERROR = ''
    try:
        for attempt in range(3):
            # ⚠ 搜索**故意不带会员 Cookie**：实测带上 MUSIC_U 时网易云会返回**空结果**
            #   （会话被风控/限流），而匿名搜索稳定正常 —— 搜索本来就是公开接口，
            #   Cookie 只在「取播放直链 / 校验登录」时才需要。最后一次重试再带上 Cookie 兜底。
            use_cookie = cookie if attempt == 2 else ''
            data = _get_json(urls[attempt % len(urls)], use_cookie)
            if data:
                songs = (data.get('result') or {}).get('songs') or []
                song = _song_from_obj(songs[0]) if songs else None
                if song is None and attempt == 0 and cookie:
                    continue        # 匿名没结果？再试一次（可能是瞬时抽风）
                _SEARCH_CACHE[cache_key] = (_t.time(), song)
                if song is None:
                    LAST_ERROR = '搜索无结果'
                return song
            _t.sleep(0.5 * (attempt + 1))
    except Exception as e:  # noqa: BLE001
        LAST_ERROR = repr(e)
        return None
    return None


def by_id(song_id: str, cookie: str = '') -> Optional[Dict[str, Any]]:
    data = _get_json(BASE + '/api/song/detail/?ids=%5B' + str(song_id) + '%5D', cookie)
    if not data:
        return None
    songs = data.get('songs') or []
    if not songs:
        return None
    return _song_from_obj(songs[0])


def _eapi_encrypt(path: str, data: str) -> str:
    """网易云 eapi 加密：AES-128-ECB + md5 摘要，返回大写十六进制。"""
    import hashlib
    import binascii
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    digest = hashlib.md5(('nobody' + path + 'use' + data + 'md5forencrypt').encode('utf-8')).hexdigest()
    payload = (path + '-36cd479b6b5-' + data + '-36cd479b6b5-' + digest).encode('utf-8')
    pad = 16 - (len(payload) % 16)
    payload = payload + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(b'e82ckenh8dichen8'), modes.ECB()).encryptor()
    out = encryptor.update(payload) + encryptor.finalize()
    return binascii.hexlify(out).decode('ascii').upper()


def eapi_post(path: str, payload: Dict[str, Any], cookie: str = '') -> Optional[Dict[str, Any]]:
    """eapi POST（AES-128-ECB 加密，认登录态），返回 JSON。"""
    import json as _json
    try:
        data = _json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        params = _eapi_encrypt(path, data)
    except Exception:  # noqa: BLE001
        return None
    try:
        import httpx
        headers = {
            'User-Agent': ('NeteaseMusic/9.1.65.240927161425(9001065);'
                           'Dalvik/2.1.0 (Linux; U; Android 14)'),
            'Referer': 'https://music.163.com/',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': 'os=android; appver=9.1.65;' + ((' ' + cookie.strip()) if cookie and cookie.strip() else ''),
        }
        r = httpx.post('https://interface3.music.163.com' + path, data={'params': params},
                       headers=headers, timeout=12.0)
        if r.status_code != 200:
            return None
        j = r.json()
        return j if isinstance(j, dict) else None
    except Exception:  # noqa: BLE001
        return None


def eapi_player_url(song_id: str, cookie: str = '') -> Optional[Tuple[str, int, int, int]]:
    """走 eapi 取播放直链（**认会员**：带 MUSIC_U 才能拿 VIP 完整版）。"""
    path = '/api/song/enhance/player/url/v1'
    try:
        j = eapi_post(path, {'ids': '[%s]' % song_id, 'level': 'exhigh', 'encodeType': 'mp3'}, cookie)
        if not j:
            return None
        arr = j.get('data') or []
        if not arr:
            return None
        d0 = arr[0] or {}
        url = d0.get('url') or None
        if not url:
            return None
        fee = int(d0.get('fee') or 0)
        duration = int(d0.get('time') or 0)
        trial = 0
        ft = d0.get('freeTrialInfo')
        if isinstance(ft, dict):
            try:
                trial = int(ft.get('end') or -1)
            except (TypeError, ValueError):
                trial = -1
            if trial > 0:
                duration = trial * 1000
        return url, trial, fee, duration
    except Exception:  # noqa: BLE001
        return None


def _old_player_url(song_id: str, cookie: str = '') -> Tuple[Optional[str], int, int, int]:
    """旧接口取直链。实测：带 Cookie 时**认会员**（能拿 320k 完整直链）。"""
    data = _get_json(BASE + '/api/song/enhance/player/url?ids=%5B' + str(song_id) + '%5D&br=320000', cookie)
    if not data:
        return None, 0, 0, 0
    arr = data.get('data') or []
    if not arr:
        return None, 0, 0, 0
    d0 = arr[0] or {}
    fee = int(d0.get('fee') or 0)
    url = d0.get('url') or None
    duration = 0
    try:
        duration = int(d0.get('time') or 0)
    except (TypeError, ValueError):
        duration = 0
    trial = 0
    ft = d0.get('freeTrialInfo')
    if isinstance(ft, dict):
        try:
            trial = int(ft.get('end') or -1)
        except (TypeError, ValueError):
            trial = -1
        if trial > 0:
            duration = trial * 1000   # 试听就按试听长度算
    return url, trial, fee, duration


def player_url(song_id: str, cookie: str = '') -> Tuple[Optional[str], int, int, int]:
    """取播放直链。返回 (url, 试听秒数(0=完整), fee, 时长毫秒)。

    优先用**旧接口**（实测带 Cookie 时认会员、能拿 320k 完整版），
    失败再试 eapi（认登录态；个别网络下会被挡，尽力而为）。
    """
    url, trial, fee, duration = _old_player_url(song_id, cookie)
    if url:
        return url, trial, fee, duration
    if cookie and cookie.strip():
        eapi = eapi_player_url(song_id, cookie)
        if eapi:
            return eapi
    return url, trial, fee, duration


# 验证 Cookie 用的一首「会员专属」歌：孤勇者（fee=1）。能拿到完整直链说明会员有效。
VIP_PROBE_SONG = '1901371647'


def check_login(cookie: str = '') -> Dict[str, Any]:
    """验证网易云 Cookie 是否可用（并实测能否播放会员歌）。

    返回 ``{'ok', 'nickname', 'vip_type', 'can_play_vip', 'message'}``。
    """
    ck = (cookie or '').strip()
    res: Dict[str, Any] = {'ok': False, 'nickname': '', 'vip_type': 0,
                           'can_play_vip': False, 'message': ''}
    if not ck:
        res['message'] = '没有填 Cookie。'
        return res
    if 'MUSIC_U' not in ck:
        res['message'] = 'Cookie 里没有 MUSIC_U —— 请把浏览器里 music.163.com 的完整 Cookie 粘过来。'
        return res

    # ① 账号信息（eapi 认登录态）
    profile: Dict[str, Any] = {}
    for path in ('/api/nuser/account/get', '/api/v1/user/info'):
        j = eapi_post(path, {}, ck)
        if not j:
            continue
        p = j.get('profile')
        if isinstance(p, dict) and p:
            profile = p
            break
        if j.get('nickname'):
            profile = j
            break
    res['nickname'] = str(profile.get('nickname') or '')
    try:
        res['vip_type'] = int(profile.get('vipType') or 0)
    except (TypeError, ValueError):
        res['vip_type'] = 0
    if res['nickname']:
        res['ok'] = True

    # ② 实测一首会员歌
    probe = player_url(VIP_PROBE_SONG, ck)
    if probe and probe[0] and probe[1] == 0:
        res['can_play_vip'] = True
        res['ok'] = True

    name = res['nickname']
    who = ('账号：' + name) if name else '账号信息没读到'
    if res['ok'] and res['can_play_vip']:
        res['message'] = '✅ Cookie 可用，会员歌能拿到完整直链（' + who + '）。'
    elif res['ok']:
        res['message'] = ('⚠️ Cookie 有效（' + who + '），但会员歌拿不到完整版：'
                          '这个账号可能不是网易云会员（或这首歌要另购数字专辑）。')
    elif res['can_play_vip']:
        res['message'] = '✅ Cookie 可用：会员歌能拿到完整直链（账号昵称接口没返回，不影响播放）。'
    else:
        res['message'] = ('❌ Cookie 无效或已过期（账号信息和会员歌都拿不到）。'
                          '请重新登录网易云音乐后重新复制 Cookie。')
    return res


def lyric(song_id: str, cookie: str = '') -> Optional[Dict[str, Any]]:
    """取歌词（LRC 原文），返回 {'lrc','translated'}；取不到返回 None。"""
    sid = str(song_id or '').strip()
    if not sid:
        return None
    data = _get_json(BASE + '/api/song/lyric?os=pc&id=' + sid + '&lv=-1&kv=-1&tv=-1', cookie)
    if not data:
        return None
    lrc = ((data.get('lrc') or {}).get('lyric') or '')
    translated = ((data.get('tlyric') or {}).get('lyric') or '')
    if not lrc.strip():
        return None
    return {'lrc': lrc, 'translated': translated}


def resolve(text: str, cookie: str = '') -> Optional[Dict[str, Any]]:
    """把「关键词 / 链接 / 歌曲id」解析成可播放信息。

    返回 {'id','title','artists','url','trial','fee','note'}，失败时 url 为 None 并有 note。
    """
    raw = (text or '').strip()
    if not raw:
        return None
    song = None
    sid = extract_id(raw)
    if sid:
        song = by_id(sid, cookie)
        if song is None:
            song = {'id': sid, 'title': '歌曲 ' + sid, 'artists': [], 'album': None}
    else:
        song = search(raw, cookie)
    if song is None:
        if LAST_ERROR and LAST_ERROR != '搜索无结果':
            raise SearchUnavailable(LAST_ERROR)
        return None
    url, trial, fee, duration = player_url(song['id'], cookie)
    note = None
    if not url:
        if LAST_ERROR and LAST_ERROR != '搜索无结果':
            note = '获取播放地址失败（可能被限流，稍等一下再试）：' + LAST_ERROR
        else:
            note = ('该歌曲需要网易云会员（请在机器人的「点歌」页登录）' if fee > 0
                    else '该歌曲暂时没有可播放的地址')
    return {
        'id': song['id'],
        'title': song.get('title') or raw,
        'artists': song.get('artists') or [],
        'album': song.get('album'),
        'url': url,
        'trial': trial,
        'fee': fee,
        'duration': duration,
        'note': note,
    }


# ----------------------------------------------------------------------
# 扫码登录
# ----------------------------------------------------------------------
class QrLogin:
    """一次扫码登录会话。

    用**同一个 httpx.Client** 发请求，这样服务端在 803 时下发的 `MUSIC_U`
    会进入客户端的 Cookie jar，稳定拿到（比只解析响应头可靠）。
    """

    def __init__(self, timeout: float = 10.0):
        import httpx
        self._cli = httpx.Client(headers=_headers(''), timeout=timeout, follow_redirects=True)
        self.unikey = None

    def start(self) -> Optional[str]:
        try:
            r = self._cli.get(BASE + '/api/login/qrcode/unikey?type=1')
            self.unikey = (r.json() or {}).get('unikey')
        except Exception:  # noqa: BLE001
            self.unikey = None
        return self.unikey

    def login_url(self) -> str:
        return 'https://music.163.com/login?codekey=' + str(self.unikey or '')

    def poll(self) -> Tuple[int, Optional[str]]:
        """返回 (code, cookie)。800 过期 / 801 等待 / 802 已扫 / 803 成功。"""
        try:
            r = self._cli.get(BASE + '/api/login/qrcode/client/login?type=1&key=' + str(self.unikey))
        except Exception:  # noqa: BLE001
            return 0, None
        code = 0
        try:
            code = int((r.json() or {}).get('code') or 0)
        except Exception:  # noqa: BLE001
            code = 0
        music_u = self._cli.cookies.get('MUSIC_U')
        if not music_u:
            for header in r.headers.get_list('set-cookie'):
                idx = header.find('MUSIC_U=')
                if idx >= 0:
                    rest = header[idx + len('MUSIC_U='):]
                    semi = rest.find(';')
                    music_u = rest[:semi] if semi >= 0 else rest
                    break
        return code, ('MUSIC_U=' + music_u if music_u else None)

    def close(self) -> None:
        try:
            self._cli.close()
        except Exception:  # noqa: BLE001
            pass


def qr_unikey() -> Optional[str]:
    data = _get_json(BASE + '/api/login/qrcode/unikey?type=1')
    if data and data.get('unikey'):
        return str(data['unikey'])
    return None


def qr_poll(unikey: str) -> Tuple[int, Optional[str]]:
    """轮询扫码状态（无状态版）。返回 (code, cookie)。"""
    try:
        import httpx
        url = BASE + '/api/login/qrcode/client/login?type=1&key=' + str(unikey)
        r = httpx.get(url, headers=_headers(''), timeout=10.0, follow_redirects=True)
        code = 0
        try:
            code = int(r.json().get('code') or 0)
        except Exception:  # noqa: BLE001
            code = 0
        cookie = None
        for header in r.headers.get_list('set-cookie'):
            idx = header.find('MUSIC_U=')
            if idx >= 0:
                rest = header[idx + len('MUSIC_U='):]
                semi = rest.find(';')
                cookie = 'MUSIC_U=' + (rest[:semi] if semi >= 0 else rest)
        return code, cookie
    except Exception:  # noqa: BLE001
        return 0, None
