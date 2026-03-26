"""测试 HostDiscovery 模块。"""

import pytest
from worker.discovery.host import HostDiscoverer


class TestGetPreferredIp:
    """测试 get_preferred_ip 方法。"""

    def test_no_config_returns_auto_ip(self):
        """未配置 IP 时，返回自动获取的 IP。"""
        result = HostDiscoverer.get_preferred_ip(configured_ip=None)
        assert result is not None
        assert result != ""

    def test_valid_config_ip_returns_config(self):
        """配置的 IP 在本机存在时，返回配置的 IP。"""
        # 先获取本机所有 IP
        all_ips = HostDiscoverer.get_ip_addresses()
        if not all_ips or all_ips[0] == "127.0.0.1":
            pytest.skip("No non-loopback IP available")

        valid_ip = all_ips[0]
        result = HostDiscoverer.get_preferred_ip(configured_ip=valid_ip)
        assert result == valid_ip

    def test_invalid_config_ip_falls_back(self):
        """配置的 IP 不在本机时，回退到自动获取。"""
        invalid_ip = "999.999.999.999"
        result = HostDiscoverer.get_preferred_ip(configured_ip=invalid_ip)
        # 应该返回本机的某个 IP，而不是无效 IP
        assert result != invalid_ip
        assert result is not None