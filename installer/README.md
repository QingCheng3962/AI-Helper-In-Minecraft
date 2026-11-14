# 安装包（.exe）构建

把整个项目打成一个 **Windows 安装程序 exe**：用户选安装目录 → 释放文件 → 建快捷方式。
安装包**自带运行环境**，目标机器不需要另装 Python / Node。

## 构建

环境要求（只在**打包机**上需要）：Windows + Python 3.9（装好 `requirements.txt` 里的依赖）+ Node.js 22+（项目 `node_modules` 已 `npm install`）+ .NET Framework 4 的 `csc.exe`（Windows 自带）。

```powershell
# 1) 先构建点歌 Mod jar（可选，但安装包会把 dist/ 里的 jar 一起带上）
cd mc-song-mod
powershell -ExecutionPolicy Bypass -File build_all.ps1

# 2) 打安装包
cd ..
python installer/build_installer.py                # 带 NapCat（体积大）
python installer/build_installer.py --no-napcat    # 不带 NapCat
python installer/build_installer.py --no-napcat --verify   # 构建 + 静默安装自检
```

产物：`dist/installer/AIHelper-Setup-v0.3.6-26.11.15-Fxied.exe`（体积大，已在 `.gitignore`，发布走 GitHub Releases）。

## 安装包做了什么

1. 组装 `payload/`：程序文件 + `runtime/python`（Python 3.9 + 依赖）+ `runtime/node/node.exe` + `node_modules`（仅生产依赖）+ `Mod/`（15 个点歌 Mod jar）+ `服务端插件/`；
2. 用 `csc` 编译两个 C# 小工具：
   - `Setup.cs` → 安装向导（选路径 / 可选 NapCat / 进度 / 快捷方式）；
   - `Launcher.cs` → `AIHelper.exe`（用内置 Python 启动 `main.py`，并把 `runtime/node` 加进 PATH）；
3. payload 打成 zip，**追加**到安装器 exe 末尾并写 footer（magic + 偏移 + 长度）；
4. 安装器运行时读自己的尾巴，把 zip 解到用户选的目录。

静默安装（脚本/CI 用）：

```powershell
AIHelper-Setup-v0.3.6-26.11.15-Fxied.exe /S /D=D:\Games\AIHelper /NAPCAT=1
```

## 说明

- 安装包**不会**覆盖 `data.db` / `daily.db` / `config.json` 等用户数据（这些不在 payload 里）；
- NapCat 是可选组件（QQ 转述用），安装时勾选；
- 内置 Python 的精简方式：复制标准库（跳过 test/idlelib 等）+ 只带 `requirements.txt` 用到的 site-packages，体积约 90 MB（zip 后）。
