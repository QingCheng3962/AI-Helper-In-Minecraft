# -*- coding: utf-8 -*-
"""把 AI Helper 打成单个 .exe 安装包（无需用户另装 Python / Node）。

做法：
1. 组装 payload/：程序文件 + 内置 Python 运行时（含依赖）+ node.exe + 生产 node_modules；
2. 用 csc 编译两个 C# 小工具：Setup.cs（安装向导）/ Launcher.cs（AIHelper.exe）；
3. payload 打成 zip，取安装器 exe 把 zip 追加在文件尾 + 写 footer（magic/偏移/长度）；
4. 安装器运行时读自己的尾巴、解压到用户选的目录，并创建快捷方式。

用法（任意目录均可，需本机有 Python 3.9 + csc + node）：
    python installer/build_installer.py                 # 带 NapCat（体积大）
    python installer/build_installer.py --no-napcat      # 不带 NapCat（体积小）
    python installer/build_installer.py --no-napcat --verify   # 构建后静默安装到临时目录并自检
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'installer'
BUILD = ROOT / 'build' / 'installer'
PAYLOAD = BUILD / 'payload'
NAPCAT_PAYLOAD = BUILD / 'napcat'
DIST = ROOT / 'dist' / 'installer'

PY_DIR = Path(r'C:\Users\HI\AppData\Local\Programs\Python\Python39')
CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
NODE_EXE = Path(r'C:\Program Files\nodejs\node.exe')
NPM_CMD = Path(r'C:\Program Files\nodejs\npm.cmd')
NAPCAT_DIR = ROOT / 'NapCat'
MOD_VERSION = '0.3.6-26.11.14'
MAGIC = b'AAFMINS1'
FOOTER = 48

#: 运行时要带的 site-packages（GUI + 网络 + 图片 + 加密 + 表格）
SITE_PACKAGES = [
    'httpx', 'httpcore', 'h11', 'anyio', 'idna', 'certifi',
    'charset_normalizer', 'openpyxl', 'et_xmlfile', 'PIL', 'qrcode',
    'cryptography', 'cffi', 'pycparser', 'customtkinter', 'darkdetect',
    'packaging', 'typing_extensions', 'png',
]

#: Lib 里不用带的（体积/无关）
LIB_SKIP = {'site-packages', 'test', 'tests', 'idlelib', 'lib2to3', 'ensurepip', 'unittest'}

#: 根目录要带上的文件
ROOT_FILES = [
    'main.py', 'bot_engine.js', 'qq_engine.js', 'bookwriter.js', 'quiz.js', 'autoeat.js',
    '3rdparty_auth.js', 'msa_auth.js', 'utils.js', 'logger.js', 'verify.js', 'app.js',
    'package.json', 'package-lock.json', 'requirements.txt', 'README.md', 'README_EN.md',
    'HELP.md', '更新日志.md', 'TODO.md', 'LICENSE', '.env.example',
]

#: 要带上的目录（相对路径）
ROOT_DIRS = ['aafm_py', 'docs', 'other_qq_pic_sent', 'promo']

#: 绝不能进安装包的用户数据
EXCLUDE_NAMES = {
    'config.json', 'mcai_chat.json', 'auth_profile.json', 'runtime_config.json',
    'qq_runtime_config.json', 'qq_accounts.json', 'affinity.json', 'daily.db', 'data.db',
    'book_progress.json', 'mcai_prompt_templates.json', 'relay.json', 'qrcode.png',
    'qq_qrcode.png', '__pycache__', '.pytest_cache',
}


def log(message: str) -> None:
    print('[build] ' + message, flush=True)


def run(command, **kwargs) -> None:
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', **kwargs)
    if result.returncode:
        print((result.stdout or '')[-3000:])
        print((result.stderr or '')[-3000:])
        raise SystemExit('命令失败：' + ' '.join(map(str, command)))


def copy_tree(src: Path, dst: Path, skip_names=None) -> None:
    skip = set(skip_names or ())
    for path in src.rglob('*'):
        rel = path.relative_to(src)
        if any(part in skip or part in EXCLUDE_NAMES for part in rel.parts):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def stage_app() -> None:
    log('组装程序文件…')
    for name in ROOT_FILES:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, PAYLOAD / name)
    for name in ROOT_DIRS:
        src = ROOT / name
        if src.is_dir():
            copy_tree(src, PAYLOAD / name)
    # 点歌 Mod / 服务端插件 jar（方便玩家直接拿）
    mod_dist = ROOT / 'mc-song-mod' / 'dist'
    if mod_dist.is_dir():
        (PAYLOAD / 'Mod').mkdir(parents=True, exist_ok=True)
        for jar in sorted(mod_dist.glob('Song_*.jar')):
            shutil.copy2(jar, PAYLOAD / 'Mod' / jar.name)
        mod_readme = ROOT / 'mc-song-mod' / 'README.md'
        if mod_readme.is_file():
            shutil.copy2(mod_readme, PAYLOAD / 'Mod' / 'README.md')
    bridge_dist = ROOT / 'mc-music-bridge' / 'dist'
    if bridge_dist.is_dir():
        (PAYLOAD / '服务端插件').mkdir(parents=True, exist_ok=True)
        for jar in bridge_dist.glob('*.jar'):
            shutil.copy2(jar, PAYLOAD / '服务端插件' / jar.name)


def stage_python() -> None:
    log('复制 Python 运行时（含依赖）…')
    runtime = PAYLOAD / 'runtime' / 'python'
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ('python.exe', 'pythonw.exe', 'python39.dll', 'python3.dll',
                 'vcruntime140.dll', 'vcruntime140_1.dll'):
        src = PY_DIR / name
        if src.is_file():
            shutil.copy2(src, runtime / name)
    copy_tree(PY_DIR / 'DLLs', runtime / 'DLLs')
    if (PY_DIR / 'tcl').is_dir():
        copy_tree(PY_DIR / 'tcl', runtime / 'tcl')
    for name in ('lib', 'tkinter', 'sqlite3', 'encodings', 'importlib', 'json',
                 'urllib', 'http', 'ssl', 'ctypes', 'logging', 'email', 'html',
                 'xml', 'csv', 'asyncio', 'concurrent', 'multiprocessing',
                 'unittest', 'collections', 'concurrent'):
        pass
    # 标准库：整份 Lib 但跳过 site-packages 与用不到的大目录
    for path in (PY_DIR / 'Lib').iterdir():
        if path.name in LIB_SKIP or path.name in EXCLUDE_NAMES:
            continue
        target = runtime / 'Lib' / path.name
        if path.is_dir():
            copy_tree(path, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    # 依赖（用 importlib 找真实位置，兼容单文件模块）
    import importlib.util
    packages = runtime / 'Lib' / 'site-packages'
    packages.mkdir(parents=True, exist_ok=True)
    for name in SITE_PACKAGES:
        spec = importlib.util.find_spec(name) if name.replace('_', '').isalnum() else None
        roots = list(spec.submodule_search_locations or []) if spec else []
        if roots:
            for root in roots:
                src_dir = Path(root)
                target = packages / src_dir.name
                if src_dir.is_dir():
                    copy_tree(src_dir, target)
                else:
                    shutil.copy2(src_dir, packages / src_dir.name)
        else:
            # 退回按目录名 / 单文件模块（xxx.py）找
            base = PY_DIR / 'Lib' / 'site-packages'
            src = base / name
            if not src.exists():
                src = base / (name + '.py')
            if not src.exists():
                log('  ! 缺少依赖 ' + name + '（跳过）')
                continue
            if src.is_dir():
                copy_tree(src, packages / name)
            else:
                shutil.copy2(src, packages / src.name)
    # 依赖里的 .pyd 可能要 adjacent dll：把 site-packages 顶层 dll 一起带上
    for dll in (PY_DIR / 'Lib' / 'site-packages').glob('*.dll'):
        shutil.copy2(dll, packages / dll.name)


def stage_node() -> None:
    log('复制 Node 运行时…')
    (PAYLOAD / 'runtime' / 'node').mkdir(parents=True, exist_ok=True)
    shutil.copy2(NODE_EXE, PAYLOAD / 'runtime' / 'node' / 'node.exe')
    log('复制 node_modules（仅生产依赖）…')
    result = subprocess.run([str(NPM_CMD), 'ls', '--omit=dev', '--parseable', '--all'],
                            cwd=str(ROOT), capture_output=True, text=True,
                            encoding='utf-8', errors='replace', shell=True)
    lines = [line.strip() for line in (result.stdout or '').splitlines() if line.strip()]
    modules = ROOT / 'node_modules'
    copied = set()
    count = 0
    for line in lines:
        path = Path(line)
        if not path.is_dir() or path == modules:
            continue
        try:
            rel = path.relative_to(modules)
        except ValueError:
            continue
        # 父包已复制过就不再重复（nested node_modules 归父包）
        if any(str(rel).lower().startswith(str(done).lower() + os.sep) for done in copied):
            continue
        target = PAYLOAD / 'node_modules' / rel
        copy_tree(path, target)
        copied.add(rel)
        count += 1
    log('  生产依赖目录：%d 个' % count)


def stage_napcat() -> None:
    if not NAPCAT_DIR.is_dir():
        log('! 没有 NapCat 目录，跳过（QQ 转述将不可用）')
        return
    log('复制 NapCat（325MB 左右，慢）…')
    copy_tree(NAPCAT_DIR, NAPCAT_PAYLOAD / 'NapCat', skip_names={'node_modules', 'logs', 'cache'})


def compile_tools() -> Path:
    log('编译启动器 / 安装器（csc）…')
    launcher = PAYLOAD / 'AIHelper.exe'
    run([str(CSC), '/nologo', '/target:winexe', '/platform:anycpu',
         '/out:' + str(launcher), '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll',
         str(INSTALLER / 'Launcher.cs')])
    stub = BUILD / 'SetupStub.exe'
    run([str(CSC), '/nologo', '/target:winexe', '/platform:anycpu',
         '/out:' + str(stub),
         '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll',
         '/r:System.IO.Compression.dll', '/r:System.IO.Compression.FileSystem.dll',
         str(INSTALLER / 'Setup.cs')])
    return stub


def write_extras() -> None:
    bat = PAYLOAD / '启动.bat'
    bat.write_text('@echo off\r\ncd /d "%~dp0"\r\nstart "" "AIHelper.exe"\r\n',
                   encoding='utf-8')
    readme = (PAYLOAD / '安装说明.txt')
    readme.write_text(
        'AI Helper In Minecraft 安装完成后：\r\n'
        '1) 双击 AIHelper.exe（或桌面/开始菜单快捷方式）启动；\r\n'
        '2) 内置 Python 3.9 + Node 运行时，不需要另外安装；\r\n'
        '3) 数据在 data.db / daily.db，配置在 config.json / mcai_chat.json；\r\n'
        '4) 点歌 Mod 在 Mod 目录里（按游戏版本选对应 jar 放进 mods/）。\r\n',
        encoding='utf-8')


def zip_dir(src: Path, out: Path) -> None:
    log('打包 ' + out.name + ' …')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in src.rglob('*'):
            if path.is_file():
                zf.write(path, str(path.relative_to(src)).replace('\\', '/'))


def assemble(stub: Path, app_zip: Path, nap_zip, output: Path) -> None:
    log('拼装安装包…')
    data = bytearray(stub.read_bytes())
    app_off = len(data)
    data += app_zip.read_bytes()
    nap_off = len(data)
    nap_len = 0
    if nap_zip is not None and nap_zip.exists():
        data += nap_zip.read_bytes()
        nap_len = nap_zip.stat().st_size
    footer = MAGIC + app_off.to_bytes(8, 'little') + app_zip.stat().st_size.to_bytes(8, 'little') \
        + nap_off.to_bytes(8, 'little') + nap_len.to_bytes(8, 'little') + b'\0' * 8
    assert len(footer) == FOOTER, len(footer)
    data += footer
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    if nap_zip is not None and nap_zip.exists():
        nap_zip.unlink()
    log('完成：%s（%.1f MB）' % (output.name, output.stat().st_size / (1 << 20)))


def verify(output: Path) -> None:
    log('静默安装自检…')
    target = Path(tempfile.mkdtemp(prefix='aafm-install-'))
    proc = subprocess.run([str(output), '/S', '/D=' + str(target), '/NAPCAT=0'],
                          capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=600)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        raise SystemExit('安装自检失败：exit=' + str(proc.returncode))
    checks = ['main.py', 'AIHelper.exe', 'runtime/python/python.exe',
              'runtime/python/Lib/tkinter/__init__.py',
              'runtime/node/node.exe', 'node_modules/mineflayer/package.json',
              'aafm_py/gui_ctk.py', 'Mod/Song_0.1.0_1.21.11.jar']
    missing = [name for name in checks if not (target / name).exists()]
    if missing:
        raise SystemExit('安装后缺少文件：' + ', '.join(missing))
    env = dict(os.environ)
    env['PYTHONPATH'] = str(target)
    code = ('import aafm_py.storage, aafm_py.gui_ctk, aafm_py.portscan, aafm_py.controller; '
            'import aafm_py.gui, aafm_py.ai_player, aafm_py.netease, aafm_py.bilibili; '
            'import aafm_py.roster, aafm_py.dailydb, aafm_py.receipt_printer, aafm_py.websearch; '
            'import aafm_py.photo_server, aafm_py.docbook, aafm_py.llm, aafm_py.updater; '
            'import customtkinter, httpx, PIL, qrcode, cryptography, openpyxl; '
            'print("IMPORT_OK")')
    result = subprocess.run([str(target / 'runtime/python/python.exe'), '-c', code],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', cwd=str(target), env=env, timeout=300)
    if result.returncode or 'IMPORT_OK' not in (result.stdout or ''):
        print((result.stdout or '')[-2000:])
        print((result.stderr or '')[-2000:])
        raise SystemExit('内置 Python 依赖自检失败')
    log('自检通过：' + str(target))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-napcat', action='store_true')
    parser.add_argument('--keep', action='store_true', help='保留 build/installer 中间文件')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    started = time.time()
    for folder in (PAYLOAD, NAPCAT_PAYLOAD):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)
    stage_app()
    stage_python()
    stage_node()
    write_extras()
    if not args.no_napcat:
        stage_napcat()
    stub = compile_tools()
    app_zip = BUILD / 'app.zip'
    zip_dir(PAYLOAD, app_zip)
    nap_zip = None
    if NAPCAT_PAYLOAD.exists() and any(NAPCAT_PAYLOAD.rglob('*')):
        nap_zip = BUILD / 'napcat.zip'
        zip_dir(NAPCAT_PAYLOAD, nap_zip)
    output = DIST / ('AIHelper-Setup-v%s.exe' % MOD_VERSION)
    assemble(stub, app_zip, nap_zip, output)
    if args.verify:
        verify(output)
    if not args.keep:
        shutil.rmtree(PAYLOAD, ignore_errors=True)
        shutil.rmtree(NAPCAT_PAYLOAD, ignore_errors=True)
        for junk in (BUILD / 'SetupStub.exe', app_zip,
                     BUILD / 'napcat.zip'):
            try:
                junk.unlink()
            except OSError:
                pass
    log('用时 %.1f 秒' % (time.time() - started))
    print(str(output))


if __name__ == '__main__':
    main()
