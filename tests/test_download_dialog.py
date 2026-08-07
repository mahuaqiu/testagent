"""手动升级下载路径测试。"""

from pathlib import Path

from worker.download_dialog import DownloadThread, _is_local_download_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_local_download_path_recognizes_unc_and_absolute_paths() -> None:
    """本地路径判断应覆盖 UNC、盘符绝对路径和普通 HTTP 地址。"""
    assert _is_local_download_path(r"\\server\share\worker.exe")
    assert _is_local_download_path(r"C:\packages\worker.exe")
    assert _is_local_download_path(r"/packages/worker.exe")
    assert _is_local_download_path("worker.exe")
    assert not _is_local_download_path("https://example.com/worker.exe")


def test_local_download_path_strips_input_whitespace() -> None:
    """路径前后的空白不应改变本地路径判断结果。"""
    assert _is_local_download_path(r"  \\server\share\worker.exe  ")


def test_local_download_copies_file_and_reports_progress() -> None:
    """本地文件复制应保留内容，并发送进度和完成信号。"""
    output_dir = PROJECT_ROOT / "test_output" / "download_dialog"
    source = output_dir / "source.exe"
    target = output_dir / "nested" / "installer.exe"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        source.write_bytes(b"worker-installer")

        thread = DownloadThread(str(source), str(target))
        progress = []
        finished = []
        thread.progress_signal.connect(lambda downloaded, total: progress.append((downloaded, total)))
        thread.finished_signal.connect(finished.append)

        thread._copy_local_file()

        assert target.read_bytes() == source.read_bytes()
        assert progress[-1] == (source.stat().st_size, source.stat().st_size)
        assert finished == [str(target)]
    finally:
        target.unlink(missing_ok=True)
        source.unlink(missing_ok=True)
        target.parent.rmdir()
        output_dir.rmdir()
