# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件。

使用目录模式打包，生成 test-worker.exe 和 _internal 目录。
"""

import os
import sys

block_cipher = None

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

# 收集数据文件
datas = [
    (os.path.join(PROJECT_ROOT, 'config'), 'config'),
]

# 收集隐藏导入
hiddenimports = [
    # FastAPI / Uvicorn
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'starlette',
    'starlette.responses',
    'starlette.routing',
    'starlette.middleware',
    'starlette.exceptions',
    # HTTP 客户端
    'httpx',
    'h11',
    'h2',
    'hpack',
    # Playwright
    'playwright',
    'playwright.sync_api',
    'playwright._impl',
    # Appium
    'appium',
    'appium.webdriver',
    'appium.options',
    'appium.options.android',
    'appium.options.ios',
    'selenium',
    'selenium.webdriver',
    # 桌面自动化
    'pyautogui',
    'pyscreeze',
    'pygetwindow',
    'mouseinfo',
    'pyrect',
    # 图像处理
    'PIL',
    'PIL.Image',
    'cv2',
    # 工具
    'yaml',
    'dotenv',
    'psutil',
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'worker', 'main.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest',
        'allure',
        'faker',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 目录模式：EXE 不包含依赖，由 COLLECT 收集
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # 不包含二进制文件
    name='test-worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# 收集所有依赖到 dist/test-worker 目录
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='test-worker',
)