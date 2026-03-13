---
name: code
description: "只执行代码生成阶段。需要已有代码生成计划作为输入。"
skill: testcase-coder
---

# 代码生成

只调用 `testcase-coder` Skill，按计划生成测试代码。

## 使用方式

```
/code <粘贴代码生成计划>
```

## 适用场景

- 已有详细的代码生成计划（如之前 plan 的输出）
- 计划已评审确认，只需执行落地

## 输出

生成/修改的代码文件：
- Page Object / Service 文件
- Steps 文件
- conftest.py fixtures
- 测试用例文件