import os
import re
import json
from typing import Optional, Any
from pathlib import Path
from dotenv import dotenv_values

# 基础目录配置（始终使用绝对路径）
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "output"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 缓存与输出文件路径
DISK_CACHE_FILE = CACHE_DIR / "disk_tree_cache.json"
SCAN_CHECKPOINT_FILE = CACHE_DIR / "scan_checkpoint.json"
SUBTIER_CACHE_FILE = CACHE_DIR / "subtier_plan_cache.json"
PLAN_MARKDOWN_FILE = OUTPUT_DIR / "网盘整理规划方案.md"
PLAN_CSV_FILE = OUTPUT_DIR / "organize_plan.csv"
UNDO_HISTORY_FILE = OUTPUT_DIR / "undo_history.json"
EXECUTION_LOG_FILE = OUTPUT_DIR / "execution.log"
CLASSIFICATION_LOG_FILE = OUTPUT_DIR / "classification.log"

# 大模型配置（优先从 .env 或 newapi_key.env 读取，支持环境变量）
ENV_FILE = BASE_DIR / "newapi_key.env"
if not ENV_FILE.exists():
    ENV_FILE = BASE_DIR / ".env"
_env_vars = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}

LLM_BASE_URL = os.getenv("LLM_BASE_URL") or _env_vars.get("DEFAULT_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY") or _env_vars.get("DEFAULT_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL") or _env_vars.get("GPT_Model") or _env_vars.get("DEFAULT_MODEL", "gpt-4o-mini")

# 本地 Kev 决策模型配置 (从 kev.env 读取，优先使用 0.5b)
KEV_ENV_FILE = BASE_DIR / "kev.env"
_kev_env = dotenv_values(KEV_ENV_FILE) if KEV_ENV_FILE.exists() else {}
KEV_BASE_URL = os.getenv("KEV_BASE_URL") or _kev_env.get("KEV_BASE_URL", "http://127.0.0.1:8008")
KEV_MODEL_NAME = os.getenv("KEV_MODEL_NAME") or _kev_env.get("KEV_LIGHT_MODEL", "0.5b")

# AList 与 WebDAV 配置
ALIST_PORT = 5244
ALIST_URL = f"http://localhost:{ALIST_PORT}"
WEBDAV_BASE_URL = f"http://localhost:{ALIST_PORT}/dav"
WEBDAV_USERNAME = os.getenv("WEBDAV_USERNAME", "admin")
WEBDAV_PASSWORD = os.getenv("WEBDAV_PASSWORD", "admin")

# 百度网盘在 AList 中的挂载路径前缀（用户在 AList 中挂载为 /baiduq）
ALIST_MOUNT_PATH = os.getenv("ALIST_MOUNT_PATH", "/baiduq")

# 防风控与速率限制配置（单线程随机延时，安全防爬）
REQUEST_MIN_DELAY = float(os.getenv("REQUEST_MIN_DELAY", "1.0"))  # 基础最小随机延时 (秒)
REQUEST_MAX_DELAY = float(os.getenv("REQUEST_MAX_DELAY", "2.5"))  # 基础最大随机延时 (秒)
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))                  # 网络断线/重试次数
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "3.0"))          # 指数退避基数 (秒)

# 隔离归档根目录（所有建议删除、超龄资料、查重副本均放入此目录，绝不物理删除）
ISOLATION_ROOT_DIR = "_待清理隔离区"
DELETE_TARGET_DIR = os.getenv("DELETE_TARGET_DIR", f"/{ISOLATION_ROOT_DIR}/03_待删除")

# 目标归档顶层大纲体系
DEFAULT_TAXONOMY = {
    "01_孩子学习": {
        "小学": {
            "数学": ["常规课程", "竞赛培优(如AMC8/高思/学而思)"],
            "语文": ["课内阅读与写作", "国学古诗文"],
            "英语": ["原版阅读与分级", "新概念与语法体系", "听力口语"],
            "科学与编程": ["少儿编程", "科学启蒙"],
            "历史与地理": ["历史故事", "地理通识"],
            "兴趣与美育": ["美术绘画", "手工折纸", "乐理艺术"]
        },
        "初中与进阶": ["小升初", "初中各科", "高中辅导"],
        "家庭教育与育儿方法": ["正面管教", "家庭育儿指南"]
    },
    "02_儿童听读与娱乐": [
        "名著评书(三国演义/西游记/水浒等)",
        "成语童话与神话音频",
        "儿童动画片与启蒙音像"
    ],
    "03_工作与研究": [
        "量化投资与金工研报",
        "行业研究与策略报告",
        "学术论文与工作文档"
    ],
    "04_影视与音像": [
        "电影与剧集",
        "音乐与音频",
        "音效与音频素材"
    ],
    "05_个人生活与备份": [
        "相册与多媒体",
        "软件与工具",
        "微信与系统备份"
    ],
    "06_其他未归类": [
        "待观察或未识别内容"
    ]
}


def parse_llm_json(raw_text: Optional[str]) -> Any:
    """从大模型原始回复中稳健提取并解析 JSON 对象或列表（支持容错与代码块截取）"""
    if not raw_text:
        return {}
    text = raw_text.strip()
    # 1. 优先匹配代码块
    code_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if code_match:
        text = code_match.group(1).strip()
    else:
        # 2. 截取最外层 JSON 结构
        brace_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if brace_match:
            text = brace_match.group(1).strip()

    try:
        return json.loads(text)
    except Exception:
        # 3. 容错清理尾部逗号
        cleaned = re.sub(r",\s*([\}\]])", r"\1", text)
        return json.loads(cleaned)
