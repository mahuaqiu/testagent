"""升级下载器基础行为测试（本地路径复制分支 + 大小校验）。"""

import os

import pytest

from worker.upgrade.downloader import DownloadError, download_installer


def test_download_installer_copies_local_file(tmp_path, monkeypatch):
    """本地路径分支必须能工作（曾因 get_temp_dir 丢失在运行时 NameError）。"""
    import worker.upgrade.downloader as downloader

    monkeypatch.setattr(downloader, "get_base_dir", lambda: str(tmp_path))
    source = tmp_path / "pkg.exe"
    source.write_bytes(b"MZ fake installer payload")

    result = download_installer(str(source))

    assert os.path.isfile(result)
    with open(result, "rb") as f:
        assert f.read() == b"MZ fake installer payload"
    assert os.path.basename(result) == "installer.exe"


def test_download_installer_size_mismatch_rejected(tmp_path, monkeypatch):
    import worker.upgrade.downloader as downloader

    monkeypatch.setattr(downloader, "get_base_dir", lambda: str(tmp_path))
    source = tmp_path / "pkg.exe"
    source.write_bytes(b"12345")

    with pytest.raises(DownloadError):
        download_installer(str(source), expected_size=999)
