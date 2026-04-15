"""
配置更新功能测试。
"""

import os
import tempfile
import shutil
import yaml
from unittest.mock import patch, MagicMock

import pytest


class TestConfigVersion:
    """版本存储测试。"""

    def test_load_config_version_not_exists(self):
        """测试版本文件不存在时返回 None。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            version_path = os.path.join(tmpdir, ".config_version")

            with patch('worker.config.get_config_version_path', return_value=version_path):
                from worker.config import load_config_version
                result = load_config_version()
                assert result is None

    def test_load_config_version_exists(self):
        """测试版本文件存在时正确读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            version_path = os.path.join(tmpdir, ".config_version")
            version = "20260415-143000"

            # 创建版本文件
            os.makedirs(os.path.dirname(version_path), exist_ok=True)
            with open(version_path, "w", encoding="utf-8") as f:
                f.write(version)

            with patch('worker.config.get_config_version_path', return_value=version_path):
                from worker.config import load_config_version
                result = load_config_version()
                assert result == version

    def test_load_config_version_with_whitespace(self):
        """测试版本文件包含空白字符时正确处理。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            version_path = os.path.join(tmpdir, ".config_version")
            version = "20260415-150000"

            # 创建版本文件（带换行）
            os.makedirs(os.path.dirname(version_path), exist_ok=True)
            with open(version_path, "w", encoding="utf-8") as f:
                f.write(version + "\n")

            with patch('worker.config.get_config_version_path', return_value=version_path):
                from worker.config import load_config_version
                result = load_config_version()
                assert result == version

    def test_save_config_version(self):
        """测试版本保存。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            version_path = os.path.join(tmpdir, ".config_version")
            version = "20260415-150000"

            with patch('worker.config.get_config_version_path', return_value=version_path):
                from worker.config import save_config_version
                save_config_version(version)

                # 验证文件内容
                with open(version_path, encoding="utf-8") as f:
                    result = f.read().strip()
                assert result == version


class TestConfigMerge:
    """配置合并测试。"""

    def test_merge_config_preserves_local_ip(self):
        """测试合并保留本地 IP。"""
        existing_yaml = """
worker:
  ip: "192.168.1.100"
  port: 8088
"""

        new_yaml = """
worker:
  ip: null
  port: 8090
  namespace: new_ns
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            existing_path = os.path.join(tmpdir, "worker.yaml")
            with open(existing_path, "w", encoding="utf-8") as f:
                f.write(existing_yaml)

            from worker.config import merge_config_with_ip_protection
            result = merge_config_with_ip_protection(new_yaml, existing_path)

            # 验证 IP 保留
            assert result["worker"]["ip"] == "192.168.1.100"
            assert result["worker"]["port"] == 8090
            assert result["worker"]["namespace"] == "new_ns"

    def test_merge_config_no_existing_ip(self):
        """测试现有配置无 IP 时不影响新配置。"""
        existing_yaml = """
worker:
  port: 8088
"""

        new_yaml = """
worker:
  ip: "192.168.2.1"
  port: 8090
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            existing_path = os.path.join(tmpdir, "worker.yaml")
            with open(existing_path, "w", encoding="utf-8") as f:
                f.write(existing_yaml)

            from worker.config import merge_config_with_ip_protection
            result = merge_config_with_ip_protection(new_yaml, existing_path)

            # 无本地 IP，使用新配置的 IP
            assert result["worker"]["ip"] == "192.168.2.1"

    def test_merge_config_no_existing_file(self):
        """测试现有配置文件不存在时使用新配置。"""
        new_yaml = """
worker:
  ip: "192.168.3.1"
  port: 8090
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            existing_path = os.path.join(tmpdir, "nonexistent.yaml")

            from worker.config import merge_config_with_ip_protection
            result = merge_config_with_ip_protection(new_yaml, existing_path)

            # 无现有配置文件，使用新配置
            assert result["worker"]["ip"] == "192.168.3.1"
            assert result["worker"]["port"] == 8090

    def test_merge_config_preserves_existing_ip_when_new_is_different(self):
        """测试新配置有不同 IP 时保留本地 IP。"""
        existing_yaml = """
