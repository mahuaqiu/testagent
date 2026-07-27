# -*- coding: utf-8 -*-
"""鸿蒙平台 action 真机验收脚本。

背景：鸿蒙各 action 是在没有真机的情况下按 uitest/aa/bm 文档实现的，
本脚本在真机上逐项验证命令模板与结果判定是否正确。命令级成功只代表
uitest/aa/bm 接受了命令，交互效果（长按弹菜单、双击缩放等）需要肉眼
确认，加 --interactive 后脚本会逐项询问人工确认结果。

覆盖项：
1.  设备发现与基础信息（形态/分辨率）
2.  屏幕状态读取（screen_state / lock_state）
3.  wakeup 唤醒（power-shell wakeup 幂等性）
4.  POWER 键熄屏 + wakeup 恢复（顺带验证 screen_state 语义）
5.  tap / doubleClick / longClick（uitest 原生命令模板）
6.  swipe（速度换算边界 200/40000）
7.  inputText（坐标输入 + 特殊字符转义，需人工确认落字）
8.  keyEvent 安全键逐个下发（BACK/HOME/音量/DPAD/ENTER/MENU）
9.  screenshot（snapshot_display）
10. 应用查询（list_apps / has_app / current_app 交叉验证）
11. 应用生命周期（start_app → current_app → stop_app，需 --package）
12. clear_app（破坏性，需 --package 且 --allow-destructive）
13. install / uninstall（破坏性，需 --hap 且 --allow-destructive）
14. unlock_screen 链路（需 --password，锁屏状态下才有意义）

用法（在 autotest 仓库根目录）：
    python scripts/harmony_action_acceptance.py                       # 只跑非破坏项
    python scripts/harmony_action_acceptance.py --interactive         # 逐项人工确认
    python scripts/harmony_action_acceptance.py --package com.xxx.app # 附带应用生命周期
    python scripts/harmony_action_acceptance.py --hap x.hap --package com.xxx.app --allow-destructive
"""

import argparse
import os
import sys
import tempfile
import time
import uuid

# 允许从仓库根目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker.platforms.harmony_hdc import (  # noqa: E402
    HarmonyHdcWrapper,
    list_target_info,
)

JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"

# 不影响系统状态或影响可自动恢复的按键；POWER 单独测（第 4 项）
SAFE_KEYS = (
    "BACK", "HOME", "VOLUME_UP", "VOLUME_DOWN", "VOLUME_MUTE",
    "ENTER", "MENU", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
    "DPAD_CENTER",
)

RESULTS: list[tuple[str, str, str]] = []  # (状态, 检查项, 说明)
INTERACTIVE = False


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f" —— {detail}" if detail else ""))


def check(name: str):
    """装饰器：统一捕获异常记为 FAIL，返回值 (True, detail) 记为 PASS。"""

    def wrapper(func):
        def inner(*args, **kwargs):
            try:
                ok, detail = func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 验收脚本逐项容错
                record("FAIL", name, f"异常: {exc}")
                return False
            record("PASS" if ok else "FAIL", name, detail)
            return ok

        return inner

    return wrapper


def confirm(prompt: str) -> tuple[bool, str]:
    """交互模式下人工确认效果；非交互模式视为通过（仅命令级验证）。"""
    if not INTERACTIVE:
        return True, "命令级通过（未人工确认效果，加 --interactive 逐项确认）"
    answer = input(f"    >> {prompt} [y/n] ").strip().lower()
    if answer == "y":
        return True, "人工确认通过"
    return False, f"人工确认失败（输入: {answer or '空'}）"


# ============================================================================
# 检查项
# ============================================================================


@check("1. 设备发现与基础信息")
def check_basic(hdc: HarmonyHdcWrapper):
    category = hdc.device_category()
    width, height = hdc.display_size()
    if category not in ("mobile", "pc"):
        return False, f"device_category={category}（预期 mobile/pc）"
    if (width, height) == (0, 0):
        return False, "display_size 解析失败"
    return True, f"category={category}, display={width}x{height}"


@check("2. 屏幕状态读取")
def check_screen_state(hdc: HarmonyHdcWrapper):
    state = hdc.screen_state()
    locked = hdc.lock_state()
    if state == "unknown":
        return False, "screen_state 返回 unknown（hidumper 输出格式与预期不符）"
    return True, f"screen_state={state}, lock_state={locked}"


