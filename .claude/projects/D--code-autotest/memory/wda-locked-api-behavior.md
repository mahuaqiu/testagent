---
name: wda-locked-api-behavior
description: WDA /wda/locked API 的异常行为特性
type: feedback
---

WDA 的 `/wda/locked` API 在设备熄屏时返回 `False`，即使设备实际处于锁定状态。

**Why**: WDA 的 `is_locked` 检测的是"安全锁定状态"，而非屏幕是否亮着。熄屏时设备处于中间状态（未安全锁定但屏幕熄灭），API 返回 False。解锁过一次后，WDA session 状态更新，后续检测才准确。

**How to apply**: 对于 iOS 解锁流程，不要依赖 `/wda/locked` API 判断熄屏状态。应使用截图亮度检测（阈值 10）判断屏幕是否亮着。熄屏唤醒后不依赖 `is_locked`，直接继续密码输入流程。