worker:
  ip: "192.168.1.100"
  port: 8088
"""

        new_yaml = """
worker:
  ip: "10.0.0.1"
  port: 8090
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            existing_path = os.path.join(tmpdir, "worker.yaml")
            with open(existing_path, "w", encoding="utf-8") as f:
                f.write(existing_yaml)

            from worker.config import merge_config_with_ip_protection
            result = merge_config_with_ip_protection(new_yaml, existing_path)

            # 本地 IP 被保留，不使用新配置的 IP
            assert result["worker"]["ip"] == "192.168.1.100"


class TestConfigSaveWithVersion:
    """配置保存测试（带事务保护）。"""

    def test_save_config_success(self):
        """测试配置成功保存。"""
        config_data = {"worker": {"port": 8090}}
        version = "20260415-143000"

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "worker.yaml")
            version_path = os.path.join(tmpdir, ".config_version")

            from worker.config import save_config_with_version
            save_config_with_version(config_data, version, config_path, version_path)

            # 验证配置文件
            with open(config_path, encoding="utf-8") as f:
                saved_config = yaml.safe_load(f)
            assert saved_config["worker"]["port"] == 8090

            # 验证版本文件
            with open(version_path, encoding="utf-8") as f:
                saved_version = f.read().strip()
            assert saved_version == version

    def test_save_config_creates_directories(self):
        """测试配置保存时自动创建目录。"""
        config_data = {"worker": {"port": 8090}}
        version = "20260415-143000"

        with tempfile.TemporaryDirectory() as tmpdir:
            # 使用不存在的子目录
            config_path = os.path.join(tmpdir, "subdir", "worker.yaml")
            version_path = os.path.join(tmpdir, "subdir", ".config_version")

            from worker.config import save_config_with_version
            save_config_with_version(config_data, version, config_path, version_path)

            # 验证目录已创建
            assert os.path.exists(config_path)
            assert os.path.exists(version_path)

    def test_save_config_rollback_on_failure(self):
        """测试配置保存失败时回滚。"""
        initial_config = {"worker": {"port": 8088}}
        new_config = {"worker": {"port": 8090}}
        version = "20260415-143000"

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "worker.yaml")
            version_path = os.path.join(tmpdir, ".config_version")

            # 创建初始配置
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(initial_config, f)

            # 模拟版本文件写入失败
            original_write = open

            def mock_open(path, *args, **kwargs):
                if path == version_path and args and args[0] == "w":
                    raise OSError("Mock write failure")
                return original_write(path, *args, **kwargs)

            with patch('builtins.open', side_effect=mock_open):
                from worker.config import save_config_with_version
                with pytest.raises(OSError):
                    save_config_with_version(new_config, version, config_path, version_path)

            # 验证配置已回滚
            with open(config_path, encoding="utf-8") as f:
                rolled_back_config = yaml.safe_load(f)
            assert rolled_back_config["worker"]["port"] == 8088

    def test_save_config_backup_removed_on_success(self):
        """测试成功保存后备份文件被删除。"""
        config_data = {"worker": {"port": 8090}}
        version = "20260415-143000"

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "worker.yaml")
            version_path = os.path.join(tmpdir, ".config_version")

            # 创建初始配置
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump({"worker": {"port": 8080}}, f)

            from worker.config import save_config_with_version
            save_config_with_version(config_data, version, config_path, version_path)

            # 验证备份文件已删除
            backup_path = config_path + ".bak"
            assert not os.path.exists(backup_path)

    def test_save_config_no_backup_when_no_existing(self):
        """测试无现有配置时正常保存。"""
        config_data = {"worker": {"port": 8090}}
        version = "20260415-143000"

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "worker.yaml")
            version_path = os.path.join(tmpdir, ".config_version")

            from worker.config import save_config_with_version
            save_config_with_version(config_data, version, config_path, version_path)

            # 验证配置文件
            with open(config_path, encoding="utf-8") as f:
                saved_config = yaml.safe_load(f)
            assert saved_config["worker"]["port"] == 8090

            # 不应该有备份文件
            backup_path = config_path + ".bak"
            assert not os.path.exists(backup_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])