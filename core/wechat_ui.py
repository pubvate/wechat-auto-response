"""微信界面识别（纯函数，无副作用）。

提供 OCR、红点角标、高亮行、消息归属等识别能力。所有函数只做「看图识图」，
不包含鼠标点击/截图等副作用（那些由 platform 与 engine 负责），便于单元测试。
"""

import re

import cv2
import numpy as np

from . import config
from .utils import preprocess_image


def read_results(reader, img):
    """对截图做预处理和 OCR，返回 easyocr 原始结果 [(bbox, text, prob), ...]。"""
    processed_img = preprocess_image(img)
    return reader.readtext(np.array(processed_img))


def has_green_bubble(chat_img, top, bottom, min_pixels=200):
    """检测指定行范围内是否存在微信"我方消息"的绿色气泡。

    微信我方气泡是标志性的绿色（浅色主题 #95EC69，深色主题偏深的绿），
    在 HSV 空间检测绿色 hue 段即可，比文字位置判断更可靠。
    """
    try:
        arr = np.array(chat_img.convert("RGB"))
        top = max(0, int(top))
        bottom = min(arr.shape[0], int(bottom) + 1)
        if bottom <= top:
            return False
        strip = arr[top:bottom]
        hsv = cv2.cvtColor(strip, cv2.COLOR_RGB2HSV)
        # OpenCV 的 hue 范围是 0-179；微信绿 hue 约 50（深色主题略深）
        mask = cv2.inRange(hsv, (35, 80, 100), (75, 255, 255))
        return int(np.count_nonzero(mask)) >= min_pixels
    except Exception:
        return False


def get_last_message_with_side(results, chat_img):
    """取聊天窗口最底部（最新）的一条消息，并判断发送方。

    返回 (消息文本, 发送方)，发送方取值：
      - "friend": 朋友发的消息（靠左，头像在左侧）
      - "self":   我方消息（靠右，绿色气泡；包括 AI 自动回复发出的）
      - "unknown": 无法判断（居中的时间戳/系统提示等）

    判定规则（按优先级）：
      1. 最后一条消息文本附近检测到绿色气泡 -> self（最可靠；
         只探测最后一条消息自身，不扫上方相邻消息的绿色气泡）
      2. 文本块左边缘在窗口左侧 32% 以内 -> friend（左侧头像之后）
      3. 文本块右边缘超过窗口 72% -> self（右侧头像之前）
      4. 其他（居中短文本，如时间戳、撤回提示）-> unknown
    """
    if not results:
        return "", "unknown"
    items = []
    for box, text, _prob in results:
        items.append({
            "top": min(p[1] for p in box),
            "bottom": max(p[1] for p in box),
            "left": min(p[0] for p in box),
            "right": max(p[0] for p in box),
            "text": text,
        })
    items.sort(key=lambda i: i["top"])

    # 从最底部的文本块往上，把同一消息的连续文本块（同一气泡内换行）
    # 并入同一条消息。行间距阈值收紧到 0.3 倍行高：两条相邻消息之间有
    # 气泡内边距、间距明显大于同一消息内的换行行距，若阈值过大会把
    # 上方紧邻的我方消息（绿色气泡）也并进来，导致对方新消息被误判。
    group = [items[-1]]
    for item in reversed(items[:-1]):
        item_height = max(item["bottom"] - item["top"], 1)
        group_top = min(i["top"] for i in group)
        if group_top - item["bottom"] <= 0.3 * item_height:
            group.append(item)
        else:
            break
    group.sort(key=lambda i: (round(i["top"]), i["left"]))
    text = "".join(i["text"] for i in group)

    group_top = min(i["top"] for i in group)
    group_bottom = max(i["bottom"] for i in group)
    group_left = min(i["left"] for i in group)
    group_right = max(i["right"] for i in group)
    w = chat_img.size[0]

    # 1) 绿色气泡 -> 我方消息。只探测**最后一条消息自己的文本块**附近：
    #    文本矩形内部就是气泡背景（绿色气泡包裹文字四周），往上外扩会
    #    触及上方紧邻的我方消息，因此顶部严格用 last.top 不外扩，只往下
    #    略扩一点覆盖气泡底部内边距。绝不使用 group_top——group 可能向上
    #    并进了上一条我方消息，用它会把对方新消息误判成我方消息。
    last = items[-1]
    line_h = max(last["bottom"] - last["top"], 1)
    pad = max(4, int(0.35 * line_h))
    if has_green_bubble(chat_img,
                        last["top"],
                        min(chat_img.size[1], last["bottom"] + pad)):
        return text, "self"
    # 2) 左边缘靠左（跟在左侧头像后） -> 朋友消息
    if group_left <= w * 0.32:
        return text, "friend"
    # 3) 右边缘靠右（顶到右侧头像前） -> 我方消息
    if group_right >= w * 0.72:
        return text, "self"
    # 4) 居中短文本（时间戳 / 系统提示） -> 无法判断
    return text, "unknown"


