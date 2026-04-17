"""
worker/tools.py 单元测试。
"""

import json
import os
import sys
import tempfile

import pytest

from worker.tools import (
    get_tools_dir,
    validate_script_name,
    get_script_version,
    update_script_version,
    save_script,
    script_exists,
)


class TestValidateScriptName:
    """测试脚本名称校验。"""

    def test_valid_ps1_script(self):
        """合法的 .ps1 脚本名称。"""
        assert validate_script_name("play_ppt.ps1") is True

    def test_valid_sh_script(self):
        """合法的 .sh 脚本名称。"""
        assert validate_script_name("play_video.sh") is True

    def test_valid_bat_script(self):
        """合法的 .bat 脚本名称。"""
        assert validate_script_name("install.bat") is True

    def test_invalid_extension(self):
        """非法扩展名。"""
        assert validate_script_name("script.py") is False
        assert validate_script_name("script.exe") is False
        assert validate_script_name("script.txt") is False

    def test_path_traversal(self):
        """路径穿越攻击。"""
        assert validate_script_name("../evil.ps1") is False
        assert validate_script_name("subdir/script.ps1") is False
        assert validate_script_name("..\\evil.ps1") is False

    def test_empty_name(self):
        """空名称。"""
        assert validate_script_name("") is False


class TestScriptVersion:
    """测试脚本版本管理。"""

    def test_get_version_not_exists(self, tmp_path, monkeypatch):
        """版本文件不存在时返回 None。"""
        # 使用 monkeypatch 修改路径
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr("worker.tools.__file__", str(tmp_path / "worker" / "tools.py"))

        result = get_script_version("test.ps1")
        assert result is None

    def test_update_and_get_version(self, tmp_path, monkeypatch):
        """更新版本后可以获取。"""
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr("worker.tools.__file__", str(tmp_path / "worker" / "tools.py"))

        # 创建 tools 目录
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        # 更新版本
        update_script_version("test.ps1", "20260418-120000")

        # 获取版本
        result = get_script_version("test.ps1")
        assert result == "20260418-120000"


class TestSaveScript:
    """测试脚本保存。"""

    def test_save_script(self, tmp_path, monkeypatch):
        """保存脚本到文件。"""
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr("worker.tools.__file__", str(tmp_path / "worker" / "tools.py"))

        content = "param([string]$Path)\nWrite-Output $Path"
        result = save_script("test.ps1", content)

        # 验证文件存在
        assert os.path.exists(result)
        assert script_exists("test.ps1")

        # 验证内容
        with open(result, "r", encoding="utf-8") as f:
            assert f.read() == content

    def test_save_script_invalid_name(self, tmp_path, monkeypatch):
        """非法脚本名称抛出 ValueError。"""
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        monkeypatch.setattr("worker.tools.__file__", str(tmp_path / "worker" / "tools.py"))

        with pytest.raises(ValueError, match="非法脚本名称"):
            save_script("../evil.ps1", "bad content")