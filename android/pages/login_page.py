"""
示例：Android 登录页面 Page Object。

演示如何基于 BasePage 编写 Android 端的页面操作类。
"""

from android.pages.base_page import BasePage


class LoginPage(BasePage):
    """Android 端登录页面。

    定位器命名规范: loc_<用途>，使用 tuple 格式 (by, value)。
    """

    # ── 定位器 ──
    loc_username = ("id", "com.example:id/et_username")
    loc_password = ("id", "com.example:id/et_password")
    loc_submit_btn = ("id", "com.example:id/btn_login")
    loc_error_toast = ("xpath", "//android.widget.Toast")
    loc_welcome_text = ("id", "com.example:id/tv_welcome")

    # ── 页面操作 ──

    def login(self, username: str, password: str):
        """执行登录操作：填写账号密码并点击登录。"""
        self.input_text(self.loc_username, username)
        self.input_text(self.loc_password, password)
        self.click(self.loc_submit_btn)

    # ── 断言 ──

    def should_login_success(self):
        """断言登录成功：欢迎文案可见。"""
        assert self.is_displayed(self.loc_welcome_text), "登录后未看到欢迎文案"

    def should_show_error(self, expected_msg: str):
        """断言显示指定的错误提示。"""
        text = self.get_text(self.loc_error_toast)
        assert expected_msg in text, f"期望错误信息包含 '{expected_msg}'，实际: '{text}'"