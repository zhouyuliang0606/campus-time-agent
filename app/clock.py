"""全项目**唯一**的取时间入口（#56-A）。

为什么单独开一个文件，而不是各处继续 `datetime.now()`：
  ① `datetime.now()` 跟**操作系统时区**走。本机是 UTC+8 所以看不出问题；
     部署到默认 UTC 的云主机上就差 8 小时 —— 22 点以后的请求会跨到"前一天"，
     模型说"今天是昨天"、候选时段与「此刻之后」那道闸一起跟着错，而且**全程没有报错**，
     全是静默错。这种错在演示里最难抓：评委的机器和本机不一样，但没人会去查时区。
  ② 同一件事有 24 个写法（实测 AST 扫出来的数），就是 24 个可能漏改的地方（红线 12 那一族）。

⚠️ **不用 `ZoneInfo("Asia/Shanghai")`**：Windows 没有 IANA 时区库，本项目 `.venv` 也没装 `tzdata`，
   实测直接 `ZoneInfoNotFoundError: No time zone found with key Asia/Shanghai`（2026-10-22 本机跑过；
   上一轮我建议的写法在这台机器上根本跑不起来）。固定 +8 是对的：中国 1991 年后不实行夏令时，全年 UTC+8。

本模块**只 import 标准库**：它被所有模块依赖，反向 import 任何 `app.*` 都会成环。
"""
import datetime

# `CST` 只是给人看的标签，判断依据是那个 timedelta（不是标签，也不是系统时区）
_TZ = datetime.timezone(datetime.timedelta(hours=8), "CST")


def now() -> datetime.datetime:
    """此刻（**东八区钟面值，naive**）。

    ⚠️ 故意 `.replace(tzinfo=None)`，这不是疏忽：全项目的时间都是 naive ——
       `ts` 存成 `"%Y-%m-%d %H:%M:%S"` 字符串，各处再 `strptime` 回来做减法
       （`main._age_hours` 的 `now() - t`、`express` 的 `today() - arrived` 都是）。
       混进 tz-aware，那些减法**当场 TypeError**。
       要时区就在这一层定死，不许在任何一个调用点带 tz。
    """
    return datetime.datetime.now(_TZ).replace(tzinfo=None)


def today() -> datetime.date:
    """东八区的今天。"""
    return now().date()


def now_str() -> str:
    """落盘用的时间串（`ts` / `created_at` 的统一格式）。"""
    return now().strftime("%Y-%m-%d %H:%M:%S")
