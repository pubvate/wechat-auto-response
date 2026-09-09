"""配置加载：环境变量(.env) + 人设白名单(personas.json)。

设计原则：
- 敏感信息（API key / base_url / model）与行为开关、调参全部走 .env，
  缺失时回退到内置默认值，保证「不配置也能跑」。
- 结构化数据（人设、白名单、联系人→人设映射）保留在 config/personas.json，
  按文件 mtime 缓存，改完文件无需重启即可生效。
"""

import json
import os

try:
    from dotenv import load_dotenv
except ImportError:  # 未安装 python-dotenv 时静默降级，仍能读系统环境变量
    load_dotenv = None

from dataclasses import dataclass

# config.py 位于 core/ 下，BASE_DIR 为项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "personas.json")
CHAT_HISTORY_DIR = os.path.join(BASE_DIR, "chat_history")
MEMORY_FILE = os.path.join(BASE_DIR, "memory.json")

# 人设配置缺失/解析失败时的兜底人设，保证脚本不因配置问题直接挂掉
FALLBACK_SYSTEM_PROMPT = ("让用户感觉你是一个亲切的、句句有回应的、三观正的、暖心的、"
                          "关系比较近的女生朋友，如果看不懂就不要说话，不要答应冒昧的请求，"
                          "回复尽量简短，口语化，不要太过于礼貌而显得生疏")


def _env_bool(name, default):
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


@dataclass
class Settings:
    """运行期配置（来自 .env / 环境变量，带默认值）。"""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"

    dry_run: bool = False             # True = 不调用 AI，回复用占位文本（调试用）
    auto_activate_wechat: bool = True  # 发送回复前自动把微信切到最前面
    show_donate: bool = True           # 主循环做出"回复/不回复"判断后是否打印付款码提示

    # 表情包功能
    enable_stickers: bool = True      # 是否启用表情包回复（AI 自主决定）
    vision_api_key: str = ""          # 视觉模型 API Key（DeepSeek 无视觉，需另配）
    vision_base_url: str = ""         # 视觉模型服务地址
    vision_model: str = ""            # 视觉模型名（GLM-4V / Qwen-VL / GPT-4o 等）

    # 动态上下文
    context_base_count: int = 4       # 默认携带的最近上下文条数
    context_max_count: int = 16       # 强上下文依赖时最多携带的条数
    context_token_budget: int = 600   # 上下文字符预算（中文约 1 字符 ≈ 1 token）

    # 长期记忆
    memory_analyze_hours: int = 24    # 分析时只取最近 N 小时内的聊天记录
    memory_analyze_interval_hours: int = 6  # 触发分析的最短间隔（冷却期）
    memory_inject_reply: bool = True  # 回复时是否注入联系人记忆
    memory_max_chars: int = 400       # 记忆文本长度上限

    # 平台相关
    wechat_bundle_id: str = "com.tencent.xinWeChat"  # macOS 微信 Bundle ID
    wechat_window_title: str = "微信"                # Windows 微信窗口标题（用于定位）

    @classmethod
    def from_env(cls):
        if load_dotenv is not None:
            load_dotenv()  # 从项目根目录 .env 加载（dotenv 会向上查找）
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
            dry_run=_env_bool("DRY_RUN", False),
            auto_activate_wechat=_env_bool("AUTO_ACTIVATE_WECHAT", True),
            show_donate=_env_bool("SHOW_DONATE", True),
            enable_stickers=_env_bool("ENABLE_STICKERS", True),
            vision_api_key=os.getenv("VISION_API_KEY", ""),
            vision_base_url=os.getenv("VISION_BASE_URL", ""),
            vision_model=os.getenv("VISION_MODEL", ""),
            context_base_count=_env_int("CONTEXT_BASE_COUNT", 4),
            context_max_count=_env_int("CONTEXT_MAX_COUNT", 16),
            context_token_budget=_env_int("CONTEXT_TOKEN_BUDGET", 600),
            memory_analyze_hours=_env_int("MEMORY_ANALYZE_HOURS", 24),
            memory_analyze_interval_hours=_env_int("MEMORY_ANALYZE_INTERVAL_HOURS", 6),
            memory_inject_reply=_env_bool("MEMORY_INJECT_REPLY", True),
            memory_max_chars=_env_int("MEMORY_MAX_CHARS", 400),
            wechat_bundle_id=os.getenv("WECHAT_BUNDLE_ID", "com.tencent.xinWeChat"),
            wechat_window_title=os.getenv("WECHAT_WINDOW_TITLE", "微信"),
        )


_settings = None


def get_settings():
    """获取运行期配置（缓存，首次调用时从环境变量加载）。"""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reload_settings():
    """强制重新加载 .env 配置（测试或运行时改环境变量后调用）。"""
    global _settings
    _settings = None
    return get_settings()


