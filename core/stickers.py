"""表情包功能：情绪 → 自定义表情面板格子映射、发送、AI 标记解析。

设计要点：
- 「含义 → 表情」不做运行时分析：用户在 config/stickers.json 里手动标定
  「情绪 → 面板第几个」的映射（emotion_map），AI 只负责在合适的语境下
  返回 [表情:情绪] 标记，这里负责解析并按标定坐标真实发送。
- 面板坐标由 GUI「表情包配置 → 标定面板」采集 4 个点击生成：
  笑脸按钮 / 面板第一行第一格中心 / 再次点笑脸按钮重开面板 / 第一行第二格中心
  （点击格子表情后面板会自动关闭，因此两次格子点击之间要重开一次面板）。
  网格按方格假设推算（纵向前进步长 = 横向间距），columns 可在配置中调整。
- 所有函数失败只 print 降级，绝不抛异常打断主回复流程。
"""

from __future__ import annotations

import json
import os
import re
import time

from . import config
from . import platform

# 项目根目录与配置文件（与 donate.py 同样的定位方式）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STICKERS_PATH = os.path.join(BASE_DIR, "config", "stickers.json")

# 打开表情面板 / 表情动画的等待时间（秒）
PANEL_OPEN_DELAY = 0.8
STICKER_CLICK_DELAY = 0.6

# [表情:开心] / [表情：开心]（情绪为空也匹配，便于把残缺标记从文本中剔除）
_TAG_RE = re.compile(r"\s*\[表情[:：]\s*([^\]\[\s]*)\s*\]")


# ===== 配置读写 =====

def load_config(force=False):
    """读取 stickers.json；文件缺失/损坏返回空骨架（不崩溃）。"""
    try:
        if force or not hasattr(load_config, "_cache"):
            with open(STICKERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            load_config._cache = data if isinstance(data, dict) else {}
        return load_config._cache
    except Exception:
        return {}


def save_config(data):
    """原子写回 stickers.json 并刷新缓存。成功返回 True。"""
    try:
        os.makedirs(os.path.dirname(STICKERS_PATH), exist_ok=True)
        tmp_path = f"{STICKERS_PATH}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STICKERS_PATH)
        load_config._cache = data
        return True
    except Exception as e:
        print(f"保存表情包配置失败：{e}")
        return False


def get_emotion_map():
    """情绪 → 面板第几个 的映射（缺失/类型不对的条目自动过滤）。"""
    raw = load_config().get("emotion_map") or {}
    result = {}
    for emotion, idx in raw.items():
        try:
            n = int(idx)
            if n >= 1:
                result[str(emotion)] = n
        except (TypeError, ValueError):
            continue
    return result


def get_panel():
    """面板标定信息（smiley/cell1/cell2 坐标 + columns），未标定返回 None。"""
    panel = load_config().get("panel")
    if not isinstance(panel, dict):
        return None
    required = ("smiley", "cell1", "cell2")
    if not all(isinstance(panel.get(k), (list, tuple)) and len(panel[k]) == 2
               for k in required):
        return None
    return panel


def stickers_ready():
    """表情包发送是否就绪：功能开 + 有映射 + 面板已标定。"""
    return (config.get_settings().enable_stickers
            and bool(get_emotion_map()) and get_panel() is not None)


# ===== AI 标记解析 =====

def parse_emotion_tag(text):
    """解析 AI 回复中的 [表情:XX] 标记。

    返回 (去掉标记后的纯文本, 情绪或 None)。标记可出现在任意位置。
    """
    text = text or ""
    m = _TAG_RE.search(text)
    if not m:
        return text.strip(), None
    emotion = m.group(1).strip()
    clean = _TAG_RE.sub("", text).strip()
    return clean, emotion or None


def sticker_prompt_suffix():
    """注入 system prompt 的表情包使用说明（功能关/无映射/未标定时为空）。"""
    if not config.get_settings().enable_stickers:
        return ""
    emotion_map = get_emotion_map()
    if not emotion_map or get_panel() is None:
        return ""
    emotions = "、".join(emotion_map.keys())
    return ("\n\n你每次回复都要在回复的最末尾加上标记"
            f" [表情:情绪]，情绪只能从这些里选：{emotions}。"
            "表情包与当前语境情绪匹配时才用，情绪合适时就加；"
            "标记会被系统解析并以表情包发出，不会显示给对方。")


# ===== 坐标推算与发送 =====

def index_to_point(index, panel):
    """面板第 index 格（1 起始，先行后列）的屏幕坐标。

    横向步长 = cell2 - cell1（第一行相邻两格）；纵向步长按方格假设 = 横向步长。
    """
    columns = int(panel.get("columns") or 8)
    dx = panel["cell2"][0] - panel["cell1"][0]
    dy = dx  # 方格假设
    col = (index - 1) % columns
    row = (index - 1) // columns
    return panel["cell1"][0] + col * dx, panel["cell1"][1] + row * dy


def send_sticker(emotion):
    """按映射发送表情包：点笑脸开面板 → 点对应格子（面板随后自动关闭）。

    成功返回 True；功能未就绪 / 找不到映射 / 操作异常返回 False（只打日志）。
    """
    if not config.get_settings().enable_stickers:
        return False
    emotion_map = get_emotion_map()
    panel = get_panel()
    if not emotion_map or panel is None:
        print("表情包功能未标定（缺映射或面板坐标），跳过发送")
        return False
    index = emotion_map.get(emotion)
    if index is None:
        print(f"情绪「{emotion}」没有配置表情映射，跳过发送")
        return False
    try:
        x, y = index_to_point(index, panel)
        platform.click(*panel["smiley"])
        time.sleep(PANEL_OPEN_DELAY)
        platform.click(x, y)  # 点击格子后面板自动收起，无需再按 Esc
        time.sleep(STICKER_CLICK_DELAY)
        print(f"已发送表情包：{emotion}（面板第 {index} 格）")
        return True
    except Exception as e:
        print(f"发送表情包失败：{e}")
        return False
