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


def check_config() -> None:
    """启动时检查密钥是否配置（人话：没填 Key 就友好提醒，别让程序莫名其妙崩）。"""
    if not DEEPSEEK_API_KEY:
        print("[配置提醒] 还没配置 DEEPSEEK_API_KEY，请复制 .env.example 为 .env 并填入你的 Key。")
        print("[配置提醒] 没有 Key 时，接口会返回友好错误，但不会崩溃。")
