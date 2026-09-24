# tests/test_files_config.py
"""files 配置段解析测试。"""
from worker.config import WorkerConfig


def test_files_defaults():
    cfg = WorkerConfig()
    assert cfg.files_root is None
    assert cfg.files_download_rate_limit_mb == 1.0
    assert cfg.files_upload_rate_limit_mb == 1.0
    assert cfg.files_max_concurrent_downloads == 2
    assert cfg.files_max_upload_size_mb == 1000


def test_files_from_yaml(tmp_path):
    yaml_file = tmp_path / "worker.yaml"
    yaml_file.write_text(
        """
files:
  root: D:/collected
  download_rate_limit_mb: 0
  upload_rate_limit_mb: 2.5
  max_concurrent_downloads: 4
  max_upload_size_mb: 2048
""",
        encoding="utf-8",
    )
    cfg = WorkerConfig.from_yaml(str(yaml_file))
    assert cfg.files_root == "D:/collected"
    assert cfg.files_download_rate_limit_mb == 0
    assert cfg.files_upload_rate_limit_mb == 2.5
    assert cfg.files_max_concurrent_downloads == 4
    assert cfg.files_max_upload_size_mb == 2048
