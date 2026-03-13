"""
示例：Android 登录功能测试用例。

约定:
    - 文件名: test_<功能>.py
    - 类名:   Test<功能>
    - 函数名: test_<场景描述>
    - 必须标注 @pytest.mark.android
"""

import pytest

from android.pages.login_page import LoginPage


@pytest.mark.android
class TestLogin:
    """Android 登录功能测试集。"""

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