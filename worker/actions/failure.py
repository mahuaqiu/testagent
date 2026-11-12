"""find 类动作失败文案的统一构造。

ocr_*/image_* 动作失败时的 error/error_type 全部经过这里，
保证 worker 日志、ActionResult、测试报告三处文案同源。
error_type 词汇表（8 词，报告端按词配色，勿新增）：
文件缺失 / OCR服务错误 / OCR请求失败 / 文本未找到 / 相似度不足 / 颜色校验未通过 / 距离超限 / 其他
"""

from __future__ import annotations


def _index_suffix(index: int) -> str:
    return f" at index {index}" if index > 0 else ""


def format_find_failure(
    failure: dict | None,
    target: str,
    threshold: float | None = None,
    index: int = 0,
    kind: str = "text",
) -> tuple[str, str]:
    """把一次 find 失败的诊断信息转成 (可读消息, 失败类型)。

    Args:
        failure: OCRClient.get_last_failure() 的返回；None 表示拿不到诊断（旧服务/本地失败）。
        target: 目标描述（文本内容或模板图片路径）。
        threshold: 动作传入的匹配阈值（文本类无阈值，传 None）。
        index: 目标序号（>0 时追加到消息末尾）。
        kind: "text" 文本查找 / "image" 图像匹配，决定兜底文案。

    Returns:
        (error_message, error_type)
    """
    suffix = _index_suffix(index)
    # worker 侧图像动作拿不到模板文件名（只有 base64），target 允许为空串
    img_name = f'"{target}"' if target else ""
    text_name = f' "{target}"' if target else ""

    # 拿不到诊断：按目标类型给兜底文案
    if not failure:
        if kind == "image":
            return (f"未找到目标图片{img_name}{suffix}", "其他")
        return (f"未找到文本{text_name}{suffix}", "文本未找到")

    # 传输层 / 服务层错误
    if failure.get("status") == "error":
        if failure.get("http_status") is not None:
            return (
                f"OCR 请求失败: HTTP {failure['http_status']} - {failure.get('error')}{suffix}",
                "OCR请求失败",
            )
        return (f"OCR 服务错误: {failure.get('error')}{suffix}", "OCR服务错误")

    reason = failure.get("not_found_reason")
    best = failure.get("best_confidence")

    if reason == "color_check_failed":
        if best is not None:
            return (
                f"未找到目标图片{img_name}：灰度相似度 {best:.2f} 达标，但颜色校验未通过{suffix}",
                "颜色校验未通过",
            )
        return (f"未找到目标图片{img_name}：颜色校验未通过{suffix}", "颜色校验未通过")

    if reason == "distance_exceeded":
        distance = failure.get("nearest_distance")
        return (
            f"未找到{img_name}附近的图片：最近候选距离 {distance:.0f}px，超过上限{suffix}",
            "距离超限",
        )

    if reason == "no_text_recognized":
        return (f"屏幕上未识别到任何文字，未找到{text_name}{suffix}", "文本未找到")

    if reason == "text_not_found":
        return (f"未找到文本{text_name}{suffix}", "文本未找到")

    if reason == "template_not_matched":
        # 模板比源图大 / 文本附近模板未命中
        if kind == "image":
            return (f"未找到目标图片{img_name}（模板与屏幕内容不匹配）{suffix}", "其他")
        return (f"未找到目标图片{text_name}{suffix}", "相似度不足")

    # 未命中但无细分原因（旧版服务）：有数值时报"差多少"
    if kind == "image":
        if best is not None and threshold is not None:
            return (
                f"未找到目标图片{img_name}：最高相似度 {best:.2f}，低于阈值 {threshold:.2f}{suffix}",
                "相似度不足",
            )
        return (f"未找到目标图片{img_name}{suffix}", "相似度不足")

    return (f"未找到文本{text_name}{suffix}", "文本未找到")
