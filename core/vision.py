"""视觉模型接入：理解对方发来的表情包 / 图片。

主对话模型（DeepSeek）没有视觉能力，所以这里用独立的 OpenAI 兼容
视觉模型（.env 中 VISION_API_KEY / VISION_BASE_URL / VISION_MODEL）。

设计要点：
- 相同图片只分析一次：按图像内容 md5 缓存到 config/sticker_cache.json。
- 未配置视觉模型 / 调用失败时安全降级返回 (None, None)，主流程照常
  用纯文本方式处理，绝不中断回复。
- 返回的情绪词用于日志与后续映射，不强制与发送映射一致。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re

from . import config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(BASE_DIR, "config", "sticker_cache.json")
CACHE_MAX_ENTRIES = 500

# 视觉模型可选的情绪集合（发送映射里的情绪会合并进来一起供模型选择）
BASE_EMOTIONS = ["肯定", "否定", "开心", "难过", "惊讶", "愤怒", "委屈", "搞笑", "无语"]

_EMOTION_RE_TEMPLATE = r"情绪[:：]\s*(\w+)"


def _load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(cache):
    """超出容量时丢弃最早的条目（dict 保序，按插入顺序近似 LRU）。"""
    try:
        if len(cache) > CACHE_MAX_ENTRIES:
            for key in list(cache.keys())[: len(cache) - CACHE_MAX_ENTRIES]:
                cache.pop(key, None)
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp_path = f"{CACHE_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
        os.replace(tmp_path, CACHE_PATH)
    except Exception as e:
        print(f"保存表情包分析缓存失败：{e}")


def _image_bytes(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _vision_ready():
    s = config.get_settings()
    return bool(s.vision_api_key and s.vision_base_url and s.vision_model)


def _candidate_emotions():
    """可供视觉模型选择的情绪 = 内置集合 + 发送映射里的自定义情绪。"""
    try:
        from . import stickers
        extra = list(stickers.get_emotion_map().keys())
    except Exception:
        extra = []
    seen, result = set(), []
    for e in BASE_EMOTIONS + extra:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result


def analyze_sticker(img):
    """分析表情包/图片的含义与情绪。

    返回 (desc, emotion)：desc 为不超过 20 字的含义概括，emotion 为情绪词。
    未配置视觉模型 / 调用失败返回 (None, None)。
    """
    if img is None:
        return None, None
    try:
        data = _image_bytes(img)
    except Exception:
        return None, None

    key = hashlib.md5(data).hexdigest()
    cache = _load_cache()
    if key in cache:
        entry = cache[key]
        return entry.get("desc"), entry.get("emotion")

    if not _vision_ready():
        print("提示：未配置视觉模型（VISION_API_KEY/VISION_BASE_URL/VISION_MODEL），"
              "无法理解表情包含义，按普通消息处理")
        return None, None

    s = config.get_settings()
    emotions = "、".join(_candidate_emotions())
    prompt = (
        "这是微信聊天里的一张表情包或图片。请判断它表达的情绪和含义，"
        f"严格按如下格式回答（含义不超过20字）：\n情绪：<从这些里选一个：{emotions}>\n"
        "含义：<简短概括>")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=s.vision_api_key, base_url=s.vision_base_url)
        resp = client.chat.completions.create(
            model=s.vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": "data:image/png;base64,"
                                          + base64.b64encode(data).decode()}},
                ],
            }],
            max_tokens=200,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"视觉模型分析表情包失败：{e}")
        return None, None

    emotion, desc = None, None
    m = re.search(_EMOTION_RE_TEMPLATE, text)
    if m:
        emotion = m.group(1)
    m = re.search(r"含义[:：]\s*(.+)", text)
    if m:
        desc = m.group(1).strip()[:30]
    if not desc and text:
        desc = text[:20]  # 模型没按格式时退化为截断原文

    cache[key] = {"desc": desc, "emotion": emotion}
    _save_cache(cache)
    return desc, emotion
