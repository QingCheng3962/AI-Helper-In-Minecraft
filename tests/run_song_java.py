"""Compile unchanged production method bodies with offline Minecraft/network fixtures.

Run with Python 3 and a JDK on PATH. Actual Fabric builds are separate validation.
No game, audio device, credentials or external service is used.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
JAVA = Path(__file__).resolve().parent / 'java'


def methods(source, name):
    pattern = rf'^    (?:private|public) static[^\n]*\b{re.escape(name)}\([^\n]*\{{\n.*?^    }}'
    found = re.findall(pattern, source, flags=re.M | re.S)
    if not found:
        raise AssertionError('production method missing: ' + name)
    return '\n'.join(found)


def run(*args):
    result = subprocess.run(args, timeout=60, encoding='utf-8', errors='replace', capture_output=True)
    if result.returncode:
        print(result.stdout + result.stderr, flush=True)
        result.check_returncode()
    # Hide ordinary game diagnostics; failures retain complete compiler output.
    for line in result.stdout.splitlines():
        if line.startswith('PASS:'):
            print(line, flush=True)


def main():
    javac, java = shutil.which('javac'), shutil.which('java')
    if not javac or not java:
        raise SystemExit('A JDK with java and javac is required; tests were not run.')
    with tempfile.TemporaryDirectory(prefix='song-cancel-') as folder:
        folder = Path(folder)
        for era in ('legacy', 'modern'):
            source = (ROOT / f'mc-song-mod/src/{era}/java/com/qingcheng/songmod/SongModClient.java').read_text(encoding='utf-8')
            names = ['handleChat', 'playById', 'searchAndPlay', 'hintIfConfigured', 'isCurrent',
                     'stopPlayback', 'feedbackIfCurrent', 'feedback', 'eq', 'extractSongId',
                     'isForMe', 'normalizeForMatch', 'consumeMyRequest',
                     'shorten', 'songLabel', 'extractObfuscatedUrl', 'firstAudioUrl']
            if era == 'modern':
                names += ['addChatLine', 'callNoArg', 'getField']
            # Only Minecraft accessors and infrastructure are fixtures; every
            # cancellation/dispatch/publish method above is copied verbatim.
            path = folder / era / 'SongModClient.java'
            path.parent.mkdir()
            path.write_text('package com.qingcheng.songmod;\nimport java.util.concurrent.TimeUnit;\n'
                            'public class SongModClient {\n'
                            + (JAVA / 'ClientCancellationHarness.java').read_text(encoding='utf-8')
                            + '\n' + '\n'.join(methods(source, name) for name in names) + '\n}\n', encoding='utf-8')
            run(javac, '-encoding', 'UTF-8', '-d', str(path.parent), str(path))
            run(java, '-cp', str(path.parent), 'com.qingcheng.songmod.SongModClient')
            print(f'{era}: passed', flush=True)
        playback = folder / 'playback'
        playback.mkdir()
        run(javac, '-encoding', 'UTF-8', '-d', str(playback),
            str(JAVA / 'PlaybackCancellationTest.java'),
            str(JAVA / 'stubs/javazoom/jl/player/Player.java'),
            str(JAVA / 'stubs/com/qingcheng/songmod/VolumeAudioDevice.java'),
            str(ROOT / 'mc-song-mod/src/main/java/com/qingcheng/songmod/SongPlayer.java'))
        run(java, '-cp', str(playback), 'PlaybackCancellationTest')


if __name__ == '__main__':
    main()
