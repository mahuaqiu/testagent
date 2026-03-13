"""
示例：Web 登录功能测试用例。

演示两种写法：
    1. 直接用 Page Object（适合测试登录页面本身的各种场景）
    2. 用 Steps（适合"登录"只是前置步骤、重点测试后续功能的场景）

约定:
    - 文件名: test_<功能>.py
    - 类名:   Test<功能>（可选，也可以直接用函数）
    - 函数名: test_<场景描述>
    - 必须标注 @pytest.mark.web
"""

import pytest

from web.pages.login_page import LoginPage


@pytest.mark.web
class TestLogin:
    """登录功能测试集 —— 测试登录页面本身，直接使用 Page Object。"""

    def test_login_success(self, page, data_factory):
        """正常登录：输入正确的账号密码，应登录成功并看到欢迎文案。"""
        login_page = LoginPage(page)
        login_page.open()
        login_page.login(username="testuser", password="Test@123")
        login_page.should_login_success()

    def test_login_wrong_password(self, page):
        """异常场景：密码错误，应提示错误信息。"""
        login_page = LoginPage(page)
        login_page.open()
        login_page.login(username="testuser", password="wrong_password")
        login_page.should_show_error("用户名或密码错误")

    def test_login_empty_username(self, page):
        """异常场景：用户名为空，应提示必填。"""
        login_page = LoginPage(page)
        login_page.open()
        login_page.login(username="", password="Test@123")
        login_page.should_show_error("请输入用户名")


@pytest.mark.web
class TestHomeAfterLogin:
    """登录后首页功能 —— 登录只是前置步骤，用 login_steps 简化。

    这是 steps 层的典型使用场景：
    当"登录"不是你要测试的功能，而只是前置条件时，用 steps 一行搞定。
    """

    def test_home_shows_username(self, login_steps, page):
        """登录后首页应显示当前用户名。"""
        login_steps.login_as("testuser", "Test@123")
        # 后续操作首页...
        # home_page = HomePage(page)
        # home_page.should_show_username("testuser")

    def test_admin_sees_management_menu(self, login_steps, page):
        """管理员登录后应看到管理菜单入口。"""
        login_steps.login_as_admin()
        # home_page = HomePage(page)
        # home_page.should_show_management_menu()
