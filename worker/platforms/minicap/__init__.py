"""
Minicap 截图模块。

基于 openstf minicap 实现，支持绑过 FLAG_SECURE 防截屏限制。
"""

from worker.platforms.minicap.minicap import Minicap

__all__ = ["Minicap"]