def ocr_bottom_y(results):
    """所有 OCR 文本块的最大 bottom（截图内像素）；无文本返回 0。"""
    bottom = 0
    for box, _text, _prob in results:
        bottom = max(bottom, max(p[1] for p in box))
    return bottom


BG_DIFF_THRESHOLD = 60  # 与背景色的「三通道绝对差之和」超过此值即视为内容（非空）像素


def estimate_chat_background(chat_img):
    """估计聊天窗口的背景色（浅色主题灰白 / 深色主题深灰，颜色不同）。

    取截图垂直方向最下面、水平方向最中间的那个像素作为背景色——
    该位置位于聊天区底部边缘的空白处，最能代表当前主题的背景色，
    对浅色/深色主题都自适应。
    返回 (r, g, b) 元组。
    """
    arr = np.array(chat_img.convert("RGB"))
    h, w = arr.shape[:2]
    r, g, b = arr[h - 1, w // 2]
    return (int(r), int(g), int(b))


def detect_sticker_region(chat_img, ocr_results=None, padding=20,
                          left_threshold_ratio=0.02):
    """检测朋友发送的表情包区域（锚定「最后一条文字下方、靠左」）。

    前提（用户确认）：聊天框截图**不含双方头像、也不含输入框**。因此朋友
    内容贴左（x≈0），我方内容靠右（x 较大）。

    判定与定位（用户指定）：
      1. 「非空像素」= 与背景色差异 > BG_DIFF_THRESHOLD 的像素（背景色取
         底部中间像素，浅色/深色主题自适应）
      2. 先确定表情包是否存在：最后一条文字 bottom 之下还有内容，且该内容
         靠近左侧（最左边界 < left_threshold_ratio * 图宽）
      3. 有 OCR 文字：只看最后一条文字 bottom + padding 之下的区域；
         无文字：看全图（从底部向上找靠左内容）
      4. 定位：取最靠下的靠左连通域，left=0，right=该块最右，bottom=该块最下

    返回 (x, y, w, h)（截图内像素），没找到返回 None。
    """
    try:
        arr = np.array(chat_img.convert("RGB"))
        h, w = arr.shape[:2]
        bg = estimate_chat_background(chat_img)
        bg_dist = np.abs(arr.astype(np.int16) - np.array(bg, dtype=np.int16)).sum(axis=2)
        fg = (bg_dist > BG_DIFF_THRESHOLD).astype(np.uint8)

        # 有文字：屏蔽最后一条文字及其上方（只看文字下方）；无文字：看全图
        if ocr_results:
            top_start = ocr_bottom_y(ocr_results) + padding
            if top_start >= h:
                return None
            fg[:top_start, :] = 0

        # 形态学：闭运算把表情包内稀疏内容连成整块，开运算去噪
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))

        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        left_threshold = w * left_threshold_ratio
        best = None
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            # 靠左判定：最左边界 < left_threshold_ratio * 图宽
            if x >= left_threshold:
                continue
            # 取最靠下的靠左块（最后一条朋友表情包；无文字时即「从底部找」）
            if best is None or (y + bh) > (best[1] + best[3]):
                best = (x, y, bw, bh)
        if best is None:
            return None
        x, y, bw, bh = best
        # left=0（截图不含头像，朋友内容贴左），right=该块最右，bottom=最下
        return (0, y, x + bw, bh)
    except Exception:
        return None


