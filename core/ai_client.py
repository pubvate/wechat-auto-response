"""AI 接口封装与上下文构建。

- AIClient：OpenAI 兼容接口（DeepSeek 等）的低层封装。
- decide_context_count / build_context_messages：根据新消息动态裁剪上下文，省 token。
"""

import ssl

from openai import OpenAI

from . import config

# 关闭 SSL 证书校验（兼容部分网络环境；生产环境建议改为正确配置 CA）
ssl._create_default_https_context = ssl._create_unverified_context

# 指代词/承接语气：出现这些词说明回复高度依赖上文，需要携带更多历史
_CONTEXT_HINT_TOKENS = (
    "他", "她", "它", "那个", "这个", "之前", "上次", "刚才", "你说",
    "不是", "为什么", "怎么", "呢", "吗", "嗯", "哦", "哈", "？", "?", "。。",
)

_client = None


def get_client():
    """获取（缓存的）OpenAI 兼容客户端实例。"""
    global _client
    if _client is None:
        settings = config.get_settings()
        _client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
    return _client


def chat(system, messages, model=None):
    """调用大模型完成一次对话，返回回复文本。

    :param system: system prompt（人设等）
    :param messages: 上下文消息列表 [{"role": "user"/"assistant", "content": ...}]
    """
    settings = config.get_settings()
    resp = get_client().chat.completions.create(
        model=model or settings.model,
        messages=[{"role": "system", "content": system}] + messages,
        stream=False,
    )
    return resp.choices[0].message.content


def decide_context_count(new_msg, settings=None):
    """根据新消息内容动态决定携带多少条历史上下文。

    - 默认只带最近 context_base_count 条，保证基本连贯、省 token
    - 新消息很短（如"嗯""真的?"）或含指代词/承接语气时，需携带更多历史
    """
    settings = settings or config.get_settings()
    if len(new_msg) <= 6:  # 极短消息几乎必然承接上文
        return settings.context_max_count
    if any(t in new_msg for t in _CONTEXT_HINT_TOKENS):
        return min(settings.context_max_count, settings.context_base_count * 2)
    return settings.context_base_count


def build_context_messages(new_msg, history, settings=None):
    """构建发给 AI 的上下文消息列表（不含 system）。

    从最新往回取，同时受条数上限和字符预算双重限制。
    返回 (messages, 携带条数)。
    """
    settings = settings or config.get_settings()
    role_map = {"friend": "user", "self": "assistant"}
    turns = [(m["role"], m["text"]) for m in history]
    n = decide_context_count(new_msg, settings)
    selected = []
    total_chars = 0
    for role, text in reversed(turns):
        if len(selected) >= n:
            break
        cost = len(text) + 8
        # 超预算时至少保底 2 条，再多就停
        if total_chars + cost > settings.context_token_budget and len(selected) >= 2:
            break
        selected.append((role, text))
        total_chars += cost
    selected.reverse()
    messages = [{"role": role_map[r], "content": t} for r, t in selected]
    messages.append({"role": "user", "content": new_msg})
    return messages, len(selected)
