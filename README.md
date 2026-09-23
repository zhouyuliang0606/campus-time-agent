# 校园时间管家 Agent / CampusTime

一个校园 AI 服务助手，分三层：

1. **学生活端（旗舰）**：课表时间规划 + 校园问答，把分散的校园信息收口到一个助手。
2. **驿站/商户侧**：监听快递外卖台账变动，主动推送取件提醒与滞留预警，用自定义人格智能回复咨询。
3. **管理控制台**：零代码维护知识库、配置客服人格。

三层共享同一个 Agent 引擎（意图路由 + 协调执行）+ 知识库 + 大模型。

## 技术栈
- 后端：Python + FastAPI
- 前端：原生 HTML / JS（不需要打包工具）
- 大模型：DeepSeek API（Key 走环境变量，不写进代码）
- Agent：自写的轻量 ReAct 循环，不套重框架

## 给零基础的你：怎么跑起来
1. 安装 Python 3.10+（已装 PyCharm 社区版的话一般就有了）
2. 在项目目录执行：`pip install -r requirements.txt`
3. 复制 `.env.example` 为 `.env`，填入你的 DeepSeek Key
4. 启动：`uvicorn app.main:app --reload`
5. 浏览器打开：`http://127.0.0.1:8000`

想验证一下功能有没有坏（不用装任何额外包，100 项自检）：

```bash
python tests/run_all.py
```

测试跑在临时数据副本上，不会碰你的演示数据。详见 [tests/README.md](tests/README.md)。

更详细的"人话说明"请看 [EXPLAIN.md](EXPLAIN.md)。
每一步的开发对话记录在 [ai-logs/](ai-logs/) 目录。
