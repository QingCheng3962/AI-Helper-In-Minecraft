"""Mini Mod 纯逻辑回归：javac + java 跑 songs 的共享核心类（不依赖 Minecraft / 网络）。

用法：python tests/run_song_core_java.py
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'mc-song-mod'
CORE = MOD / 'src' / 'main' / 'java' / 'com' / 'qingcheng' / 'songmod'
TEST = Path(__file__).resolve().parent / 'java' / 'SongCoreTest.java'
FILES = ['SongSpeed.java', 'SongProtocol.java', 'SongLyrics.java', 'Mp3Track.java']


def _safe(text: str) -> str:
    """Windows 控制台可能是 GBK：把编不出来/看不懂的字符换成 '?'，别让测试脚本自己崩。"""
    text = (text or '').replace('\ufffd', '?')
    try:
        text.encode(sys.stdout.encoding or 'utf-8')
        return text
    except Exception:  # noqa: BLE001
        return text.encode(sys.stdout.encoding or 'utf-8', 'replace').decode(
            sys.stdout.encoding or 'utf-8', 'replace')


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass
    javac, java = shutil.which('javac'), shutil.which('java')
    if not javac or not java:
        raise SystemExit('A JDK with java and javac is required; tests were not run.')
    with tempfile.TemporaryDirectory(prefix='song-core-') as folder:
        sources = [str(CORE / name) for name in FILES] + [str(TEST)]
        compile_result = subprocess.run(
            [javac, '-encoding', 'UTF-8', '-nowarn', '-d', folder, *sources],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        if compile_result.returncode:
            print(_safe(compile_result.stdout[-4000:] + compile_result.stderr[-4000:]))
            raise SystemExit('compile failed')
        run_result = subprocess.run(
            [java, '-cp', folder, 'SongCoreTest'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        print(_safe(run_result.stdout), end='')
        if run_result.returncode:
            print(_safe(run_result.stderr))
            raise SystemExit('song core tests failed')


if __name__ == '__main__':
    main()
