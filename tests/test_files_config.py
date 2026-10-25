# tests/test_files_config.py
"""files 固定运行参数测试。"""
from worker.config import WorkerConfig


def test_files_defaults():
    cfg = WorkerConfig()
    assert cfg.files_root is None
    assert cfg.files_download_rate_limit_mb == 1.0
    assert cfg.files_upload_rate_limit_mb == 1.0
    assert cfg.files_max_concurrent_downloads == 2
    assert cfg.files_max_concurrent_uploads == 2
    assert cfg.files_max_upload_size_mb == 1024


def test_files_settings_in_yaml_are_ignored(tmp_path):
    yaml_file = tmp_path / "worker.yaml"
    yaml_file.write_text(
        """
worker:
  id: test-worker
files:
  root: D:/collected
  download_rate_limit_mb: 0
  upload_rate_limit_mb: 2.5
  max_concurrent_downloads: 4
  max_concurrent_uploads: 3
  max_upload_size_mb: 2048
""",
        encoding="utf-8",
    )
    cfg = WorkerConfig.from_yaml(str(yaml_file))
    assert cfg.files_root is None
    assert cfg.files_download_rate_limit_mb == 1.0
    assert cfg.files_upload_rate_limit_mb == 1.0
    assert cfg.files_max_concurrent_downloads == 2
    assert cfg.files_max_concurrent_uploads == 2
    assert cfg.files_max_upload_size_mb == 1024


def test_set_files_config_creates_missing_root(tmp_path):
    """全新安装的根目录应由 set_files_config 自动创建,平台首个请求即可用。"""
    from worker.files_api import set_files_config

    root = tmp_path / "fresh" / "collected"
    assert not root.exists()
    set_files_config(WorkerConfig(files_root=str(root)))
    assert root.is_dir()


def test_set_files_config_no_create_root(tmp_path):
    """create_root=False(懒加载兜底路径)不得触碰磁盘。"""
    from worker.files_api import set_files_config

    root = tmp_path / "lazy" / "collected"
    set_files_config(WorkerConfig(files_root=str(root)), create_root=False)
    assert not root.exists()


def test_set_files_config_clamps_invalid_values(tmp_path):
    """负数限速=不限速,并发下限 1,非法大小上限回退默认而非 413 全部上传。"""
    from worker.files_api import get_files_settings, set_files_config

    set_files_config(
        WorkerConfig(
            files_root=str(tmp_path),
            files_download_rate_limit_mb=-5,
            files_upload_rate_limit_mb=-1,
            files_max_concurrent_downloads=0,
            files_max_concurrent_uploads=-3,
            files_max_upload_size_mb=-100,
        )
    )
    settings = get_files_settings()
    assert settings.download_rate_limit_mb == 0.0
    assert settings.upload_rate_limit_mb == 0.0
    assert settings.max_concurrent_downloads == 1
    assert settings.max_concurrent_uploads == 1
    assert settings.max_upload_size_mb == 1024


def test_set_files_config_sweeps_orphan_part_files(tmp_path):
    """启动清扫:上次崩溃残留的 .part 会被清掉(含子目录),正常文件不受影响。"""
    from worker.files_api import set_files_config

    root = tmp_path / "collected"
    root.mkdir()
    (root / "orphan.abcdef.part").write_bytes(b"x")
    sub = root / "sub"
    sub.mkdir()
    (sub / "orphan2.123456.part").write_bytes(b"y")
    (root / "keep.log").write_bytes(b"ok")

    set_files_config(WorkerConfig(files_root=str(root)))

    assert list(root.rglob("*.part")) == []
    assert (root / "keep.log").read_bytes() == b"ok"
