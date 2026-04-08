# tests/upgrade/test_state.py
"""
升级状态管理测试。
"""

import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from worker.upgrade.state import (
    get_state_file_path,
    save_state,
    load_state,
    clear_state,
)
from worker.upgrade.models import UpgradeState


class TestStateFilePath:
    """测试状态文件路径获取。"""

    def test_get_state_file_path_frozen(self):
        """测试打包后路径获取。"""
        # 使用 create=True 来模拟 sys.frozen 属性（默认不存在）
        with patch('sys.frozen', True, create=True):
            with patch('sys.executable', '/path/to/test-worker.exe'):
                path = get_state_file_path()
                # 验证路径结尾是 upgrade.json，且位于 executable 同级目录
                assert path.endswith('upgrade.json')
                assert 'path' in path.lower()

    def test_get_state_file_path_development(self):
        """测试开发模式路径获取。"""
        # sys.frozen 默认不存在
        path = get_state_file_path()
        assert path.endswith('upgrade.json')


class TestSaveLoadState:
    """测试状态保存和加载。"""

    def test_save_and_load_state(self, tmp_path):
        """测试状态保存和加载完整流程。"""
        # 使用临时目录
        state_file = tmp_path / "upgrade.json"

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            # 创建状态
            state = UpgradeState(
                status="downloading",
                target_version="20260408150000",
                current_version="20260405120000",
                download_url="http://example.com/installer.exe",
                started_at="2026-04-08T15:00:00",
            )

            # 保存
            save_state(state)

            # 验证文件存在
            assert state_file.exists()

            # 加载
            loaded = load_state()
            assert loaded is not None
            assert loaded.status == "downloading"
            assert loaded.target_version == "20260408150000"
            assert loaded.current_version == "20260405120000"

    def test_load_state_not_exists(self, tmp_path):
        """测试加载不存在的状态文件。"""
        state_file = tmp_path / "upgrade.json"

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            loaded = load_state()
            assert loaded is None

    def test_load_state_invalid_json(self, tmp_path):
        """测试加载无效 JSON 文件。"""
        state_file = tmp_path / "upgrade.json"
        state_file.write_text("invalid json content")

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            loaded = load_state()
            assert loaded is None


class TestClearState:
    """测试状态清除。"""

    def test_clear_state_existing(self, tmp_path):
        """测试清除存在的状态文件。"""
        state_file = tmp_path / "upgrade.json"
        state_file.write_text("{}")

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            clear_state()
            assert not state_file.exists()

    def test_clear_state_not_exists(self, tmp_path):
        """测试清除不存在的状态文件（无错误）。"""
        state_file = tmp_path / "upgrade.json"

        with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
            clear_state()  # 应该不报错
            assert not state_file.exists()