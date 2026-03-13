"""
App 端登录相关业务流程封装。

Usage:
    def test_something_after_login(self, login_steps):
        login_steps.login_as("testuser", "Test@123")
        # 后续操作...
"""

from appium.webdriver import Remote as AppiumDriver

from app.pages.login_page import LoginPage


class LoginSteps:
    """App 端登录业务流程封装。

    封装级别说明：
        - LoginPage.login() → 只是填写表单并提交（页面级操作）
        - LoginSteps.login_as() → 完整登录流程 + 断言成功（业务流程）

    Args:
        driver: Appium Driver 对象。
    """

    def __init__(self, driver: AppiumDriver):
        self.driver = driver
        self.login_page = LoginPage(driver)

    def login_as(self, username: str, password: str):
        """以指定账号完成登录全流程。

        步骤: 输入账号密码 → 提交 → 断言登录成功。

        Args:
            username: 用户名。
            password: 密码。
        """
        self.login_page.login(username, password)
        self.login_page.should_login_success()

    def login_as_admin(self):
        """以管理员身份登录。"""
        self.login_as("admin", "Admin@123")

    def logout(self):
        """执行退出登录操作。"""
        # TODO: 根据实际 App 页面实现
        pass
