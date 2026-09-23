# app/modules 包：装各个业务模块（课表/问答/驿站/拓展/管理）
# 每个模块文件对外暴露：MODULE_KEY、SYSTEM_PROMPT、build_tools()
# 由 app/main.py 的注册表统一收集后交给 Agent 引擎使用。