def detect_red_badges(list_img):
    """检测联系人列表中头像右上角的红色未读角标。

    在 HSV 空间用高饱和度红色做掩码，再按形状/尺寸过滤，
    返回角标中心坐标列表 [(cx, cy, w, h), ...]（截图内像素坐标）。
    """
    hsv = cv2.cvtColor(np.array(list_img), cv2.COLOR_RGB2HSV)
    # 红色 hue 分两段（0-10 和 170-180），要求高饱和度和较高亮度，避开浅红/棕色头像
    mask1 = cv2.inRange(hsv, (0, 140, 140), (10, 255, 255))
    mask2 = cv2.inRange(hsv, (170, 140, 140), (180, 255, 255))
    mask = mask1 | mask2
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    badges = []
    list_w = list_img.size[0]
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        cx, cy = x + w // 2, y + h // 2
        # 角标只出现在左侧头像列的右上角：限制水平位置 + 收紧尺寸范围
        if (10 <= w <= 50 and 10 <= h <= 50 and 0.4 < w / h < 2.5
                and w * h >= 150 and cx <= list_w * 0.45):
            badges.append((cx, cy, w, h))
    return badges


def identify_contact_at(reader, list_img, name_line_y, row_height):
    """OCR 列表行的【联系人名字】区域，尝试匹配白名单。

    联系人行的几何布局（微信 Mac 版）：
      - 名字在第一行，垂直方向只占该行的上 50%
      - 水平方向在头像右侧、时间左侧
    name_line_y 为名字行的中心线（角标中心或整行中心 - 1/4 行高），
    裁剪 [name_line_y - 1/4 行高, name_line_y + 1/4 行高] 正好覆盖上半行。
    返回 (匹配到的白名单名称或 None, 该行识别出的文本列表)。
    """
    w, h = list_img.size
    half_zone = max(10, int(row_height * 0.25))  # 上下各 1/4 行高，合计上 50% 区域
    top = max(0, int(name_line_y) - half_zone)
    bottom = min(h, int(name_line_y) + half_zone)
    x0 = int(w * 0.20)  # 避开最左侧头像
    x1 = int(w * 0.80)  # 避开最右侧时间（时间贴右边缘）
    strip = list_img.crop((x0, top, x1, bottom))
    results = read_results(reader, strip)
    texts = [t for _box, t, _p in results]
    whitelist = config.get_whitelist()
    if not whitelist:
        if config.whitelist_active():
            # 白名单模式已开启但名单为空 = 不自动回复任何人
            return None, texts
        # 未开启白名单模式（从未配置过联系人）= 回复所有人，视为匹配
        return (texts[0] if texts else None), texts
    for name in whitelist:
        for t in texts:
            if name in t or t in name:
                return name, texts
    return None, texts


def detect_selected_row_y(list_img, threshold=10):
    """通过行背景差异检测当前选中（高亮）的联系人行。

    两种主题的选中高亮特征不同，按优先级检测：

    1. **彩色高亮（深色主题）**：选中行整行是高饱和的绿色，而普通行/置顶行
       都是灰色（饱和度≈0）。对每行采样右侧区域的 HSV 饱和度均值，
       「高饱和 + 一行高」的区段即为选中行。实测深色主题绿色行 S≈100+、
       其余行 S<10，区分度极大，完全不受置顶底色干扰。

    2. **灰色高亮（浅色主题）**：选中行比普通行更灰。采样每行右侧区域的
       平均亮度，找与中位数差异的区段。注意**置顶块底色也偏离中位数**且
       行数多（浅色主题置顶块偏灰、深色主题偏亮），顶部搜索框同理，因此：
       - 排除高度超过列表高度 25% 的区段（选中高亮只有一行高）；
       - 在剩余候选里取平均偏差最强的（选中高亮对比度高于置顶底色）。

    返回 (该行中心 y, 高亮带高度≈行高)（截图内像素坐标），
    检测失败返回 (None, None)。
    """
    try:
        w, h = list_img.size
        x0, x1 = int(w * 0.55), int(w * 0.95)  # 取行右侧纯背景区，避开头像和文字
        if x1 <= x0:
            return None, None
        arr = np.array(list_img.convert("RGB"))
        # 选中高亮是完整的一行：上限排除置顶块/搜索栏（远超一行），
        # 下限排除时间/预览文字等文本噪声行（只有文字行高，约整行的 1/4~1/5）
        min_band = max(8, h // 16)
        max_band = h * 0.25

        def _bands(mask):
            """把布尔序列切成连续区段 [start, end) 列表。"""
            bands, start = [], None
            for i, m in enumerate(mask):
                if m and start is None:
                    start = i
                elif not m and start is not None:
                    bands.append((start, i))
                    start = None
            if start is not None:
                bands.append((start, len(mask)))
            return bands

        # 1) 深色主题：绿色选中行 => 高饱和区段（灰色的置顶/普通行饱和度≈0）
        sat = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)[:, x0:x1, 1].mean(axis=1)
        sat_bands = [(s, e) for s, e in _bands(sat > 40)
                     if min_band <= e - s <= max_band]
        if sat_bands:
            s, e = max(sat_bands, key=lambda se: sat[se[0]:se[1]].mean())
            return s + (e - s) // 2, e - s

        # 2) 浅色主题：灰色选中行 => 亮度偏离中位数的区段
        gray = np.array(list_img.convert("L"))
        col = gray[:, x0:x1].mean(axis=1)
        med = np.median(col)
        dev = np.abs(col - med)
        lum_bands = [(s, e) for s, e in _bands(dev > threshold)
                     if min_band <= e - s <= max_band]
        if not lum_bands:
            return None, None
        s, e = max(lum_bands, key=lambda se: dev[se[0]:se[1]].mean())
        return s + (e - s) // 2, e - s
    except Exception:
        return None, None


