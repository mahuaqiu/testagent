"""
测试数据工厂 —— 使用 Faker 生成各类随机测试数据。

Usage:
    from common.data_factory import DataFactory

    df = DataFactory()
    user = df.random_user()
    phone = df.random_phone()
"""

from faker import Faker


class DataFactory:
    """测试数据生成工厂，封装常见的测试数据生成方法。

    默认使用中文 locale（zh_CN），可通过参数切换。

    Args:
        locale: Faker locale，如 "zh_CN"、"en_US"。
    """

    def __init__(self, locale: str = "zh_CN"):
        self.fake = Faker(locale)

    def random_user(self) -> dict:
        """生成随机用户信息。

        Returns:
            dict: 包含 name, phone, email, address 的字典。

        Examples:
            >>> df = DataFactory()
            >>> user = df.random_user()
            >>> # {"name": "张三", "phone": "13800001111", "email": "...", "address": "..."}
        """
        return {
            "name": self.fake.name(),
            "phone": self.fake.phone_number(),
            "email": self.fake.email(),
            "address": self.fake.address(),
        }

    def random_phone(self) -> str:
        """生成随机手机号。"""
        return self.fake.phone_number()

    def random_email(self) -> str:
        """生成随机邮箱。"""
        return self.fake.email()

    def random_text(self, max_length: int = 100) -> str:
        """生成随机文本。

        Args:
            max_length: 最大字符数。
        """
        return self.fake.text(max_nb_chars=max_length)

    def random_int(self, min_val: int = 1, max_val: int = 9999) -> int:
        """生成随机整数。"""
        return self.fake.random_int(min=min_val, max=max_val)

    def template_user(self, **overrides) -> dict:
        """生成用户数据，支持部分字段覆盖。

        用于需要固定某些字段、其余随机的场景。

        Args:
            **overrides: 需要覆盖的字段。

        Returns:
            dict: 合并后的用户字典。

        Examples:
            >>> df.template_user(name="测试专用", phone="13800000000")
            {"name": "测试专用", "phone": "13800000000", "email": "...", "address": "..."}
        """
        user = self.random_user()
        user.update(overrides)
        return user
