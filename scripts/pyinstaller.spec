# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件。

使用目录模式打包，生成 test-worker.exe 和 _internal 目录。
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

# 收集数据文件
datas = [
    (os.path.join(PROJECT_ROOT, 'config'), 'config'),
    (os.path.join(PROJECT_ROOT, 'assets'), 'assets'),  # 图标文件
]

# 收集 cv2 (OpenCV) 数据文件
try:
    datas += collect_data_files('cv2')
except Exception:
    pass

# 收集 email-validator 数据文件和元数据（Pydantic EmailStr 类型需要）
try:
    datas += collect_data_files('email_validator', include_py_files=False)
except Exception:
    pass

# 收集 pydantic 数据文件和元数据
try:
    datas += collect_data_files('pydantic', include_py_files=False)
except Exception:
    pass

# 收集 pydantic-core 数据文件和元数据
try:
    datas += collect_data_files('pydantic_core', include_py_files=False)
except Exception:
    pass

# 收集 uiautomator2 数据文件（关键：包含 u2.jar 和 app-uiautomator.apk）
# 这些文件需要推送到 Android 设备上才能正常运行
try:
    datas += collect_data_files('uiautomator2', include_py_files=False)
except Exception:
    pass

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
    # Android 直连 (uiautomator2)
    'uiautomator2',
    'uiautomator2.__main__',
    # iOS 直连 (tidevice3)
    'tidevice3',
    'tidevice3.cli',
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
    'numpy',
    'numpy.core',
    'numpy.core._multiarray_umath',
    # GUI 组件（新增）
    'pystray',
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'PyQt5.sip',
    # 工具
    'yaml',
    'dotenv',
    'psutil',
    'email_validator',
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'worker', 'gui_main.py')],
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
    console=False,        # 关闭 CMD 窗口
    uac_admin=True,       # 管理员权限
    icon=os.path.join(PROJECT_ROOT, 'assets', 'icon.ico'),  # EXE 图标
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
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='test-worker',
)