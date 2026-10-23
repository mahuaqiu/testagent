"""Worker 固定运行参数。"""

# 解锁功能固定参数。
DEFAULT_UNLOCK_PASSWORD = "123456"
UNLOCK_CLICK_INTERVAL_MS = 150

# iOS 按设备物理分辨率选择密码键盘点击坐标和解锁方式。
IOS_UNLOCK_METHODS = {
    "750x1334": "home_key",
    "828x1792": "home_key",
    "1170x2532": "swipe_up",
    "1179x2556": "swipe_up",
    "1290x2796": "swipe_up",
    "default": "home_key",
}

IOS_KEYPAD_COORDINATES = {
    "750x1334": {
        "1": {"x": 167, "y": 483}, "2": {"x": 375, "y": 483}, "3": {"x": 570, "y": 483},
        "4": {"x": 167, "y": 670}, "5": {"x": 375, "y": 670}, "6": {"x": 570, "y": 670},
        "7": {"x": 167, "y": 838}, "8": {"x": 375, "y": 838}, "9": {"x": 570, "y": 838},
        "0": {"x": 375, "y": 1010},
    },
    "828x1792": {
        "1": {"x": 104, "y": 760}, "2": {"x": 276, "y": 760}, "3": {"x": 448, "y": 760},
        "4": {"x": 104, "y": 880}, "5": {"x": 276, "y": 880}, "6": {"x": 448, "y": 880},
        "7": {"x": 104, "y": 1000}, "8": {"x": 276, "y": 1000}, "9": {"x": 448, "y": 1000},
        "0": {"x": 276, "y": 1120},
    },
    "1170x2532": {
        "1": {"x": 144, "y": 1080}, "2": {"x": 390, "y": 1080}, "3": {"x": 636, "y": 1080},
        "4": {"x": 144, "y": 1260}, "5": {"x": 390, "y": 1260}, "6": {"x": 636, "y": 1260},
        "7": {"x": 144, "y": 1440}, "8": {"x": 390, "y": 1440}, "9": {"x": 636, "y": 1440},
        "0": {"x": 390, "y": 1620},
    },
    "1179x2556": {
        "1": {"x": 147, "y": 1095}, "2": {"x": 393, "y": 1095}, "3": {"x": 639, "y": 1095},
        "4": {"x": 147, "y": 1275}, "5": {"x": 393, "y": 1275}, "6": {"x": 639, "y": 1275},
        "7": {"x": 147, "y": 1455}, "8": {"x": 393, "y": 1455}, "9": {"x": 639, "y": 1455},
        "0": {"x": 393, "y": 1635},
    },
    "1290x2796": {
        "1": {"x": 162, "y": 1200}, "2": {"x": 430, "y": 1200}, "3": {"x": 698, "y": 1200},
        "4": {"x": 162, "y": 1400}, "5": {"x": 430, "y": 1400}, "6": {"x": 698, "y": 1400},
        "7": {"x": 162, "y": 1600}, "8": {"x": 430, "y": 1600}, "9": {"x": 698, "y": 1600},
        "0": {"x": 430, "y": 1800},
    },
    "default": {
        "1": {"x": 92, "y": 570}, "2": {"x": 250, "y": 570}, "3": {"x": 408, "y": 570},
        "4": {"x": 92, "y": 670}, "5": {"x": 250, "y": 670}, "6": {"x": 408, "y": 670},
        "7": {"x": 92, "y": 770}, "8": {"x": 250, "y": 770}, "9": {"x": 408, "y": 770},
        "0": {"x": 250, "y": 870},
    },
}

ANDROID_KEYPAD_COORDINATES = {
    "1080x2400": {
        "1": {"x": 180, "y": 850}, "2": {"x": 540, "y": 850}, "3": {"x": 900, "y": 850},
        "4": {"x": 180, "y": 950}, "5": {"x": 540, "y": 950}, "6": {"x": 900, "y": 950},
        "7": {"x": 180, "y": 1050}, "8": {"x": 540, "y": 1050}, "9": {"x": 900, "y": 1050},
        "0": {"x": 540, "y": 1150},
    },
    "default": {
        "1": {"x": 90, "y": 850}, "2": {"x": 270, "y": 850}, "3": {"x": 450, "y": 850},
        "4": {"x": 90, "y": 950}, "5": {"x": 270, "y": 950}, "6": {"x": 450, "y": 950},
        "7": {"x": 90, "y": 1050}, "8": {"x": 270, "y": 1050}, "9": {"x": 450, "y": 1050},
        "0": {"x": 270, "y": 1150},
    },
}

