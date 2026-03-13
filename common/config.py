"""
配置管理模块 —— 读取 config/settings.yaml 并按环境返回配置。

Usage:
    cfg = Config(env="dev")
    base_url = cfg.get("base_url")          # "https://dev.example.com"
    db_host  = cfg.get("db.host")           # "127.0.0.1"
    caps     = cfg.get("app.desired_caps")  # dict
"""

import os
import yaml


class Config:
    """多环境配置管理器。

    从 config/settings.yaml 加载配置，根据 env 参数选择环境段。
    支持点号分隔的 key 路径访问嵌套值。

    Args:
        env: 环境名称，对应 yaml 中的顶级 key（dev / staging / prod）。
    """

    def __init__(self, env: str = "dev"):
        self.env = env
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml"
        )
        with open(config_path, "r", encoding="utf-8") as f:
            all_config = yaml.safe_load(f)
        self._data: dict = all_config.get(env, {})

    def get(self, key: str, default=None):
        """通过点号路径获取配置值。

        Args:
            key: 配置键，支持点号分隔，如 "db.host"、"app.desired_caps"。
            default: 键不存在时的默认值。

        Returns:
            配置值，或 default。

        Examples:
            >>> cfg = Config(env="dev")
            >>> cfg.get("base_url")
            'https://dev.example.com'
            >>> cfg.get("db.host")
            '127.0.0.1'
        """
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    @property
    def base_url(self) -> str:
        """Web 端 base_url 快捷访问。"""
        return self.get("base_url", "")

    @property
    def api_base_url(self) -> str:
        """API base_url 快捷访问。"""
        return self.get("api_base_url", "")
