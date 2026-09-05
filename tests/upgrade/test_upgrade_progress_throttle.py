"""升级下载进度持久化节流测试。"""

from worker.upgrade import state as upgrade_state_module
from worker.upgrade.models import UpgradeState


def test_progress_persistence_is_throttled(monkeypatch, tmp_path):
    """进度回调按百分比节流落盘，下载完成时必须落盘。"""
    writes = []
    monkeypatch.setattr(upgrade_state_module, "save_state", lambda s: writes.append(s.download_progress))
    monkeypatch.setattr(upgrade_state_module, "get_state_file_path", lambda: str(tmp_path / "upgrade.json"))

    manager = upgrade_state_module.UpgradeStatusManager()
    manager.clear()
    try:
        state = UpgradeState(
            status="downloading",
            target_version="1",
            current_version="0",
            download_url="http://example/pkg.exe",
            started_at="now",
        )
        manager.set_state(state)
        writes.clear()

        # 同一百分比内多次回调只落盘一次；百分比变化才落盘
        manager.update_download_progress(1, 1000)     # 0%  → 落盘
        manager.update_download_progress(9, 1000)     # 0%  → 跳过
        manager.update_download_progress(10, 1000)    # 1%  → 落盘
        manager.update_download_progress(19, 1000)    # 1%  → 跳过
        manager.update_download_progress(50, 1000)    # 5%  → 落盘
        manager.update_download_progress(510, 1000)   # 51% → 落盘
        manager.update_download_progress(999, 1000)   # 99% → 落盘
        manager.update_download_progress(1000, 1000)  # 100%（完成必落盘）

        assert writes == [0, 1, 5, 51, 99, 100]
    finally:
        manager.clear()