# ===== 人设与白名单（personas.json，热更新） =====
_config_cache = {"mtime": None, "data": None}
_config_warned = set()


def _warn_once(key, text):
    if key not in _config_warned:
        _config_warned.add(key)
        print(text)


def load_persona_config(force=False):
    """读取 config/personas.json（按文件 mtime 缓存，改文件后下一轮自动生效）。

    返回字典：
      {
        "personas":        {人设key: {"name": 显示名, "prompt": 人设文本}},
        "default_persona": "默认人设 key",
        "contacts":        {联系人: 人设key},  # 这些联系人同时构成自动回复白名单
      }
    """
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = None
    # mtime 为 None 表示文件不存在，此时不复用缓存（文件可能会被重新创建）
    if (not force and _config_cache["data"] is not None and mtime is not None
            and _config_cache["mtime"] == mtime):
        return _config_cache["data"]

    data = {"personas": {}, "default_persona": "default", "contacts": {},
            "whitelist_mode": False}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        _warn_once("missing",
                   f"警告：未找到配置文件 {CONFIG_PATH}，将使用兜底人设且不限制白名单。")
        raw = {}
    except Exception as e:
        _warn_once("broken",
                   f"警告：配置文件 {CONFIG_PATH} 解析失败（{e}），将使用兜底人设。")
        raw = {}

    # 人设：支持 {"key": {"name":..,"prompt":..}} 或简写 {"key": "人设文本"}
    personas = {}
    for key, val in (raw.get("personas") or {}).items():
        if isinstance(val, str):
            personas[key] = {"name": key, "prompt": val}
        elif isinstance(val, dict) and val.get("prompt"):
            personas[key] = {"name": val.get("name", key), "prompt": val["prompt"]}
    data["personas"] = personas

    # 默认人设：没配或配了个不存在 key 时，退回到第一个人设
    default_key = raw.get("default_persona") or "default"
    if default_key not in personas:
        default_key = next(iter(personas), "default")
    data["default_persona"] = default_key

    # 联系人 -> 人设。值支持三种写法：
    #   "人设key"                              -> 用该人设
    #   null / 不写                            -> 白名单内，用默认人设
    #   {"persona": "key", "enabled": false}   -> enabled=false 时不进白名单（临时停用）
    contacts = {}
    for name, val in (raw.get("contacts") or {}).items():
        if isinstance(val, str) and val:
            contacts[name] = val
        elif isinstance(val, dict):
            if val.get("enabled", True):
                contacts[name] = val.get("persona") or default_key
        else:
            contacts[name] = default_key
    data["contacts"] = contacts

    # 白名单模式：contacts 里存在任意条目（含 enabled=false 的停用条目）即开启。
    # 开启后只有白名单内的联系人才自动回复；未开启（从未配置过联系人）时
    # 保持旧行为：回复所有人。
    data["whitelist_mode"] = bool(raw.get("contacts"))

    _config_cache["mtime"] = mtime
    _config_cache["data"] = data
    return data


def whitelist_active():
    """白名单模式是否启用（personas.json 存在任意 contacts 条目）。"""
    return bool(load_persona_config().get("whitelist_mode"))


def get_whitelist():
    """自动回复白名单（配置文件 contacts 的键）。为空表示回复所有人。"""
    return list(load_persona_config()["contacts"].keys())


def get_persona_key(contact):
    """取得某联系人使用的人设 key；未配置对应关系时用默认人设。"""
    cfg = load_persona_config()
    key = cfg["contacts"].get(contact) or cfg["default_persona"]
    if key not in cfg["personas"]:
        key = cfg["default_persona"]
    return key


def get_system_prompt(contact):
    """取得某联系人对应的 system prompt（人设）。"""
    cfg = load_persona_config()
    persona = cfg["personas"].get(get_persona_key(contact))
    return persona["prompt"] if persona else FALLBACK_SYSTEM_PROMPT


# ===== 人设配置的原始读写（供 GUI 人设编辑器使用） =====

def load_raw_config(path=None):
    """读取 personas.json 的**原始**内容（不规范化、不缓存）。

    与 load_persona_config 的区别：保留 `_说明`、`contacts` 等全部字段，
    便于编辑器「读出来改一处再写回去」，不破坏用户手写的注释和结构。
    文件不存在或解析失败时返回 {}。
    """
    try:
        with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_raw_config(data, path=None):
    """把原始配置写回 personas.json，并刷新人设缓存让改动立即生效。

    采用「先写临时文件再原子替换」，避免写一半崩溃导致配置文件损坏。
    成功返回 True；失败返回 False（错误信息已 print）。
    """
    target = path or CONFIG_PATH
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp_path = f"{target}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target)
    except Exception as e:
        print(f"保存人设配置失败：{e}")
        return False
    if path is None:  # 写的是真实配置文件才需要刷新缓存（测试里写临时文件不干扰）
        load_persona_config(force=True)
    return True
