"""
Runtime hook for OpenCV compatibility in frozen environment.

PyInstaller 打包后 cv2 可能缺少 __version__ 属性，导致 pyscreeze 导入失败。
此 hook 在应用启动时修复这个问题。
"""

import sys

# 如果 cv2 缺少 __version__，添加一个假的版本号
if hasattr(sys, 'frozen'):
    try:
        import cv2
        if not hasattr(cv2, '__version__'):
            cv2.__version__ = '4.5.0'
    except ImportError:
        pass