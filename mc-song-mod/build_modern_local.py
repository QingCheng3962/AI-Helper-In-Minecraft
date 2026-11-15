"""26.x 离线打包脚本：javac + zipfile（不经过 Gradle/Loom）。

为什么需要它：本机沙箱环境下 Java 的 zipfs 把工作区判定为只读，Gradle/Loom 写
mapping/jar 会抛 ReadOnlyFileSystemException；而 26.x 的 Minecraft 未混淆、**不需要
remap**，所以直接用 javac 编译 + Python zipfile 打包就能产出可用的 Fabric mod jar
（结构与 Gradle 产物一致：classes + fabric.mod.json + assets + META-INF/jars/jlayer）。

用法（在工作区根目录）：python mc-song-mod/build_modern_local.py 26.1.2 26.1 26.1.1 26.2
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MOD = Path(__file__).resolve().parent
ROOT = MOD.parent
SELF = Path(__file__).resolve()
JDK = Path(r'C:\Program Files\Java\latest\jdk-25')
JAVAC = JDK / 'bin' / 'javac.exe'
JAVA = JDK / 'bin' / 'java.exe'
#: 各版本已缓存的 Minecraft 合并 jar（Loom 之前下载/重映射过）
MERGED = {
    '26.1': MOD / '.gradle/loom-cache/minecraftMaven/net/minecraft/minecraft-merged-4ac4624eb9/26.1/minecraft-merged-4ac4624eb9-26.1.jar',
    '26.1.1': MOD / '.gradle/loom-cache/minecraftMaven/net/minecraft/minecraft-merged-4ac4624eb9/26.1.1/minecraft-merged-4ac4624eb9-26.1.1.jar',
    '26.1.2': MOD / '.gradle/loom-cache/minecraftMaven/net/minecraft/minecraft-merged-e132aec792/26.1.2/minecraft-merged-e132aec792-26.1.2.jar',
    '26.2': MOD / '.gradle/loom-cache/minecraftMaven/net/minecraft/minecraft-merged-a8e9c7b123/26.2/minecraft-merged-a8e9c7b123-26.2.jar',
}
MOD_VERSION = '0.1.0'
JLAYER = MOD / 'build/processIncludeJars/jlayer-1.0.1-1.jar'


#: 各版本对应的 Fabric API 聚合 jar 里带的模块版本（从 Gradle 依赖表来）
FABRIC_API_VERSION = {
    '26.1': '0.145.1+26.1',
    '26.1.1': '0.145.4+26.1.1',
    '26.1.2': '0.155.3+26.1.2',
    '26.2': '0.161.0+26.2',
}


def _modules_dir(version: str) -> Path:
    """把 Fabric API 聚合 jar 里的嵌套模块 jar 解出来（每个 MC 版本一份）。"""
    target = MOD / 'build/api26' / version
    marker = target / '.extracted'
    if marker.exists():
        return target
    wanted = FABRIC_API_VERSION.get(version, '')
    candidates = []
    for root in (Path.home() / '.gradle/caches/modules-2/files-2.1',
                 MOD / '.gradle-home/caches/modules-2/files-2.1'):
        if root.exists():
            candidates.extend(root.rglob('fabric-api-' + wanted + '.jar'))
    if not candidates:
        # 没缓存就用 26.1.2 的（事件接口在 26.x 之间是稳定的），并提示
        print('[%s] 没找到 fabric-api %s 的聚合 jar，改用 26.1.2 的模块编译' % (version, wanted))
        fallback = _modules_dir('26.1.2')
        return fallback
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(candidates[0]) as zf:
        for name in zf.namelist():
            if name.startswith('META-INF/jars/') and name.endswith('.jar'):
                (target / Path(name).name).write_bytes(zf.read(name))
    marker.write_text('ok', encoding='utf-8')
    return target


def find_jars(version: str) -> list:
    """Minecraft 之外需要的依赖：Fabric API 模块 + gson / fastutil。"""
    jars = [str(p) for p in _modules_dir(version).glob('*.jar')]
    caches = [
        Path.home() / '.gradle/caches/modules-2/files-2.1',
        MOD / '.gradle-home/caches/modules-2/files-2.1',
    ]
    wanted = ['fabric-loader-', 'brigadier-', 'authlib-', 'jspecify-',
              'datafixerupper-', 'gson-', 'fastutil-']
    picked = {}
    for root in caches:
        if not root.exists():
            continue
        for path in root.rglob('*.jar'):
            name = path.name
            if 'sources' in name or 'javadoc' in name:
                continue
            for prefix in wanted:
                if name.startswith(prefix) and prefix not in picked:
                    picked[prefix] = str(path)
                    break
    return jars + list(picked.values())


def sources(version: str) -> list:
    out = []
    for part in ('src/main/java', 'src/modern/java'):
        out.extend(str(p) for p in (MOD / part).rglob('*.java'))
    return out


def write_argfile(path: Path, cp: str, files: list) -> None:
    lines = ['-encoding', 'UTF-8', '--release', '25', '-nowarn', '-Xmaxerrs', '60',
             '-classpath', '"' + cp + '"', '-d', '"' + str(MOD / 'build/modern-classes') + '"']
    lines += ['"' + f.replace(chr(92), '/') + '"' for f in files]
    path.write_text(chr(10).join(lines), encoding='utf-8')


def build(version: str) -> bool:
    mc_jar = MERGED.get(version)
    if not mc_jar or not mc_jar.exists():
        print('[%s] 缺少 Minecraft jar：%s' % (version, mc_jar))
        return False
    deps = find_jars(version)
    cp = ';'.join([str(mc_jar)] + deps + [str(JLAYER)])
    classes = MOD / 'build/modern-classes'
    if classes.exists():
        shutil.rmtree(classes)
    files = sources(version)
    command = [str(JAVAC), '-encoding', 'UTF-8', '--release', '25', '-nowarn',
               '-Xmaxerrs', '60', '-d', str(classes), '-classpath', cp] + files
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if result.returncode:
        print('[%s] javac 失败：' % version)
        print((result.stdout or '')[-4000:])
        print((result.stderr or '')[-2000:])
        return False

    # 资源：assets + fabric.mod.json（按版本展开占位符）
    resource_root = MOD / 'src/main/resources'
    mod_json = json.loads((resource_root / 'fabric.mod.json').read_text(encoding='utf-8'))
    mod_json['version'] = MOD_VERSION
    mod_json['depends']['minecraft'] = '~' + version
    mod_json['depends']['java'] = '>=25'
    mod_json['jars'] = [{'file': 'META-INF/jars/' + JLAYER.name}]

    out = MOD / 'dist' / ('Song_%s_%s.jar' % (MOD_VERSION, version))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='songmodern-'))
    try:
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('META-INF/MANIFEST.MF',
                        'Manifest-Version: 1.0' + chr(13) + chr(10) + chr(13) + chr(10))
            zf.writestr('fabric.mod.json', json.dumps(mod_json, ensure_ascii=False, separators=(',', ':')))
            for path in classes.rglob('*.class'):
                zf.write(path, str(path.relative_to(classes)).replace(chr(92), '/'))
            assets = resource_root / 'assets'
            if assets.exists():
                for path in assets.rglob('*'):
                    if path.is_file():
                        zf.write(path, str(path.relative_to(resource_root)).replace(chr(92), '/'))
            zf.write(JLAYER, 'META-INF/jars/' + JLAYER.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 自检：能打开、fabric.mod.json 正确、包含入口类与嵌套 jar
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        meta = json.loads(zf.read('fabric.mod.json'))
        assert meta['depends']['minecraft'] == '~' + version, meta['depends']
        assert 'com/qingcheng/songmod/SongModClient.class' in names
        assert 'META-INF/jars/' + JLAYER.name in names
    print('[%s] OK -> %s (%d KB, %d entries)' % (
        version, out.name, out.stat().st_size // 1024, len(names)))
    return True


def main() -> None:
    versions = sys.argv[1:] or ['26.1.2', '26.1', '26.1.1', '26.2']
    ok = 0
    for version in versions:
        if build(version):
            ok += 1
    print('完成 %d/%d' % (ok, len(versions)))
    raise SystemExit(0 if ok == len(versions) else 1)


if __name__ == '__main__':
    main()
