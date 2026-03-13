"""api.steps 包 —— API 端业务流程封装层。

steps 层的定位：
    - services 层：单个接口的请求封装
    - steps 层：多接口组合的业务流程（登录+获取token、创建订单完整流程）
    - tests 层：测试用例，组合 steps 完成场景验证

命名规范：
    - 文件名: <业务>_steps.py
    - 类名:   <业务>Steps
"""