@check("3. wakeup 唤醒（幂等）")
def check_wakeup(hdc: HarmonyHdcWrapper):
    # 亮屏状态下连续调用两次，验证 power-shell wakeup 不会反向熄屏
    if not hdc.wakeup():
        return False, "第一次 wakeup 失败"
    time.sleep(0.5)
    if not hdc.wakeup():
        return False, "第二次 wakeup 失败"
    time.sleep(0.5)
    if not hdc.is_screen_on():
        return False, "两次 wakeup 后屏幕不是亮屏状态（幂等性被打破）"
    return True, "连续两次 wakeup 后仍亮屏"


@check("4. POWER 熄屏 + wakeup 恢复")
def check_power_cycle(hdc: HarmonyHdcWrapper):
    # 亮屏时按 POWER 应熄屏 → 验证 screen_state 语义；再 wakeup 恢复
    if not hdc.is_screen_on():
        hdc.wakeup()
        time.sleep(1)
    if not hdc.press_key("POWER"):
        return False, "POWER 键下发失败"
    time.sleep(1.5)
    off_state = hdc.screen_state()
    hdc.wakeup()
    time.sleep(1.5)
    on_after = hdc.is_screen_on()
    if off_state not in ("off", "doze", "dim", "suspend"):
        return False, f"POWER 后 screen_state={off_state}（未识别为熄屏，判定逻辑或键码有误）"
    if not on_after:
        return False, "wakeup 后未恢复亮屏"
    return True, f"POWER 后 state={off_state}，wakeup 恢复亮屏"


@check("5a. tap 点击")
def check_tap(hdc: HarmonyHdcWrapper, cx: int, cy: int):
    if not hdc.tap(cx, cy):
        return False, "uitest uiInput click 命令失败"
    return confirm(f"屏幕中心 ({cx},{cy}) 是否出现了点击响应？")


@check("5b. doubleClick 双击")
def check_double_tap(hdc: HarmonyHdcWrapper, cx: int, cy: int):
    if not hdc.double_tap(cx, cy):
        return False, "uitest uiInput doubleClick 命令失败"
    return confirm("是否出现了双击响应（如图库缩放/文本选词）？")


@check("5c. longClick 长按")
def check_long_tap(hdc: HarmonyHdcWrapper, cx: int, cy: int):
    if not hdc.long_tap(cx, cy):
        return False, "uitest uiInput longClick 命令失败"
    return confirm("是否出现了长按响应（如桌面图标菜单/文本选择柄）？重点确认不是普通点击！")


@check("6. swipe 滑动（含速度边界）")
def check_swipe(hdc: HarmonyHdcWrapper, width: int, height: int):
    cx = width // 2
    y1, y2 = int(height * 0.7), int(height * 0.3)
    # 正常速度上滑再下滑还原
    if not hdc.swipe(cx, y1, cx, y2, speed=2000):
        return False, "speed=2000 上滑失败"
    time.sleep(0.8)
    if not hdc.swipe(cx, y2, cx, y1, speed=2000):
        return False, "speed=2000 下滑失败"
    time.sleep(0.8)
    # 速度边界值（200 慢滑 / 40000 极速），验证 uitest 不拒绝夹紧后的边界
    if not hdc.swipe(cx, y1, cx, int(height * 0.55), speed=200):
        return False, "speed=200（下边界）被拒绝"
    time.sleep(1.5)
    if not hdc.swipe(cx, int(height * 0.55), cx, y1, speed=40000):
        return False, "speed=40000（上边界）被拒绝"
    return confirm("四次滑动是否都真实发生（尤其 speed=200 是否为可见慢滑）？")


@check("7. inputText 文本输入")
def check_input_text(hdc: HarmonyHdcWrapper, cx: int, cy: int):
    if INTERACTIVE:
        input("    >> 请先手动打开任一输入框（如备忘录/搜索框）并回车继续...")
        text = "Abc123 中文'\"$test"
        if not hdc.input_text_at(cx, cy, text):
            return False, "uitest uiInput inputText 命令失败"
        return confirm(f"输入框是否完整出现文本：{text!r}（重点核对引号/$/中文/空格）？")
    # 非交互只验证命令模板不被 uitest 拒绝（无输入框时文本无处落地属预期）
    if not hdc.input_text_at(cx, cy, "test'\"$1"):
        return False, "uitest uiInput inputText 命令失败"
    return True, "命令级通过（转义未报错；落字效果需 --interactive 人工核对）"


