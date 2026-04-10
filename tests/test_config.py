"""配置模块测试。"""


def test_worker_config_upgrade_fields():
    """测试升级配置字段。"""
    from worker.config import WorkerConfig

    config = WorkerConfig()
    assert hasattr(config, 'upgrade_check_url')
    assert config.upgrade_check_url == ""
    assert config.upgrade_check_timeout == 30
    assert config.upgrade_download_timeout == 300


def test_worker_config_upgrade_from_yaml(tmp_path):
    """测试从 YAML 加载升级配置。"""
    import yaml
    from worker.config import WorkerConfig

    config_file = tmp_path / "worker.yaml"
    config_data = {
        "upgrade": {
            "check_url": "http://example.com/upgrade",
            "check_timeout": 60,
            "download_timeout": 600,
        }
    }
    with open(config_file, "w") as f:
        yaml.dump(config_data, f)

    config = WorkerConfig.from_yaml(str(config_file))
    assert config.upgrade_check_url == "http://example.com/upgrade"
    assert config.upgrade_check_timeout == 60
    assert config.upgrade_download_timeout == 600