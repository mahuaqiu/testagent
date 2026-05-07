"""
设置窗口模块。

PyQt5 实现的配置设置对话框，采用简洁现代的设计风格。
"""

import os
import re
import yaml
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QCheckBox,
    QGridLayout,
    QFrame,
    QWidget,
    QMessageBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon

import logging

logger = logging.getLogger(__name__)


class SettingsWindow(QDialog):
    """设置窗口。"""

    def __init__(self, icon_path: str = None, parent=None):
        super().__init__(parent)
        # 内部自动获取用户配置路径
        from worker.config import get_user_config_path
        self.config_path = get_user_config_path()
        self._config = self._load_config()

        # 设置窗口图标
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self._setup_ui()
        self._apply_styles()
        self._load_values()

    def _load_config(self) -> dict:
        """加载配置文件。

        优先从根目录 config/worker.yaml 读取，
        若不存在则从 _internal/config/worker.yaml 复制一份。
        """
        from worker.config import get_user_config_path, get_default_template_path

        config_path = get_user_config_path()

        # 用户配置不存在，从默认模板复制
        if not os.path.exists(config_path):
            default_template = get_default_template_path()
            if os.path.exists(default_template):
                self._copy_default_config(default_template, config_path)
                logger.info(f"Default config copied to: {config_path}")

        if not os.path.exists(self.config_path):
            return {}

        # 尝试多种编码
        encodings = ["utf-8", "gbk", "gb18030"]
        data = None
        last_error = None

        for encoding in encodings:
            try:
                with open(self.config_path, "r", encoding=encoding) as f:
                    data = yaml.safe_load(f) or {}
                logger.info(f"Config loaded successfully with {encoding} encoding")
                return data
            except UnicodeDecodeError as e:
                last_error = f"编码错误 ({encoding}): {e}"
                logger.warning(f"Failed to load config with {encoding}: {e}")
                continue
            except yaml.YAMLError as e:
                last_error = f"YAML 格式错误: {e}"
                logger.error(f"YAML parse error: {e}")
                break
            except Exception as e:
                last_error = f"读取失败: {e}"
                logger.error(f"Failed to load config: {e}")
                break

        # 加载失败时弹出提示
        if data is None and last_error:
            QMessageBox.warning(
                self,
                "配置加载失败",
                f"无法加载配置文件:\n{self.config_path}\n\n错误: {last_error}\n\n将使用默认值，保存时会覆盖原有配置。"
            )

        return {}

    def _copy_default_config(self, src: str, dst: str) -> None:
        """复制默认配置模板到用户配置路径。

        Args:
            src: 默认配置模板路径
            dst: 用户配置文件路径
        """
        import shutil
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        logger.info(f"Default config copied from {src} to {dst}")

    def _setup_ui(self):
        """设置界面。"""
        self.setWindowTitle("设置")
        self.setMinimumWidth(500)
        self.setMinimumHeight(480)
        self.setModal(True)

        # 移除右上角问号按钮，保留关闭按钮
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint | Qt.CustomizeWindowHint | Qt.WindowCloseButtonHint | Qt.WindowTitleHint)

        # 主布局
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(30, 25, 30, 25)

        # 标题
        title_label = QLabel("配置参数")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #333333;")
        layout.addWidget(title_label)

        # 配置项网格
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(1, 1)

        row = 0

        # IP 地址
        grid.addWidget(self._create_label("IP 地址"), row, 0)
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("自动获取")
        grid.addWidget(self.ip_input, row, 1)
        row += 1

        # 端口
        grid.addWidget(self._create_label("端口"), row, 0)
        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText("8088")
        grid.addWidget(self.port_input, row, 1)
        row += 1

        # 命名空间
        grid.addWidget(self._create_label("命名空间"), row, 0)
        self.namespace_input = QLineEdit()
        self.namespace_input.setPlaceholderText("meeting_public")
        grid.addWidget(self.namespace_input, row, 1)
        row += 1

        # 分隔线
        line1 = QFrame()
        line1.setFrameShape(QFrame.HLine)
        line1.setStyleSheet("background-color: #e8e8e8;")
        line1.setFixedHeight(1)
        grid.addWidget(line1, row, 0, 1, 2)
        row += 1

        # 平台 API
        grid.addWidget(self._create_label("平台 API"), row, 0)
        self.platform_api_input = QLineEdit()
        self.platform_api_input.setPlaceholderText("http://...")
        grid.addWidget(self.platform_api_input, row, 1)
        row += 1

        # OCR 服务
        grid.addWidget(self._create_label("OCR 服务"), row, 0)
        self.ocr_service_input = QLineEdit()
        self.ocr_service_input.setPlaceholderText("http://...")
        grid.addWidget(self.ocr_service_input, row, 1)
        row += 1

        # 分隔线
        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet("background-color: #e8e8e8;")
        line2.setFixedHeight(1)
        grid.addWidget(line2, row, 0, 1, 2)
        row += 1

        # 日志级别
        grid.addWidget(self._create_label("日志级别"), row, 0)
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        grid.addWidget(self.log_level_combo, row, 1)
        row += 1

        # 设备发现开关（同一行）
        self.discover_android_checkbox = QCheckBox("Android")
        self.discover_android_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
        grid.addWidget(self.discover_android_checkbox, row, 0)

        self.discover_ios_checkbox = QCheckBox("iOS")
        self.discover_ios_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
        grid.addWidget(self.discover_ios_checkbox, row, 1)

        layout.addLayout(grid)

        # 弹性空间
        layout.addStretch()

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)

        cancel_button = QPushButton("取消")
        cancel_button.setFixedSize(100, 36)
        cancel_button.setObjectName("cancelBtn")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        button_layout.addStretch()

        save_button = QPushButton("保存并重启")
        save_button.setFixedSize(120, 36)
        save_button.setObjectName("saveBtn")
        save_button.clicked.connect(self._on_save)
        save_button.setDefault(True)
        button_layout.addWidget(save_button)

        layout.addLayout(button_layout)

    def _create_label(self, text: str) -> QLabel:
        """创建标签。"""
        label = QLabel(text)
        label.setStyleSheet("color: #555555; font-size: 14px;")
        return label

    def _apply_styles(self):
        """应用样式。"""
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
            }
            QLineEdit {
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 8px 12px;
                background-color: #fafafa;
                font-size: 14px;
                color: #333333;
            }
            QLineEdit:focus {
                border: 2px solid #1a73e8;
                background-color: #ffffff;
            }
            QComboBox {
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 8px 12px;
                background-color: #fafafa;
                font-size: 14px;
                color: #333333;
            }
            QComboBox:focus {
                border: 2px solid #1a73e8;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox::down-arrow {
                width: 14px;
                height: 14px;
            }
            QPushButton {
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 14px;
                background-color: #f5f5f5;
                color: #333333;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
                border: 1px solid #b0b0b0;
            }
            QPushButton:pressed {
                background-color: #d8d8d8;
            }
            QPushButton#cancelBtn {
                background-color: #f5f5f5;
                color: #333333;
                border: 1px solid #d0d0d0;
            }
            QPushButton#cancelBtn:hover {
                background-color: #e8e8e8;
                border: 1px solid #b0b0b0;
            }
            QPushButton#saveBtn {
                background-color: #1a73e8;
                color: #ffffff;
                border: 1px solid #1a73e8;
            }
            QPushButton#saveBtn:hover {
                background-color: #1557b0;
                border: 1px solid #1557b0;
            }
            QPushButton#saveBtn:pressed {
                background-color: #0d47a1;
            }
        """)

    def _load_values(self):
        """从配置加载值。"""
        worker = self._config.get("worker", {})
        external = self._config.get("external_services", {})
        logging_cfg = self._config.get("logging", {})

        ip = worker.get("ip")
        if ip:
            self.ip_input.setText(ip)

        port = worker.get("port", 8088)
        self.port_input.setText(str(port))

        namespace = worker.get("namespace", "meeting_public")
        self.namespace_input.setText(namespace)

        platform_api = external.get("platform_api", "")
        self.platform_api_input.setText(platform_api)

        ocr_service = external.get("ocr_service", "")
        self.ocr_service_input.setText(ocr_service)

        log_level = logging_cfg.get("level", "INFO")
        index = self.log_level_combo.findText(log_level)
        if index >= 0:
            self.log_level_combo.setCurrentIndex(index)

        # 设备发现开关
        discover_android = worker.get("discover_android_devices", False)
        self.discover_android_checkbox.setChecked(discover_android)

        discover_ios = worker.get("discover_ios_devices", False)
        self.discover_ios_checkbox.setChecked(discover_ios)

    def _validate(self) -> bool:
        """验证输入。"""
        port_text = self.port_input.text().strip()
        if not port_text:
            logger.warning("Validation failed: port is empty")
            self._show_warning("端口不能为空")
            return False
        try:
            port = int(port_text)
            if port < 1 or port > 65535:
                logger.warning(f"Validation failed: port range {port}")
                self._show_warning("端口范围应为 1-65535")
                return False
        except ValueError:
            logger.warning(f"Validation failed: port not number {port_text}")
            self._show_warning("端口应为数字")
            return False

        ip_text = self.ip_input.text().strip()
        if ip_text:
            ipv4_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
            if not re.match(ipv4_pattern, ip_text):
                logger.warning(f"Validation failed: invalid IP {ip_text}")
                self._show_warning("IP 格式错误")
                return False

        namespace_text = self.namespace_input.text().strip()
        if not namespace_text:
            logger.warning("Validation failed: namespace empty")
            self._show_warning("命名空间不能为空")
            return False

        url_pattern = r"^https?://.+"

        platform_api_text = self.platform_api_input.text().strip()
        if not platform_api_text:
            logger.warning("Validation failed: platform API empty")
            self._show_warning("平台 API 不能为空")
            return False
        if not re.match(url_pattern, platform_api_text):
            logger.warning(f"Validation failed: invalid platform API {platform_api_text}")
            self._show_warning("平台 API 格式错误")
            return False

        ocr_service_text = self.ocr_service_input.text().strip()
        if not ocr_service_text:
            logger.warning("Validation failed: OCR service empty")
            self._show_warning("OCR 服务不能为空")
            return False
        if not re.match(url_pattern, ocr_service_text):
            logger.warning(f"Validation failed: invalid OCR service {ocr_service_text}")
            self._show_warning("OCR 服务格式错误")
            return False

        return True

    def _show_warning(self, message: str) -> None:
        """显示警告提示对话框。"""
        QMessageBox.warning(self, "配置验证", message)

    def _on_save(self):
        """保存按钮点击。"""
        if not self._validate():
            return

        # Read original file content to preserve comments and format
        # Try multiple encodings for compatibility
        original_content = ""
        encodings = ["utf-8", "gbk", "gb18030"]

        for encoding in encodings:
            try:
                if os.path.exists(self.config_path):
                    with open(self.config_path, "r", encoding=encoding) as f:
                        original_content = f.read()
                    logger.info(f"Config file read successfully with {encoding} encoding")
                    break
            except UnicodeDecodeError as e:
                logger.warning(f"Failed to read config with {encoding}: {e}")
                continue
            except Exception as e:
                logger.error(f"Failed to read config file: {e}")
                continue

        if original_content:
            # Update specific fields using string replacement (preserve comments)
            original_content = self._update_yaml_value(original_content, "ip", self.ip_input.text().strip() or "null")
            original_content = self._update_yaml_value(original_content, "port", self.port_input.text().strip())
            original_content = self._update_yaml_value(original_content, "namespace", self.namespace_input.text().strip())
            original_content = self._update_yaml_value(original_content, "platform_api", self.platform_api_input.text().strip())
            original_content = self._update_yaml_value(original_content, "ocr_service", self.ocr_service_input.text().strip())
            original_content = self._update_yaml_value(original_content, "level", self.log_level_combo.currentText())
            original_content = self._update_yaml_value(original_content, "discover_android_devices", "true" if self.discover_android_checkbox.isChecked() else "false")
            original_content = self._update_yaml_value(original_content, "discover_ios_devices", "true" if self.discover_ios_checkbox.isChecked() else "false")

            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    f.write(original_content)
                logger.info(f"Configuration saved: {self.config_path}")
                QMessageBox.information(self, "保存成功", "配置已保存，程序将重启以应用新配置。")
                self.accept()
            except Exception as e:
                logger.error(f"Failed to save config: {e}")
                QMessageBox.warning(self, "保存失败", f"无法保存配置文件:\n{self.config_path}\n\n错误: {e}")
        else:
            # Fallback: create new config if original file doesn't exist
            self._config.setdefault("worker", {})
            self._config["worker"]["ip"] = self.ip_input.text().strip() or None
            self._config["worker"]["port"] = int(self.port_input.text().strip())
            self._config["worker"]["namespace"] = self.namespace_input.text().strip()
            self._config["worker"]["discover_android_devices"] = self.discover_android_checkbox.isChecked()
            self._config["worker"]["discover_ios_devices"] = self.discover_ios_checkbox.isChecked()

            self._config.setdefault("external_services", {})
            self._config["external_services"]["platform_api"] = self.platform_api_input.text().strip()
            self._config["external_services"]["ocr_service"] = self.ocr_service_input.text().strip()

            self._config.setdefault("logging", {})
            self._config["logging"]["level"] = self.log_level_combo.currentText()

            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    yaml.dump(self._config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                logger.info(f"Configuration saved: {self.config_path}")
                QMessageBox.information(self, "保存成功", "配置已保存，程序将重启以应用新配置。")
                self.accept()
            except Exception as e:
                logger.error(f"Failed to save config: {e}")
                QMessageBox.warning(self, "保存失败", f"无法保存配置文件:\n{self.config_path}\n\n错误: {e}")

    def _update_yaml_value(self, content: str, key: str, value: str) -> str:
        """Update YAML value while preserving comments and format.

        Uses simple string replacement to update specific key values.
        """
        import re

        # Boolean values should not be quoted
        if value in ("true", "false"):
            # Keep boolean value without quotes
            pass
        elif value != "null" and not value.isdigit() and not value.startswith('"'):
            # Quote string values (except null, numbers, and already quoted)
            value = f'"{value}"'

        # Pattern: match "  key: value" (with possible comment after)
        # Handles: key: value, key: "value", key: null
        pattern = rf'^(\s+){key}:\s*([^\s#]+)(\s*(#.*)?)$'

        lines = content.split('\n')
        for i, line in enumerate(lines):
            match = re.match(pattern, line)
            if match:
                indent = match.group(1)
                comment_part = match.group(3) or ""
                lines[i] = f"{indent}{key}: {value}{comment_part}"
                break

        return '\n'.join(lines)