"""
打包环境检测工具模块。

支持 Nuitka 和 PyInstaller 两种打包方式。
"""

import sys
import os


def _find_main_exe(exe_dir):
    """在目录中查找主程序 exe（非 python.exe）。"""
    try:
        for f in os.listdir(exe_dir):
            if f.lower().endswith('.exe') and f.lower() != 'python.exe':
                return True
    except OSError:
        pass
    return False


def is_packaged():
    """检测是否在打包环境中（支持 Nuitka 和 PyInstaller）。"""
    # Nuitka 打包后会设置 __compiled__ 属性
    if hasattr(sys, '__compiled__'):
        return True
    # PyInstaller 打包后会设置 sys.frozen
    if getattr(sys, 'frozen', False):
        return True
    # Nuitka standalone 模式：sys.executable 是 python.exe，目录中有主程序 exe
    exe_dir = os.path.dirname(sys.executable)
    exe_name = os.path.basename(sys.executable).lower()
    if exe_name == 'python.exe':
        return _find_main_exe(exe_dir)
    # 如果 sys.executable 不是 python.exe，检查是否在打包目录中
    # （Nuitka 可能直接使用主程序 exe 作为 sys.executable）
    if exe_dir and os.path.basename(sys.executable).lower().endswith('.exe'):
        # 检查目录中是否有 common/packaging.py（打包后会有）
        # 或者检查是否有 Nuitka 特征文件
        if os.path.exists(os.path.join(exe_dir, 'python3.dll')):
            return True
    return False


def _resolve_packaged_dir():
    """解析打包环境的应用目录。"""
    exe_dir = os.path.dirname(sys.executable)
    exe_name = os.path.basename(sys.executable).lower()
    if exe_name == 'python.exe':
        return exe_dir
    return exe_dir


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
