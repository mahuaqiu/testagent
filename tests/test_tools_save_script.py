"""save_script 落盘编码契约测试。

Windows PowerShell 5.1 对无 BOM 的 .ps1 按系统 ANSI 代码页（中文系统为 GBK）解码，
中文的 UTF-8 字节会被错误配对并吞掉引号/换行，导致脚本解析报错或字符串乱码。
因此 .ps1 必须带 UTF-8 BOM 落盘；.sh 依赖 shebang，不能带 BOM。
"""

from pathlib import Path

import pytest

from worker import tools


@pytest.fixture
def tools_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, 'get_tools_dir', lambda: str(tmp_path))
    return tmp_path


def test_save_script_ps1_writes_utf8_bom(tools_dir) -> None:
    """含中文的 .ps1 落盘必须带 UTF-8 BOM，否则 PowerShell 5.1 按 GBK 解析会报错。"""
    script_path = Path(tools.save_script('play.ps1', '# 中文注释\nWrite-Output "记事本"\n'))
    assert script_path.read_bytes().startswith(b'\xef\xbb\xbf')


def test_save_script_sh_keeps_bom_free(tools_dir) -> None:
    """.sh 依赖 shebang，BOM 会让内核解释器失效，必须保持无 BOM。"""
    script_path = Path(tools.save_script('run.sh', '#!/bin/bash\necho ok\n'))
    assert not script_path.read_bytes().startswith(b'\xef\xbb\xbf')


def test_save_script_content_roundtrip(tools_dir) -> None:
    """BOM 不应影响按 UTF-8 读回内容。"""
    content = '# 检查进程是否存在\nWrite-Output "窗口激活成功"\n'
    script_path = Path(tools.save_script('check.ps1', content))
    assert script_path.read_text(encoding='utf-8-sig') == content