@check("8. keyEvent 安全键逐个下发")
def check_keys(hdc: HarmonyHdcWrapper):
    failed = []
    for key in SAFE_KEYS:
        if not hdc.press_key(key):
            failed.append(key)
        time.sleep(0.3)
    if failed:
        return False, f"下发失败: {failed}"
    # ENTER=2054/MENU=2067 等键码值需人工观察效果是否与语义一致
    return confirm(
        f"已依次下发 {len(SAFE_KEYS)} 个按键（{', '.join(SAFE_KEYS)}），"
        "音量条是否弹出、BACK/HOME 是否生效？"
    )


@check("9. screenshot 截图")
def check_screenshot(hdc: HarmonyHdcWrapper):
    local_path = os.path.join(
        tempfile.gettempdir(), f"harmony_action_{uuid.uuid4().hex}.jpeg"
    )
    try:
        if not hdc.screenshot(local_path):
            return False, "hdc.screenshot 返回 False"
        with open(local_path, "rb") as f:
            data = f.read()
    finally:
        if os.path.isfile(local_path):
            os.remove(local_path)
    if not (data[:2] == JPEG_START and data[-2:] == JPEG_END):
        return False, f"截图非合法 JPEG（{len(data)} 字节）"
    return True, f"JPEG {len(data)} 字节"


@check("10. 应用查询交叉验证")
def check_app_query(hdc: HarmonyHdcWrapper):
    apps = hdc.list_apps(include_system=True)
    if not apps:
        return False, "list_apps 为空（bm dump -a 解析正则可能不匹配真机输出）"
    sample = apps[0]
    if not hdc.has_app(sample):
        return False, f"has_app({sample}) 为 False，与 list_apps 结果矛盾"
    if hdc.has_app("com.nonexistent.pkg.zzz"):
        return False, "has_app 对不存在的包返回 True（判定过宽）"
    pkg, ability = hdc.current_app()
    detail = f"共 {len(apps)} 个包, has_app 交叉一致, 前台={pkg}/{ability}"
    if pkg is None:
        return False, f"current_app 未解析出前台应用（aa dump -l 格式与预期不符）；{detail}"
    return True, detail


@check("11. 应用生命周期 start→current→stop")
def check_app_lifecycle(hdc: HarmonyHdcWrapper, package: str, ability: str):
    if not hdc.start_app(package, ability):
        return False, f"start_app {package}/{ability} 失败（确认 ability 名，默认 EntryAbility 不一定通用）"
    time.sleep(3)
    fg_pkg, fg_ability = hdc.current_app()
    if fg_pkg != package:
        return False, f"启动后前台是 {fg_pkg}/{fg_ability}，不是 {package}"
    if not hdc.stop_app(package):
        return False, "stop_app 失败"
    time.sleep(2)
    fg_pkg2, _ = hdc.current_app()
    if fg_pkg2 == package:
        return False, "force-stop 后应用仍在前台"
    return True, f"启动→前台确认（ability={fg_ability}）→停止，全链路通过"


@check("12. clear_app 清数据")
def check_clear_app(hdc: HarmonyHdcWrapper, package: str):
    if not hdc.clear_app(package):
        return False, "bm clean -n 失败"
    return confirm(f"{package} 的数据是否已被清空（重新打开应为首启状态）？")


@check("13. install / uninstall")
def check_install(hdc: HarmonyHdcWrapper, hap_path: str, package: str):
    if not hdc.install(hap_path):
        return False, "hdc install 失败"
    if not hdc.has_app(package):
        return False, "安装后 has_app 为 False"
    if not hdc.uninstall(package):
        return False, "hdc uninstall 失败"
    if hdc.has_app(package):
        return False, "卸载后 has_app 仍为 True"
    return True, "安装→has_app→卸载→has_app 闭环通过"


@check("14. unlock_screen 解锁链路")
def check_unlock(hdc: HarmonyHdcWrapper, password: str):
    # 直接走 action 执行链路，验证 unlock.py 的滑动/键盘坐标是否适配真机
    from worker.config import PlatformConfig  # noqa: PLC0415
    from worker.platforms.harmony import HarmonyPlatformManager  # noqa: PLC0415
    from worker.task import Action  # noqa: PLC0415

    if not hdc.is_locked():
        # 先熄屏并等待自动落锁；未配置锁屏密码的设备此项无意义
        hdc.press_key("POWER")
        time.sleep(3)
        if not hdc.is_locked():
            return False, "无法进入锁屏状态（设备可能未设置锁屏），跳过意义不大"

    category = hdc.device_category()
    manager = HarmonyPlatformManager(
        PlatformConfig(),
        device_type="harmony_pc" if category == "pc" else "harmony_mobile",
    )
    manager._device_clients[hdc.serial] = hdc
    manager._current_device = hdc.serial
    action = Action(action_type="unlock_screen", value=password)
    result = manager.execute_action(hdc, action)
    if result.status.value != "success":
        return False, f"unlock_screen 失败: {result.error}（九宫格/密码框坐标可能需按真机分辨率覆写）"
    time.sleep(1)
    if hdc.is_locked():
        return False, "unlock_screen 返回成功但设备仍处于锁屏"
    return True, "解锁成功且 lock_state 确认已解锁"


