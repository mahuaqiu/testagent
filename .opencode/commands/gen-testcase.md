---
name: gen-testcase
description: "生成自动化测试用例。启动三阶段流水线，将测试需求转化为落地的自动化代码。"
agent: testcase-leader
---

# 生成自动化测试用例

启动 `testcase-leader` Agent，执行完整的三阶段流水线：

```
用户需求 → testcase-refiner → testcase-planner → testcase-coder → 测试代码
```

## 使用方式

```
/gen-testcase <你的测试需求描述>
```

## 示例

```
/gen-testcase 帮我生成登录功能的 Web 测试用例，包括正常登录和各种异常场景

/gen-testcase 根据以下接口文档生成 API 测试用例：
POST /api/users/register
参数: {"username": "str", "password": "str", "phone": "str"}

/gen-testcase 把这份手工用例转成自动化：
1. 输入正确账号密码登录
2. 验证跳转到首页
3. 首页显示用户名
```

## 流程说明

1. **阶段 1 - 需求结构化**：与用户交互确认细节，输出标准测试步骤
2. **阶段 2 - 代码库分析**：扫描已有 Pages/Steps/Services，生成代码计划
3. **阶段 3 - 代码生成**：按计划创建/修改文件，生成测试代码

每个阶段结束后会请用户确认，确保方向正确后再继续。