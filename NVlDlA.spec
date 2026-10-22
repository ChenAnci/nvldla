# -*- mode: python ; coding: utf-8 -*-
# NVlDlA 打包配置（PyInstaller onefile + windowed）
#
# 用法（在仓库根执行）：
#   faker\.venv\Scripts\python.exe -m PyInstaller --noconfirm NVlDlA.spec
#
# 产物：dist\NVlDlA.exe（单文件，双击即用；配置/会话写入 exe 所在目录）

from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "screenshotask" / "nvidia.py")],
    pathex=[str(ROOT / "screenshotask"), str(ROOT / "faker")],
    binaries=[],
    datas=[
        # 托盘/窗口图标 → 解压目录根（main.py frozen 时从 _MEIPASS/icon.png 读取）
        (str(ROOT / "screenshotask" / "icon.png"), "."),
        # DeepSeek PoW 求解器（webchat 包内数据文件 → webchat/ 子目录）
        (str(ROOT / "faker" / "webchat" / "pow_solver.mjs"), "webchat"),
        (str(ROOT / "faker" / "webchat" / "sha3_wasm_bg.wasm"), "webchat"),
    ],
    hiddenimports=[
        "webchat",
        "webchat.config",
        "webchat.doubao",
        "webchat.deepseek",
        "webchat.doubao_login",
        "webchat.errors",
        "webchat.sse",
        "main",
        "chat_worker",
        "config",
        "hotkey",
        "login_worker",
        "overlay",
        "settings",
        "anticapture",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排除用不到的重型库，控制体积；playwright 仅在 DeepSeek 浏览器登录时需要
    # （打包版以豆包为主，DeepSeek 请源码运行），tkinter 等无用 GUI 一并排除
    excludes=["tkinter", "PyQt5", "PySide2", "PySide6", "playwright", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NVlDlA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "screenshotask" / "icon.png"),
)
