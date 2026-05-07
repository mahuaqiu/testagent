"""
打包环境检测工具模块。

支持 Nuitka 打包方式。
"""

import sys
import os


def is_packaged():
    """检测是否在 Nuitka 打包环境中。

    检测逻辑：
    1. Nuitka 打包后会设置 __compiled__ 属性
    2. 其他情况视为普通 Python 环境

    Returns:
        bool: True 表示打包环境，False 表示普通 Python 环境
    """
    # Nuitka 打包后会设置 __compiled__ 属性
    if hasattr(sys, '__compiled__'):
        return True

    return False


def _resolve_packaged_dir():
    """解析打包环境的应用目录。"""
    return os.path.dirname(sys.executable)


def get_app_dir():
    """获取应用目录（打包环境为 exe 所在目录，开发环境为当前工作目录）。"""
    if is_packaged():
        return _resolve_packaged_dir()
    return os.getcwd()


def get_base_dir():
    """获取项目根目录（打包环境为 exe 所在目录，开发环境为项目根目录）。"""
    if is_packaged():
        return _resolve_packaged_dir()
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
