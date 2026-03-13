"""
Web 端登录相关业务流程封装。

将"登录"这个高频业务动作封装为 step，测试用例直接调用即可，
不需要每次重复写 打开页面 → 填账号 → 填密码 → 点击登录 → 断言成功。

Usage:
    # 在测试用例中
    def test_something_after_login(self, login_steps):
        login_steps.login_as("testuser", "Test@123")
        # 后续操作...

    # 或在其他 steps 中组合
    class OrderSteps:
        def __init__(self, page):
            self.login_steps = LoginSteps(page)
            self.order_page = OrderPage(page)

        def create_order_as_user(self, username, password, product):
            self.login_steps.login_as(username, password)
            self.order_page.select_product(product)
            ...
"""

from playwright.sync_api import Page

from web.pages.login_page import LoginPage


class LoginSteps:
    """Web 端登录业务流程封装。

    封装级别说明：
        - LoginPage.login() → 只是填写表单并提交（页面级操作）
        - LoginSteps.login_as() → 打开页面 + 登录 + 断言成功（完整业务流程）

    Args:
        page: Playwright Page 对象。
    """

    def __init__(self, page: Page):
        self.page = page
        self.login_page = LoginPage(page)

    def login_as(self, username: str, password: str):
        """以指定账号完成登录全流程。

        步骤: 打开登录页 → 输入账号密码 → 提交 → 断言登录成功。

        Args:
            username: 用户名。
            password: 密码。
        """
        self.login_page.open()
        self.login_page.login(username, password)
        self.login_page.should_login_success()

    def login_as_admin(self):
        """以管理员身份登录（使用默认管理员账号）。"""
        self.login_as("admin", "Admin@123")

    def login_and_stay(self, username: str, password: str):
        """登录后停留在当前页面（不断言跳转，用于测试登录后的页面状态）。

        Args:
            username: 用户名。
            password: 密码。
        """
        self.login_page.open()
        self.login_page.login(username, password)

    def logout(self):
        """执行退出登录操作。"""
        # TODO: 根据实际页面实现
        pass