HARMONY_KEYPAD_COORDINATES = {
    "1260x2720": {
        "1": {"x": 340, "y": 1475}, "2": {"x": 645, "y": 1475}, "3": {"x": 950, "y": 1475},
        "4": {"x": 340, "y": 1785}, "5": {"x": 645, "y": 1785}, "6": {"x": 950, "y": 1785},
        "7": {"x": 340, "y": 2095}, "8": {"x": 645, "y": 2095}, "9": {"x": 950, "y": 2095},
        "0": {"x": 645, "y": 2405},
    },
    "1080x2400": {
        "1": {"x": 180, "y": 850}, "2": {"x": 540, "y": 850}, "3": {"x": 900, "y": 850},
        "4": {"x": 180, "y": 950}, "5": {"x": 540, "y": 950}, "6": {"x": 900, "y": 950},
        "7": {"x": 180, "y": 1050}, "8": {"x": 540, "y": 1050}, "9": {"x": 900, "y": 1050},
        "0": {"x": 540, "y": 1150},
    },
    "default": {
        "1": {"x": 180, "y": 850}, "2": {"x": 540, "y": 850}, "3": {"x": 900, "y": 850},
        "4": {"x": 180, "y": 950}, "5": {"x": 540, "y": 950}, "6": {"x": 900, "y": 950},
        "7": {"x": 180, "y": 1050}, "8": {"x": 540, "y": 1050}, "9": {"x": 900, "y": 1050},
        "0": {"x": 540, "y": 1150},
    },
}

HARMONY_PC_PASSWORD_BOX = {"x": 1560, "y": 1300}
HARMONY_MOBILE_UNLOCK_SWIPE = (540, 2000, 540, 500)
HARMONY_PC_UNLOCK_SWIPE = (1560, 1600, 1560, 600)

# 日志运行参数；日志级别由本机设置界面单独保存。
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_OPTIONS = ("DEBUG", "INFO", "WARNING", "ERROR")
DEFAULT_LOG_FILE = None
DEFAULT_LOG_MAX_SIZE = 52428800
DEFAULT_LOG_BACKUP_COUNT = 5

# 升级检查与下载超时。
DEFAULT_UPGRADE_CHECK_TIMEOUT = 30
DEFAULT_UPGRADE_DOWNLOAD_TIMEOUT = 300

# 录屏参数。
DEFAULT_RECORDING_OUTPUT_DIR = "data/recordings"
DEFAULT_RECORDING_FPS = 10
DEFAULT_RECORDING_MAX_TIMEOUT_MS = 7200000

# 本地任务、截图及录屏的保留策略。
DEFAULT_TASK_RETENTION_HOURS = 1
DEFAULT_ARTIFACT_RETENTION_DAYS = 3
DEFAULT_CLEANUP_INTERVAL_HOURS = 6

# WebSocket 推流参数。
DEFAULT_MAX_CONNECTIONS_PER_DEVICE = 5
DEFAULT_WEBSOCKET_SEND_TIMEOUT_SECONDS = 30
DEFAULT_WEBSOCKET_IDLE_TIMEOUT_SECONDS = 900
DEFAULT_WEBSOCKET_STREAMING_FPS = 15
DEFAULT_WEBSOCKET_STREAMING_BITRATE = 4000000
DEFAULT_WEBSOCKET_STREAMING_PROFILE = 66
DEFAULT_REALTIME_INPUT_ENABLED = True
DEFAULT_HARMONY_STREAMING_FPS = 15
DEFAULT_HARMONY_STREAMING_JPEG_QUALITY = 60
DEFAULT_HARMONY_STREAMING_MAX_LONG_EDGE = 1600

# 鸿蒙官方 Bridge 固定随程序发布；仅开发环境的 Java 可执行文件允许配置。
DEFAULT_HARMONY_JAR_REL_PATH = "tools/harmony/hosScrcpy-1.0.15-beta.jar"
DEFAULT_HARMONY_BRIDGE_REL_PATH = "tools/harmony/bridge"
DEFAULT_HARMONY_BUNDLED_JAVA_REL_PATH = "tools/jre/bin/java.exe"

# 文件接口参数。
DEFAULT_FILES_ROOT = None
DEFAULT_FILES_ROOT_REL = "data/collected"
DEFAULT_FILES_DOWNLOAD_RATE_LIMIT_MB = 1.0
DEFAULT_FILES_UPLOAD_RATE_LIMIT_MB = 1.0
DEFAULT_FILES_MAX_CONCURRENT_DOWNLOADS = 2
DEFAULT_FILES_MAX_CONCURRENT_UPLOADS = 2
DEFAULT_FILES_MAX_UPLOAD_SIZE_MB = 1000
