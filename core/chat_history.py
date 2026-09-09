"""聊天记录持久化：按联系人分文件存储，带内存缓存与写穿。

记录格式：[{"role": "friend"|"self", "text": "...", "time": "ISO时间"}, ...]
"""

import json
import os
from datetime import datetime

from . import config
from .utils import sanitize_filename

_history_cache = {}  # 联系人 -> 记录列表（内存缓存，写穿到文件）
MAX_RECORDS = 500    # 单个联系人最多保留的记录条数


def history_path(contact):
    return os.path.join(config.CHAT_HISTORY_DIR, f"{sanitize_filename(contact)}.json")


def load_history(contact):
    """加载联系人聊天记录（带内存缓存）。"""
    if contact in _history_cache:
        return _history_cache[contact]
    msgs = []
    try:
        path = history_path(contact)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    msgs = loaded
    except Exception as e:
        print(f"读取聊天记录失败({contact}): {e}")
    _history_cache[contact] = msgs
    return msgs


def append_history(contact, role, text):
    """追加一条记录并立即写盘。role: "friend" | "self"。

    连续完全相同的记录会被去重（防止 OCR 抖动重复记录）。
    """
    msgs = load_history(contact)
    if msgs and msgs[-1]["role"] == role and msgs[-1]["text"] == text:
        return
    msgs.append({"role": role, "text": text,
                 "time": datetime.now().isoformat(timespec="microseconds")})
    if len(msgs) > MAX_RECORDS:  # 只保留最近若干条，防止文件无限增长
        del msgs[:-MAX_RECORDS]
    _history_cache[contact] = msgs
    try:
        os.makedirs(config.CHAT_HISTORY_DIR, exist_ok=True)
        with open(history_path(contact), "w", encoding="utf-8") as f:
            json.dump(msgs, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"保存聊天记录失败({contact}): {e}")


def already_replied(contact, friend_msg):
    """检查这条朋友消息是否已经回复过（依据聊天记录，重启后依然有效）。

    规则：记录中最后一条 friend 消息与本条相同、且其后存在 self 回复。
    """
    msgs = load_history(contact)
    last_friend_idx = None
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["role"] == "friend":
            last_friend_idx = i
            break
    if last_friend_idx is None:
        return False
    if msgs[last_friend_idx]["text"] != friend_msg:
        return False
    return any(m["role"] == "self" for m in msgs[last_friend_idx + 1:])
