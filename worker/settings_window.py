"""
设置窗口模块。

PyQt5 实现的配置设置对话框。
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
    QMessageBox,
)
from PyQt5.QtCore import Qt

import logging

logger = logging.getLogger(__name__)


class SettingsWindow(QDialog):
    """设置窗口。"""

    def __init__(self, config_path: str, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self._config = self._load_config()

        self._setup_ui()
        self._load_values()

    def _load_config(self) -> dict:
        """加载配置文件。"""
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _setup_ui(self):
        """设置界面。"""
        self.setWindowTitle("Test Worker 设置")
        self.setFixedSize(450, 300)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Worker IP
        ip_layout = QHBoxLayout()
        ip_label = QLabel("Worker IP 地址:")
        ip_label.setFixedWidth(120)
        self.ip_input = QLineEdit()
        ip_layout.addWidget(ip_label)
        ip_layout.addWidget(self.ip_input)
        layout.addLayout(ip_layout)

        # Worker 端口
        port_layout = QHBoxLayout()
        port_label = QLabel("Worker 端口:")
        port_label.setFixedWidth(120)
        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText("8088")
        port_layout.addWidget(port_label)
        port_layout.addWidget(self.port_input)
        layout.addLayout(port_layout)

        # 命名空间
        namespace_layout = QHBoxLayout()
        namespace_label = QLabel("命名空间:")
        namespace_label.setFixedWidth(120)
        self.namespace_input = QLineEdit()
        self.namespace_input.setPlaceholderText("meeting_public")
        namespace_layout.addWidget(namespace_label)
        namespace_layout.addWidget(self.namespace_input)
        layout.addLayout(namespace_layout)

        # 平台 API 地址
        platform_api_layout = QHBoxLayout()
        platform_api_label = QLabel("平台 API 地址:")
        platform_api_label.setFixedWidth(120)
        self.platform_api_input = QLineEdit()
        platform_api_layout.addWidget(platform_api_label)
        platform_api_layout.addWidget(self.platform_api_input)
        layout.addLayout(platform_api_layout)

        # OCR 服务地址
        ocr_service_layout = QHBoxLayout()
        ocr_service_label = QLabel("OCR 服务地址:")
        ocr_service_label.setFixedWidth(120)
        self.ocr_service_input = QLineEdit()
        ocr_service_layout.addWidget(ocr_service_label)
        ocr_service_layout.addWidget(self.ocr_service_input)
        layout.addLayout(ocr_service_layout)

        # 日志级别
        log_level_layout = QHBoxLayout()
        log_level_label = QLabel("日志级别:")
        log_level_label.setFixedWidth(120)
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        log_level_layout.addWidget(log_level_label)
        log_level_layout.addWidget(self.log_level_combo)
        layout.addLayout(log_level_layout)

        # 添加弹性空间
        layout.addStretch()

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        save_button = QPushButton("保存并重启")
        save_button.clicked.connect(self._on_save)
        button_layout.addWidget(save_button)

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)

    def _load_values(self):
        """从配置加载值。"""
        worker = self._config.get("worker", {})
        external = self._config.get("external_services", {})
        logging_cfg = self._config.get("logging", {})

        # Worker IP
        ip = worker.get("ip")
        if ip:
            self.ip_input.setText(ip)

        # Worker 端口
        port = worker.get("port", 8088)
        self.port_input.setText(str(port))

        # 命名空间
        namespace = worker.get("namespace", "meeting_public")
        self.namespace_input.setText(namespace)

        # 平台 API 地址
        platform_api = external.get("platform_api", "")
        self.platform_api_input.setText(platform_api)

        # OCR 服务地址
        ocr_service = external.get("ocr_service", "")
        self.ocr_service_input.setText(ocr_service)

        # 日志级别
        log_level = logging_cfg.get("level", "INFO")
        index = self.log_level_combo.findText(log_level)
        if index >= 0:
            self.log_level_combo.setCurrentIndex(index)

    def _validate(self) -> bool:
        """验证输入。"""
        # 端口验证
        port_text = self.port_input.text().strip()
        if not port_text:
            QMessageBox.warning(self, "验证失败", "Worker 端口不能为空")
            return False
        try:
            port = int(port_text)
            if port < 1 or port > 65535:
                QMessageBox.warning(self, "验证失败", "Worker 端口范围应为 1-65535")
                return False
        except ValueError:
            QMessageBox.warning(self, "验证失败", "Worker 端口应为数字")
            return False

        # IP 验证（可选）
        ip_text = self.ip_input.text().strip()
        if ip_text:
            ipv4_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
            if not re.match(ipv4_pattern, ip_text):
                QMessageBox.warning(self, "验证失败", "Worker IP 格式错误")
                return False

        # 命名空间验证
        namespace_text = self.namespace_input.text().strip()
        if not namespace_text:
            QMessageBox.warning(self, "验证失败", "命名空间不能为空")
            return False

        # URL 验证
        url_pattern = r"^https?://.+"

        platform_api_text = self.platform_api_input.text().strip()
        if not platform_api_text:
            QMessageBox.warning(self, "验证失败", "平台 API 地址不能为空")
            return False
        if not re.match(url_pattern, platform_api_text):
            QMessageBox.warning(self, "验证失败", "平台 API 地址格式错误（应为 http:// 或 https://）")
            return False

        ocr_service_text = self.ocr_service_input.text().strip()
        if not ocr_service_text:
            QMessageBox.warning(self, "验证失败", "OCR 服务地址不能为空")
            return False
        if not re.match(url_pattern, ocr_service_text):
            QMessageBox.warning(self, "验证失败", "OCR 服务地址格式错误（应为 http:// 或 https://）")
            return False

        return True

    def _on_save(self):
        """保存按钮点击。"""
        if not self._validate():
            return

        # 更新配置
        self._config.setdefault("worker", {})
        self._config["worker"]["ip"] = self.ip_input.text().strip() or None
        self._config["worker"]["port"] = int(self.port_input.text().strip())
        self._config["worker"]["namespace"] = self.namespace_input.text().strip()

        self._config.setdefault("external_services", {})
        self._config["external_services"]["platform_api"] = self.platform_api_input.text().strip()
        self._config["external_services"]["ocr_service"] = self.ocr_service_input.text().strip()

        self._config.setdefault("logging", {})
        self._config["logging"]["level"] = self.log_level_combo.currentText()

        # 写入配置文件
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(self._config, f, allow_unicode=True, default_flow_style=False)
            logger.info(f"Configuration saved: {self.config_path}")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"无法保存配置文件: {e}")
            logger.error(f"Failed to save config: {e}")

    def get_config(self) -> dict:
        """获取更新后的配置。"""
        return self._config