def estimate_row_height(list_img, list_region, selected_band=None):
    """估算联系人列表单行高度（截图像素）。

    优先用高亮带实测高度；否则按微信 Mac 行高约 64 逻辑点 × Retina 缩放估算。
    """
    scale = list_img.height / list_region[3] if list_region[3] else 2.0
    if selected_band and 20 * scale <= selected_band <= 200 * scale:
        return selected_band
    return 64 * scale


def identify_contact_at_y(reader, list_img, row_center_y, row_height, fallback=None):
    """在列表指定行（给出整行中心 y 和行高）识别联系人名称，失败返回 fallback。

    联系人名字只占行的上 50%，名字行中心 ≈ 整行中心 - 1/4 行高。
    """
    if row_center_y is None:
        return fallback
    name_line_y = row_center_y - row_height * 0.25
    matched, texts = identify_contact_at(reader, list_img, name_line_y, row_height)
    if matched:
        return matched
    return texts[0] if texts else fallback


def get_current_contact(reader, list_img, list_region, fallback=None):
    """通过高亮行 OCR 识别当前打开的会话联系人名称，失败时返回 fallback。"""
    y, band = detect_selected_row_y(list_img)
    return identify_contact_at_y(
        reader, list_img, y, estimate_row_height(list_img, list_region, band), fallback)


# ===== 联系人列表整表 OCR（白名单配置界面用） =====

# 时间/系统栏噪声：纯数字符号组合，或包含这些关键词的文本不是联系人名
_NOISE_RE = re.compile(r"^[\d:：/.\-\s]+$")
_NOISE_KEYWORDS = ("昨天", "前天", "今天", "星期", "周", "上午", "下午", "晚上",
                   "凌晨", "刚刚", "分钟", "小时", "天前", "搜索")


def is_noise_name(text):
    """判断 OCR 文本是否像时间戳/系统栏文字而非联系人名字。"""
    t = (text or "").strip()
    if not t:
        return True
    if _NOISE_RE.match(t):
        return True
    return any(kw in t for kw in _NOISE_KEYWORDS)


def read_all_contact_names(reader, list_img, list_region):
    """OCR 读取联系人列表区域里所有可见联系人的名字。

    行定位：以高亮行为锚点、按行高向上下推算每行中心；没有高亮行时
    从顶部按行高逐步扫描。每行复用 identify_contact_at 的名字条带裁剪
    （行的上 50%、避开最左头像与最右时间）。
    返回去重后的名字列表（识别失败/无文本时可能为空）。
    """
    h = list_img.size[1]
    selected_y, band = detect_selected_row_y(list_img)
    row_h = estimate_row_height(list_img, list_region, band)
    if selected_y is not None:
        # 从选中行往上数出第一行的中心，保证行网格与真实列表对齐
        n_above = int((selected_y - row_h * 0.5) // row_h) + 1
        cy = selected_y - (n_above - 1) * row_h
    else:
        cy = row_h * 0.5
    names, seen = [], set()
    while cy < h - row_h * 0.15:
        if cy > row_h * 0.3:
            _matched, texts = identify_contact_at(
                reader, list_img, cy - row_h * 0.25, row_h)
            for t in texts:
                if not is_noise_name(t) and t not in seen:
                    names.append(t)
                    seen.add(t)
                    break
        cy += row_h
    return names
