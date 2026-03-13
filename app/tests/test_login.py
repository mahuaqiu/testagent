"""
示例：App 登录功能测试用例。

演示 App 端标准的测试用例写法，SKILL 生成新用例时参考此风格。

约定:
    - 文件名: test_<功能>.py
    - 类名:   Test<功能>
    - 函数名: test_<场景描述>
    - 必须标注 @pytest.mark.app
"""

import pytest

from app.pages.login_page import LoginPage


@pytest.mark.app
class TestLogin:
    """App 登录功能测试集。"""

    def test_login_success(self, driver, data_factory):
        """正常登录：输入正确的账号密码，应登录成功并看到欢迎文案。"""
        login_page = LoginPage(driver)
        login_page.login(username="testuser", password="Test@123")
        login_page.should_login_success()

    def test_login_wrong_password(self, driver):
        """异常场景：密码错误，应提示错误信息。"""
        login_page = LoginPage(driver)
        login_page.login(username="testuser", password="wrong_password")
        login_page.should_show_error("用户名或密码错误")

    def test_login_empty_username(self, driver):
        """异常场景：用户名为空，应提示必填。"""
        login_page = LoginPage(driver)
        login_page.login(username="", password="Test@123")
        login_page.should_show_error("请输入用户名")
