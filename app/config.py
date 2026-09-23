"""配置模块（人话：统一从 .env 读取密钥和开关，全项目都来这里拿，代码里不写死任何密码）。"""
import os

from dotenv import load_dotenv

# load_dotenv() 会读取项目根目录下的 .env 文件，把里面的内容变成环境变量
# 这样我们下面用 os.getenv 就能拿到密钥，而密钥本身不在代码里
load_dotenv()

# —— DeepSeek 大模型相关配置 ——
# 你的 API Key：从环境变量读。如果没填，就是空字符串（调用时会报错提示你去填）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
# 接口地址，DeepSeek 兼容 OpenAI 格式，默认就是这个
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 使用的模型名，deepseek-chat 是通用 V3 模型
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# —— 调试开关 ——
# DEBUG=true 时，Agent 的"思考轨迹"会打印出来，方便演示和排查问题
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")


def get_llm_config() -> dict:
    """获取当前真正生效的大模型配置（人话：管理后台配了就听它的，没配就用 .env 的）。

    优先级：管理控制台的 settings.json  >  .env 环境变量

    为什么做成一个函数而不是几个常量？
    因为管理员在后台改完配置希望立刻生效。每次调用重新读一次文件，
    开销可以忽略，但换来了"改完不用重启服务"这个很值钱的体验。
    """
    # 放在函数内导入：避免 config 这个底层模块在启动时就依赖 store
    from app.store import get_settings

    s = get_settings()
    key = s.get("api_key") or DEEPSEEK_API_KEY
    # base_url / model 允许只覆盖其中一个，另一个回落到默认值
    base = s.get("base_url") or DEEPSEEK_BASE_URL
    model = s.get("model") or DEEPSEEK_MODEL

    if s.get("api_key"):
        source = "管理后台配置"
    elif DEEPSEEK_API_KEY:
        source = ".env 环境变量"
    else:
        source = "未配置"
    return {"api_key": key, "base_url": base, "model": model, "source": source}


def check_config() -> None:
    """启动时检查密钥是否配置（人话：没填 Key 就友好提醒，别让程序莫名其妙崩）。"""
    if not get_llm_config()["api_key"]:
        print("[配置提醒] 还没配置大模型密钥。两种配法任选其一：")
        print("          1) 复制 .env.example 为 .env，在里面填 DEEPSEEK_API_KEY")
        print("          2) 启动后进管理控制台的「API 配置」卡片在线填（不用重启）")
        print("[配置提醒] 没有 Key 时，接口会返回友好提示，不会崩溃。")
