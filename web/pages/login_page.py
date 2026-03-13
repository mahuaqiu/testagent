"""
示例：登录页面 Page Object。

演示如何基于 BasePage 编写一个具体页面的操作类。
SKILL 生成新页面时可参考此文件的结构和命名风格。
"""

from web.pages.base_page import BasePage


class LoginPage(BasePage):
    """Web 端登录页面。

    定位器命名规范: loc_<用途>，如 loc_username、loc_submit_btn。
    业务方法直接描述操作: login()、fill_username()。
    """

    # ── 定位器 ──
    loc_username = "input[name='username']"
    loc_password = "input[name='password']"
    loc_submit_btn = "button[type='submit']"
    loc_error_msg = ".error-message"
    loc_welcome_text = ".welcome"

    # ── 页面操作 ──

    def open(self):
        """打开登录页面。"""
        self.navigate("/login")

    def login(self, username: str, password: str):
        """执行登录操作：填写账号密码并提交。

        Args:
            username: 用户名。
            password: 密码。
        """
        self.fill(self.loc_username, username)
        self.fill(self.loc_password, password)
        self.click(self.loc_submit_btn)

    def get_error_message(self) -> str:
        """获取登录错误提示文本。"""
        return self.get_text(self.loc_error_msg)

    # ── 断言 ──

    def should_login_success(self):
        """断言登录成功：欢迎文案可见。"""
        self.expect_visible(self.loc_welcome_text)

    def should_show_error(self, expected_msg: str):
        """断言显示指定的错误提示。

        Args:
            expected_msg: 期望的错误提示文本。
        """
        self.expect_text(self.loc_error_msg, expected_msg)
