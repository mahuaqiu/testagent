#!/bin/bash
# 启动应用程序，支持重启和窗口激活
#
# 参数：
#   -a, --app-path    应用程序路径（必填）
#   -r, --restart     强制重启（可选，默认 false）
#
# 示例：
#   ./start_app.sh -a "/Applications/MyApp.app"
#   ./start_app.sh -a "/Applications/MyApp.app" -r

set -e

# 默认参数
APP_PATH=""
RESTART=false

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -a|--app-path)
            APP_PATH="$2"
            shift 2
            ;;
        -r|--restart)
            RESTART=true
            shift
            ;;
        *)
            echo "未知参数: $1"
            exit 1
            ;;
    esac
done

# 参数校验
if [[ -z "$APP_PATH" ]]; then
    echo "错误: app_path 是必填参数"
    exit 1
fi

# 从路径提取进程名（.app 目录取目录名，去掉 .app 后缀）
if [[ "$APP_PATH" == *.app ]]; then
    PROCESS_NAME=$(basename "$APP_PATH" .app)
else
    PROCESS_NAME=$(basename "$APP_PATH")
fi

# 检查进程是否存在
EXISTING_PID=$(pgrep -x "$PROCESS_NAME" 2>/dev/null || true)

if [[ "$RESTART" == "true" ]]; then
    # 模式1: 强制重启
    if [[ -n "$EXISTING_PID" ]]; then
        echo "正在关闭进程: $PROCESS_NAME (PID: $EXISTING_PID)"
        pkill -x "$PROCESS_NAME"
        sleep 1
    fi

    echo "正在启动: $APP_PATH"
    open "$APP_PATH"
    echo "启动成功: $PROCESS_NAME"
else
    # 模式2: 不强制重启
    if [[ -n "$EXISTING_PID" ]]; then
        # 进程已存在，激活窗口
        echo "进程已存在，激活窗口: $PROCESS_NAME"

        # 使用 AppleScript 激活应用窗口
        osascript -e "tell application \"$PROCESS_NAME\" to activate" 2>/dev/null || true
        echo "窗口激活成功: $PROCESS_NAME"
    else
        # 进程不存在，启动
        echo "正在启动: $APP_PATH"
        open "$APP_PATH"
        echo "启动成功: $PROCESS_NAME"
    fi
fi