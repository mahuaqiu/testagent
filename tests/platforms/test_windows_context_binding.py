"""Windows 上下文窗口绑定与脚本名校验修复测试。"""

from unittest.mock import patch

from worker.config import PlatformConfig
from worker.platforms.windows import WindowsContext, WindowsPlatformManager
from worker.tools import validate_script_name


def test_create_context_returns_per_context_binding():
    """窗口绑定随上下文返回，不再写入共享字段。"""
    manager = WindowsPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.win_utils.find_window_handle", return_value=0x1234),
        patch("worker.platforms.win_utils.get_window_rect", return_value=(10, 20, 110, 220)),
    ):
        context = manager.create_context(options={"window": {"title": "Notepad"}})

    assert isinstance(context, WindowsContext)
    assert context.window_handle == 0x1234
    assert context.window_rect == (10, 20, 110, 220)
    # 共享字段不再被 create_context 触碰
    assert manager._window_handle is None
    assert manager._window_rect is None


def test_new_context_does_not_clobber_existing_binding():
    """并发场景：新建/关闭上下文不得清掉已有上下文的窗口绑定。"""
    manager = WindowsPlatformManager(PlatformConfig())
    bound = WindowsContext(window_handle=0xAAAA, window_rect=(1, 2, 3, 4))

    fresh = manager.create_context(options=None)
    manager.close_context(fresh)

    # 已绑定上下文不受影响
    handle, rect = manager._resolve_window_binding(bound)
    assert handle == 0xAAAA
    assert rect == (1, 2, 3, 4)


def test_convert_coords_honors_context_binding():
    manager = WindowsPlatformManager(PlatformConfig())
    context = WindowsContext(window_handle=0x1234, window_rect=(100, 200, 300, 400))
    assert manager._convert_to_global_coords(10, 20, context) == (110, 220)


def test_convert_coords_falls_back_to_shared_fields_without_context():
    """旧调用方式（不传 context）回退共享字段，保持兼容。"""
    manager = WindowsPlatformManager(PlatformConfig())
    manager._window_handle = 123
    manager._window_rect = (120, 230, 320, 430)
    assert manager._convert_to_global_coords(0, 0) == (120, 230)


def test_validate_script_name_rejects_drive_relative_and_reserved():
    assert validate_script_name("ok.ps1")
    assert validate_script_name("my-script.bat")
    assert validate_script_name("run.sh")
    # 盘符相对路径
    assert not validate_script_name("C:evil.ps1")
    assert not validate_script_name("D:tools.bat")
    # 路径穿越
    assert not validate_script_name("..\\evil.ps1")
    assert not validate_script_name("a/b.sh")
    # Windows 保留设备名
    assert not validate_script_name("CON.ps1")
    assert not validate_script_name("NUL.bat")
    # 非法字符
    assert not validate_script_name('a"b.ps1')
    assert not validate_script_name("a|b.sh")
    # 空名称
    assert not validate_script_name("")
