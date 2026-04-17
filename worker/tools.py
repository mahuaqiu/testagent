"""
脚本管理工具模块。

提供 tools 目录路径解析、脚本保存、版本管理等功能。
"""

import json
import os
import sys

from typing import Optional


def get_tools_dir() -> str:
    """
    获取 tools 目录的完整路径。

    打包后：exe 所在目录下的 tools
    开发时：项目根目录下的 tools

    Returns:
        str: tools 目录完整路径
    """
    if getattr(sys, 'frozen', False):
        # 打包后：exe 所在目录
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发时：项目根目录（worker 的上级目录）
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, 'tools')


def validate_script_name(name: str) -> bool:
    """
    校验脚本名称合法性。

    规则：
    - 只允许 .ps1、.sh、.bat 扩展名
    - 禁止路径穿越（../、/、\\）

    Args:
        name: 脚本名称

    Returns:
        bool: 是否合法
    """
    # 只允许合法扩展名
    allowed_exts = {'.ps1', '.sh', '.bat'}
    ext = os.path.splitext(name)[1].lower()
    if ext not in allowed_exts:
        return False

    # 禁止路径穿越
    if '..' in name or '/' in name or '\\' in name:
        return False

    return True


def get_versions_file() -> str:
    """获取版本记录文件路径。"""
    return os.path.join(get_tools_dir(), '.versions.json')


def get_script_version(name: str) -> Optional[str]:
    """
    获取脚本版本号。

    Args:
        name: 脚本名称

    Returns:
        str | None: 版本号，不存在则返回 None
    """
    versions_file = get_versions_file()
    if not os.path.exists(versions_file):
        return None

    try:
        with open(versions_file, 'r', encoding='utf-8') as f:
            versions = json.load(f)
        return versions.get(name)
    except (json.JSONDecodeError, IOError):
        return None


def update_script_version(name: str, version: str) -> None:
    """
    更新脚本版本记录。

    Args:
        name: 脚本名称
        version: 版本号
    """
    tools_dir = get_tools_dir()
    versions_file = get_versions_file()
    os.makedirs(tools_dir, exist_ok=True)

    # 读取现有版本记录
    versions = {}
    if os.path.exists(versions_file):
        try:
            with open(versions_file, 'r', encoding='utf-8') as f:
                versions = json.load(f)
        except json.JSONDecodeError:
            versions = {}

    # 更新版本
    versions[name] = version

    # 保存版本记录
    with open(versions_file, 'w', encoding='utf-8') as f:
        json.dump(versions, f, indent=2)


def save_script(name: str, content: str) -> str:
    """
    保存脚本到 tools 目录。

    Args:
        name: 脚本名称
        content: 脚本内容

    Returns:
        str: 脚本完整路径
    """
    tools_dir = get_tools_dir()
    os.makedirs(tools_dir, exist_ok=True)
    script_path = os.path.join(tools_dir, name)

    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return script_path


def script_exists(name: str) -> bool:
    """
    检查脚本是否已存在。

    Args:
        name: 脚本名称

    Returns:
        bool: 是否存在
    """
    script_path = os.path.join(get_tools_dir(), name)
    return os.path.exists(script_path)