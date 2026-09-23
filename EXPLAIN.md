# EXPLAIN.md —— 每个文件「人话在干啥」

> 给评审和零基础同学看：不用读代码，先看这一页就知道每个文件是干嘛的。
> 会随着项目推进持续更新（每加一个关键文件就补一行）。

## 一、项目一句话
**校园时间管家 CampusTime** = 一个会"先想后做"的校园 AI 助手，帮学生排课表时间、答校园问题，帮驿站主动提醒取件，帮管理员零代码管知识库。

## 二、文件地图（人话作用）

| 文件 | 人话作用 |
| --- | --- |
| `README.md` | 项目门面：一句话介绍 + 零基础怎么跑起来 |
| `.env.example` | 密钥样板：告诉你 .env 里要填什么（真实 .env 不提交） |
| `.gitignore` | 守门员：保证密钥和缓存永远不进 git |
| `requirements.txt` | 购物清单：项目用到哪些第三方库，一键安装 |
| `EXPLAIN.md` | 本文件：每个关键文件的「人话作用」 |
| `app/__init__.py` | 文件夹标记：告诉 Python 这是可导入的代码包 |
| `app/config.py` | 读配置：从 .env 拿密钥和开关，全项目统一取用 |
| `app/llm/client.py` | 大模型电话：把我们的话发给 DeepSeek，把回答拿回来 |
| `app/agent/engine.py` | Agent 大脑：ReAct 循环（想→做→看结果→再想） |
| `app/agent/router.py` | 调度员：判断用户的话归哪个模块管（课表/问答/驿站…） |
| `app/agent/tools.py` | 工具箱底座：规范每个"工具"怎么描述、怎么被调用 |
| `app/modules/schedule.py` | 旗舰模块：课表时间规划（查课表/找空闲/排任务） |
| `app/modules/faq.py` | 学生端·校园问答：search_kb 在知识库检索，收口分散校园信息 |
| `app/modules/express.py` | 学生端·快递查询：查我的快递/取件码/自动算"滞留"预警 |
| `app/modules/takeout.py` | 学生端·外卖查询：查外卖订单/取餐点/待取提醒 |
| `app/data/kb.json` | 校园知识库：图书馆/食堂/校车/校医院/宿舍/奖学金等问答来源 |
| `app/data/express.json` | 演示快递台账（含一件"滞留"件用于预警演示） |
| `app/data/takeout.json` | 演示外卖订单（制作中/配送中/待取） |
| `app/modules/faq.py` | 校园问答：从知识库找答案再组织语言回答 |
| `app/modules/station.py` | 驿站助手：主动提醒取件 + 自定义人格回复 |
| `app/modules/admin.py` | 管理台：知识库增删改 + 客服人格配置 |
| `app/modules/lostfound.py` / `repair.py` / `notice.py` | 拓展模块：失物招领/报修/通知 |
| `app/main.py` | 总入口：FastAPI 把上面所有能力接到网页接口上 |
| `app/data/courses.json` | 示例课表：旗舰模块用来演示的真实数据 |
| `app/data/kb.json` | 校园知识库：问答模块的答案来源 |
| `app/static/*.html` | 三个前端页面：学生端 / 驿站端 / 管理台 |
| `ai-logs/*.md` | 开发日记：每做一个模块，把我们的对话整理成一篇 |

## 三、它是怎么转起来的（粗流程图）
```
用户在前端说话
   → app/main.py 收到
   → router 判断意图（属于哪个模块）
   → engine 用该模块的工具 + 大模型，跑 ReAct 循环
   → 需要时调用工具（查课表/查知识库/查台账）
   → 大模型把结果组织成自然语言
   → 返回前端展示
```

## 四、给评审的一句话总结
> 没有用现成 Agent 框架，而是自己写了一个轻量 ReAct 推理循环；
> 三层业务（学生/驿站/管理）共用一套引擎 + 知识库 + 大模型；
> 密钥全程走环境变量，不进代码；每一步都有通俗注释和文档。
