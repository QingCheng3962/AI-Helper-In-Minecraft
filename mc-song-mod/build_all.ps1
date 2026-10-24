# 全版本构建脚本（可在任意目录/任意电脑运行）
# 用法：powershell -ExecutionPolicy Bypass -File build_all.ps1

$ErrorActionPreference = "Stop"

# 工程目录 = 脚本所在目录（不再写死某台电脑的路径）
$proj = $PSScriptRoot
if (-not $proj) { $proj = Split-Path -Parent $MyInvocation.MyCommand.Path }
$dist = Join-Path $proj "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# --- JDK：优先用户已有的 JAVA_HOME，其次在常见位置找 JDK 25 ---
function Test-Jdk([string]$path) {
    if (-not $path) { return $false }
    return (Test-Path (Join-Path $path "bin\java.exe"))
}
if (-not (Test-Jdk $env:JAVA_HOME)) {
    $candidates = @()
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "Java\latest\jdk-25")
        $candidates += (Join-Path $env:ProgramFiles "Java\jdk-25")
        $javaRoot = Join-Path $env:ProgramFiles "Java"
        if (Test-Path $javaRoot) {
            $candidates += Get-ChildItem $javaRoot -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "jdk-25*" } |
                Sort-Object Name -Descending |
                ForEach-Object { $_.FullName }
        }
    }
    $found = $candidates | Where-Object { Test-Jdk $_ } | Select-Object -First 1
    if (-not $found) {
        Write-Error "找不到 JDK 25。请安装 JDK 25，或设置 JAVA_HOME 指向 JDK 25 后重试（26.x 的 Loom 需要 Java 25）。"
        exit 1
    }
    $env:JAVA_HOME = $found
}
Write-Output "JAVA_HOME = $env:JAVA_HOME"

# --- Gradle：优先项目自带 wrapper，其次 PATH 里的 gradle ---
$gradlew = Join-Path $proj "gradlew.bat"
if (Test-Path $gradlew) {
    $g = $gradlew
} else {
    $cmd = Get-Command "gradle.bat" -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command "gradle" -ErrorAction SilentlyContinue }
    if (-not $cmd) {
        Write-Error "找不到 Gradle。请把 Gradle 加入 PATH，或在 mc-song-mod 下提供 gradlew.bat。"
        exit 1
    }
    $g = $cmd.Source
}
Write-Output "Gradle = $g"

# --- Mod 版本从 gradle.properties 读取（不再写死旧版号） ---
$propsFile = Join-Path $proj "gradle.properties"
$modVer = "0.1.0"
if (Test-Path $propsFile) {
    $line = Select-String -Path $propsFile -Pattern '^\s*mod_version\s*=\s*(\S+)' |
        Select-Object -First 1
    if ($line) { $modVer = $line.Matches[0].Groups[1].Value.Trim() }
}
Write-Output "Mod version = $modVer"

$vers = @('26.1.2','1.21.11','1.21.1','1.21.2','1.21.3','1.21.4','1.21.5','1.21.6','1.21.7','1.21.8',
          '1.21.9','1.21.10','26.1','26.1.1','26.2')

$results = @()
foreach ($v in $vers) {
    Write-Output "==================== MC $v ===================="
    $libs = Join-Path $proj "build\libs"
    $src = Join-Path $libs "Song_${modVer}_$v.jar"
    $dst = Join-Path $dist "Song_${modVer}_$v.jar"
    # 先删掉同名旧产物，避免「构建失败/没产出却复制到上次的 jar」
    if (Test-Path $src) { Remove-Item $src -Force -ErrorAction SilentlyContinue }
    if (Test-Path $dst) { Remove-Item $dst -Force -ErrorAction SilentlyContinue }

    $log = & $g -p $proj build "-Pmcver=$v" --no-daemon --console=plain 2>&1
    $exitCode = $LASTEXITCODE
    $ok = ($exitCode -eq 0) -and (Test-Path $src)
    if ($ok) {
        Copy-Item $src $dst -Force
        Write-Output "RESULT $v : OK"
        $results += "$v OK"
    } elseif ($exitCode -eq 0) {
        Write-Output "RESULT $v : BUILD OK but jar missing ($src)"
        $results += "$v JAR-MISSING"
    } else {
        Write-Output "RESULT $v : FAILED (exit $exitCode)"
        $log | Select-Object -Last 25 | ForEach-Object { Write-Output ("   " + $_) }
        $results += "$v FAILED"
    }
}
Write-Output "==================== SUMMARY ===================="
$results | ForEach-Object { Write-Output $_ }
Write-Output "ALL DONE"