# ============================================================================
# 主流程
# ============================================================================


def main() -> int:
    global INTERACTIVE

    parser = argparse.ArgumentParser(description="鸿蒙平台 action 真机验收")
    parser.add_argument("--udid", help="设备序列号（缺省取第一台在线设备）")
    parser.add_argument("--hdc-path", help="hdc 工具路径（缺省自动查找）")
    parser.add_argument("--interactive", action="store_true",
                        help="逐项人工确认交互效果（推荐首次验收时开启）")
    parser.add_argument("--package", help="用于应用生命周期测试的包名")
    parser.add_argument("--ability", default="EntryAbility",
                        help="启动 ability 名（默认 EntryAbility）")
    parser.add_argument("--hap", help="用于 install/uninstall 测试的 HAP 路径")
    parser.add_argument("--password", help="锁屏密码，提供则测 unlock_screen 链路")
    parser.add_argument("--allow-destructive", action="store_true",
                        help="允许破坏性操作（clear_app / install / uninstall）")
    args = parser.parse_args()
    INTERACTIVE = args.interactive

    targets = list_target_info(args.hdc_path)
    if not targets:
        print("未发现在线设备，终止验收。")
        return 1
    udid = args.udid or targets[0].udid
    if udid not in [t.udid for t in targets]:
        print(f"指定设备 {udid} 不在线，当前: {[t.udid for t in targets]}")
        return 1

    hdc = HarmonyHdcWrapper(udid, args.hdc_path)
    print(f"验收设备: {udid}（interactive={INTERACTIVE}）\n")

    check_basic(hdc)
    check_screen_state(hdc)
    check_wakeup(hdc)
    check_power_cycle(hdc)

    width, height = hdc.display_size()
    cx, cy = (width // 2, height // 2) if width else (500, 500)

    check_tap(hdc, cx, cy)
    time.sleep(0.5)
    check_double_tap(hdc, cx, cy)
    time.sleep(0.5)
    check_long_tap(hdc, cx, cy)
    time.sleep(0.5)
    # 长按可能弹出菜单，BACK 收掉再继续
    hdc.press_key("BACK")
    time.sleep(0.5)
    check_swipe(hdc, width or 1080, height or 2400)
    check_input_text(hdc, cx, cy)
    check_keys(hdc)
    check_screenshot(hdc)
    check_app_query(hdc)

    if args.package:
        check_app_lifecycle(hdc, args.package, args.ability)
        if args.allow_destructive:
            check_clear_app(hdc, args.package)
        else:
            record("SKIP", "12. clear_app 清数据", "未加 --allow-destructive")
    else:
        record("SKIP", "11. 应用生命周期 start→current→stop", "未提供 --package")
        record("SKIP", "12. clear_app 清数据", "未提供 --package")

    if args.hap and args.package and args.allow_destructive:
        check_install(hdc, args.hap, args.package)
    else:
        record("SKIP", "13. install / uninstall",
               "需同时提供 --hap --package --allow-destructive")

    if args.password:
        check_unlock(hdc, args.password)
    else:
        record("SKIP", "14. unlock_screen 解锁链路", "未提供 --password")

    # 汇总
    print("\n" + "=" * 70)
    passed = sum(1 for s, *_ in RESULTS if s == "PASS")
    failed = sum(1 for s, *_ in RESULTS if s == "FAIL")
    skipped = sum(1 for s, *_ in RESULTS if s == "SKIP")
    for status, name, detail in RESULTS:
        print(f"  [{status}] {name}" + (f" —— {detail}" if detail else ""))
    print(f"\n结果: {passed} 通过, {failed} 失败, {skipped} 跳过")
    if not INTERACTIVE:
        print("提示: 本次为命令级验证；交互效果（长按/双击/落字）请加 --interactive 复跑确认。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
