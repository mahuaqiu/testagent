"""测试配置路径函数。"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

from worker.config import get_user_config_path, get_default_template_path, load_config, WorkerConfig


class TestConfigPathFunctions:
    """测试配置路径获取函数。"""

    def test_get_user_config_path_development_mode(self):
        """开发模式下返回项目根目录 config/worker.yaml。"""
        # 开发模式（sys.frozen 不存在）
        path = get_user_config_path()
        # 验证路径格式
        assert path.endswith("config/worker.yaml") or path.endswith("config\\worker.yaml")
        assert "config" in path

    def test_get_default_template_path_development_mode(self):
        """开发模式下返回项目根目录 config/worker.yaml（与用户配置相同）。"""
        path = get_default_template_path()
        assert path.endswith("config/worker.yaml") or path.endswith("config\\worker.yaml")

    def test_user_and_template_path_same_in_dev_mode(self):
        """开发模式下用户配置和默认模板路径相同。"""
        user_path = get_user_config_path()
        template_path = get_default_template_path()
        assert user_path == template_path


class TestLoadConfigPriority:
    """测试配置加载优先级逻辑。"""

    def test_load_config_from_existing_user_config(self):
        """用户配置存在时直接读取。"""
        # 现有测试：config/worker.yaml 存在
        config = load_config()
        assert config is not None
        assert isinstance(config, WorkerConfig)

    def test_load_config_copies_template_if_user_missing(self, tmp_path):
        """用户配置不存在时从模板复制（模拟打包环境）。"""
        # 创建模拟的打包目录结构
        app_dir = tmp_path / "app"
        app_dir.mkdir()

        internal_config = app_dir / "_internal" / "config"
        internal_config.mkdir(parents=True)

        # 写入默认模板
        template_content = """
worker:
  port: 9999
external_services:
  platform_api: "http://test.example.com:8000"
  ocr_service: "http://test.example.com:9021"
"""
        (internal_config / "worker.yaml").write_text(template_content, encoding="utf-8")

        # 创建用户配置目录（空）
        user_config_dir = app_dir / "config"
        user_config_dir.mkdir()

        # 模拟打包环境
        original_frozen = getattr(sys, 'frozen', False)
        original_executable = getattr(sys, 'executable', None)

        try:
            # 模拟打包环境
            sys.frozen = True
            sys.executable = str(app_dir / "test-worker.exe")

            # 用户配置文件不存在
            user_config_path = app_dir / "config" / "worker.yaml"
            assert not user_config_path.exists()

            # 加载配置
            config = load_config()

            # 验证用户配置已创建
            assert user_config_path.exists()
            assert config.port == 9999
        finally:
            # 恢复原始状态
            if original_frozen:
                sys.frozen = original_frozen
            else:
                delattr(sys, 'frozen')
            if original_executable:
                sys.executable = original_executable


if __name__ == "__main__":
    pytest.main([__file__, "-v"])