"""
common 公共模块 —— 所有端共享的基础库。

使用方式:
    from common.config import Config
    from common.base_api import BaseAPI
    from common.assertions import assert_status_ok, assert_json_contains
    from common.data_factory import DataFactory
    from common.utils import retry, timestamp
"""

from common.config import Config
from common.base_api import BaseAPI
from common.assertions import assert_status_ok, assert_json_contains
from common.data_factory import DataFactory
