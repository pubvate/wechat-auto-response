"""wechat_ui 模块测试：红点角标、高亮行、消息归属、联系人识别等识别逻辑。"""

import pytest
from PIL import Image, ImageDraw, ImageFont

from core import wechat_ui


def ocr(text, x, y, w=200, h=36):
    """构造一条 easyocr 结果：[(bbox, text, prob)]"""
    box = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return (box, text, 0.9)


# ===== 红点角标检测 =====
def test_detect_red_badges():
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([10, 20, 90, 100], fill=(120, 180, 250))   # 头像
    d.ellipse([70, 20, 100, 50], fill=(250, 81, 81))     # 角标1
    d.ellipse([10, 140, 90, 220], fill=(120, 180, 250))
    d.ellipse([70, 140, 100, 170], fill=(250, 81, 81))   # 角标2
    d.rectangle([200, 100, 260, 160], fill=(200, 60, 60))  # 列表右侧大块红 -> 应过滤
    badges = wechat_ui.detect_red_badges(img)
    assert len(badges) == 2


def test_detect_red_badges_empty():
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    assert wechat_ui.detect_red_badges(img) == []


# ===== 高亮行检测 =====
def test_detect_selected_row_y():
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([0, 240, 400, 320], fill=(237, 237, 237))
    y, band = wechat_ui.detect_selected_row_y(img)
    assert abs(y - 280) < 5
    assert 78 <= band <= 82  # 高亮带高度（80 上下 1px 的测量误差）


def test_detect_selected_row_y_none():
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    y, band = wechat_ui.detect_selected_row_y(img)
    assert y is None and band is None


def test_detect_selected_row_y_ignores_pinned_block():
    """置顶块（整块浅灰）+ 真正的选中高亮：必须选中高亮行，不能落在置顶块里。

    回归背景：旧逻辑取「最长差异区段」，置顶块行数多、区段长，几乎总是压过
    只有一行高的选中高亮，导致置顶联系人被误识别为当前联系人。
    """
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 400, 256], fill=(240, 240, 240))    # 置顶块 2 行
    d.rectangle([0, 560, 400, 640], fill=(200, 200, 200))  # 选中高亮 1 行
    y, band = wechat_ui.detect_selected_row_y(img)
    assert y is not None
    assert 560 <= y <= 640
    assert 78 <= band <= 82


def test_detect_selected_row_y_pinned_only_returns_none():
    """只有置顶块、没有选中高亮时，不能把置顶块中心误当高亮行。"""
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([0, 0, 400, 256], fill=(240, 240, 240))
    y, band = wechat_ui.detect_selected_row_y(img)
    assert y is None and band is None


def test_detect_selected_row_y_search_bar_does_not_win():
    """顶部搜索框（浅灰窄条）对比度再高也不应抢走高亮行的判定。"""
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 400, 30], fill=(220, 220, 220))     # 搜索框（窄、对比强）
    d.rectangle([0, 400, 400, 480], fill=(203, 203, 203))  # 选中行
    y, _band = wechat_ui.detect_selected_row_y(img)
    assert y is not None
    assert 400 <= y <= 480


def test_detect_selected_row_y_dark_theme_green_highlight():
    """深色主题：选中行是绿色高亮，前几行是置顶（深灰）。

    回归背景：真实截图（前 3 行置顶、第 1 行绿色高亮）曾被识别成第 3 行。
    绿色行饱和度 S≈100+，灰色置顶/普通行 S≈0——饱和度是最强特征，
    且不受置顶块占区域比例的影响（亮度路径在区域较长时会失效）。
    """
    img = Image.new("RGB", (400, 800), (40, 40, 40))       # 普通行深灰
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 400, 256], fill=(60, 60, 60))       # 置顶块 2 行（占 32%）
    d.rectangle([0, 320, 400, 400], fill=(7, 193, 96))     # 绿色选中行（微信品牌绿）
    y, band = wechat_ui.detect_selected_row_y(img)
    assert y is not None
    assert 320 <= y <= 400
    assert 75 <= band <= 85


def test_detect_selected_row_y_text_noise_band_rejected():
    """没有高亮时，时间/预览文字行（窄噪声区段）不应被当成选中行。"""
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((260, 100), "昨天 14:38", fill=(160, 160, 160))  # 右侧区域内的文字
    y, band = wechat_ui.detect_selected_row_y(img)
    assert y is None and band is None


# ===== 消息归属判断 =====
def test_get_last_message_friend():
    img = Image.new("RGB", (800, 600), (237, 237, 237))
    d = ImageDraw.Draw(img)
    d.ellipse([10, 500, 70, 560], fill=(120, 180, 250))       # 左侧头像
    d.rounded_rectangle([80, 505, 420, 555], 10, fill=(255, 255, 255))  # 白气泡
    d.text((92, 512), "你在干嘛呢", fill=(0, 0, 0))
    text, side = wechat_ui.get_last_message_with_side([ocr("你在干嘛呢", 92, 512)], img)
    assert text == "你在干嘛呢"
    assert side == "friend"


def test_get_last_message_self_green():
    img = Image.new("RGB", (800, 600), (237, 237, 237))
    d = ImageDraw.Draw(img)
    d.ellipse([730, 500, 790, 560], fill=(120, 180, 250))
    d.rounded_rectangle([380, 505, 720, 555], 10, fill=(149, 236, 105))  # 绿色气泡
    d.text((392, 512), "在写代码", fill=(0, 0, 0))
    text, side = wechat_ui.get_last_message_with_side([ocr("在写代码", 392, 512)], img)
    assert side == "self"


def test_get_last_message_self_position():
    # 绿色检测失效时，靠右位置兜底判定为我方
    img = Image.new("RGB", (800, 600), (237, 237, 237))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([560, 505, 740, 555], 10, fill=(200, 200, 200))
    d.text((572, 512), "好的", fill=(0, 0, 0))
    text, side = wechat_ui.get_last_message_with_side([ocr("好的", 572, 512, w=80)], img)
    assert side == "self"


def test_get_last_message_unknown_centered():
    img = Image.new("RGB", (800, 600), (237, 237, 237))
    d = ImageDraw.Draw(img)
    d.text((370, 512), "下午2:30", fill=(153, 153, 153))
    text, side = wechat_ui.get_last_message_with_side([ocr("下午2:30", 370, 512, w=90)], img)
    assert side == "unknown"


def test_get_last_message_multiline_merge():
    img = Image.new("RGB", (800, 600), (237, 237, 237))
    d = ImageDraw.Draw(img)
    d.ellipse([10, 430, 70, 490], fill=(120, 180, 250))
    d.rounded_rectangle([80, 435, 500, 555], 10, fill=(255, 255, 255))
    d.text((92, 442), "今天天气不错", fill=(0, 0, 0))
    d.text((92, 486), "出来走走吗", fill=(0, 0, 0))
    text, side = wechat_ui.get_last_message_with_side(
        [ocr("今天天气不错", 92, 442), ocr("出来走走吗", 92, 486)], img)
    assert text == "今天天气不错出来走走吗"
    assert side == "friend"


def test_get_last_message_empty():
    img = Image.new("RGB", (800, 600))
    assert wechat_ui.get_last_message_with_side([], img) == ("", "unknown")


def test_friend_reply_tight_below_my_green_not_misjudged():
    """对方消息紧贴我方绿色气泡下方时，不得把对方新消息误判成我方。

    复现历史 bug：分组合并阈值过大，把我方上一条消息并进「最后一条消息」，
    绿色气泡检测又用整段 group 范围，扫到我方气泡 -> 对方新消息被判 self，
    于是「对方发来消息却显示最后一条为我方消息，无需回复」。
    """
    img = Image.new("RGB", (800, 600), (237, 237, 237))
    d = ImageDraw.Draw(img)
    # 我方上一条绿色气泡（文字 bottom≈473）
    d.rounded_rectangle([380, 430, 720, 480], 10, fill=(149, 236, 105))
    d.text((392, 437), "好的好的", fill=(0, 0, 0))
    # 对方紧贴其下（gap≈12px < 0.5 行高，旧阈值会误合并）的新消息
    d.ellipse([10, 475, 70, 535], fill=(120, 180, 250))
    d.rounded_rectangle([80, 478, 420, 528], 10, fill=(255, 255, 255))
    d.text((92, 485), "你在干嘛呢", fill=(0, 0, 0))

    text, side = wechat_ui.get_last_message_with_side(
        [ocr("好的好的", 392, 437), ocr("你在干嘛呢", 92, 485)], img)

    assert side == "friend"
    assert text == "你在干嘛呢"  # 不把上一条我方消息并入


# ===== 行高估算 =====
def test_estimate_row_height_from_band():
    img = Image.new("RGB", (400, 800))
    # 高亮带 128px 在合理范围内 -> 采信 128
    assert wechat_ui.estimate_row_height(img, (0, 0, 200, 400), 128) == 128


def test_estimate_row_height_default():
    img = Image.new("RGB", (400, 800))  # 2x 缩放
    # 无高亮带 -> 64 逻辑点 × 2x = 128
    assert wechat_ui.estimate_row_height(img, (0, 0, 200, 400), None) == 128


# ===== 联系人识别 =====
class FakeReader:
    def __init__(self, texts):
        self.texts = texts

    def readtext(self, img):
        return [ocr(t, 30, 10) for t in self.texts]


def test_identify_contact_match(monkeypatch):
    monkeypatch.setattr("core.config.get_whitelist", lambda: ["小明", "张三"])
    reader = FakeReader(["小明"])
    img = Image.new("RGB", (400, 800))
    matched, texts = wechat_ui.identify_contact_at(reader, img, 100, 128)
    assert matched == "小明"


def test_identify_contact_no_match(monkeypatch):
    monkeypatch.setattr("core.config.get_whitelist", lambda: ["小明"])
    reader = FakeReader(["陌生人"])
    img = Image.new("RGB", (400, 800))
    matched, texts = wechat_ui.identify_contact_at(reader, img, 100, 128)
    assert matched is None
    assert texts == ["陌生人"]


def test_identify_contact_reply_all(monkeypatch):
    # 未启用白名单模式（从未配置过联系人）= 回复所有人，返回识别到的第一个文本
    monkeypatch.setattr("core.config.get_whitelist", lambda: [])
    monkeypatch.setattr("core.config.whitelist_active", lambda: False)
    reader = FakeReader(["任何人"])
    img = Image.new("RGB", (400, 800))
    matched, texts = wechat_ui.identify_contact_at(reader, img, 100, 128)
    assert matched == "任何人"


def test_identify_contact_active_but_empty_whitelist(monkeypatch):
    # 白名单模式开启但名单为空（联系人全部取消勾选）= 不回复任何人
    monkeypatch.setattr("core.config.get_whitelist", lambda: [])
    monkeypatch.setattr("core.config.whitelist_active", lambda: True)
    reader = FakeReader(["任何人"])
    img = Image.new("RGB", (400, 800))
    matched, texts = wechat_ui.identify_contact_at(reader, img, 100, 128)
    assert matched is None
    assert texts == ["任何人"]


# ===== 整表联系人名字 OCR（白名单配置界面用） =====

def test_is_noise_name():
    assert wechat_ui.is_noise_name("昨天") is True
    assert wechat_ui.is_noise_name("12:30") is True
    assert wechat_ui.is_noise_name("2024/09/08") is True
    assert wechat_ui.is_noise_name("搜索") is True
    assert wechat_ui.is_noise_name("") is True
    assert wechat_ui.is_noise_name("张三") is False
    assert wechat_ui.is_noise_name("AI机器人") is False


def test_read_all_contact_names_rows_and_dedup(monkeypatch):
    """按高亮行锚点逐行扫描：过滤时间噪声、跨行去重。"""
    img = Image.new("RGB", (400, 1024))
    monkeypatch.setattr(wechat_ui, "detect_selected_row_y", lambda img: (600, 128))
    monkeypatch.setattr(wechat_ui, "estimate_row_height", lambda *a, **kw: 128)

    by_center = {344: ["昨天"], 472: ["张三"], 600: ["张三"],
                 728: ["12:30"], 856: ["李四"]}

    def fake_identify(reader, list_img, name_line_y, row_h):
        center = name_line_y + row_h * 0.25
        return None, by_center.get(center, [])

    monkeypatch.setattr(wechat_ui, "identify_contact_at", fake_identify)
    names = wechat_ui.read_all_contact_names(None, img, (0, 0, 200, 512))
    assert names == ["张三", "李四"]


def test_read_all_contact_names_no_highlight(monkeypatch):
    """无高亮行时从顶部按行高扫描，同样能取到名字。"""
    img = Image.new("RGB", (400, 512))
    monkeypatch.setattr(wechat_ui, "detect_selected_row_y", lambda img: (None, None))
    monkeypatch.setattr(wechat_ui, "estimate_row_height", lambda *a, **kw: 128)

    centers = []

    def fake_identify(reader, list_img, name_line_y, row_h):
        centers.append(name_line_y + row_h * 0.25)
        return None, ["王五"] if len(centers) == 2 else []

    monkeypatch.setattr(wechat_ui, "identify_contact_at", fake_identify)
    names = wechat_ui.read_all_contact_names(None, img, (0, 0, 200, 256))
    # 首行中心 = 0.5*128 = 64，第二行 192（王五），第三行 320 超出 512-19.2 循环止
    assert names == ["王五"]
    assert 64 in centers and 192 in centers


# ===== 表情包区域检测（锚定「最后一条文字下方、靠左」） =====

def test_detect_sticker_region_finds_friend_sticker_below_text():
    """朋友表情包：最后一条文字下方、贴左（x≈0），应被检出且位置准确。"""
    img = Image.new("RGB", (800, 1200), (245, 245, 245))
    d = ImageDraw.Draw(img)
    # 上方一条朋友文字（靠左白气泡 + 黑字）
    d.rounded_rectangle([10, 700, 250, 750], 10, fill=(255, 255, 255))
    d.text((20, 707), "给你发个图", fill=(0, 0, 0))
    # 下方朋友表情包：贴左（x=0），彩色块
    d.rectangle([0, 850, 200, 1050], fill=(220, 60, 60))
    ocr_results = [ocr("给你发个图", 20, 700)]
    bbox = wechat_ui.detect_sticker_region(img, ocr_results)
    assert bbox is not None
    x, y, w, h = bbox
    assert x == 0                       # left=0（截图不含头像）
    assert 830 <= y <= 860              # 表情包顶部附近（文字 bottom 之下）
    assert 190 <= w <= 210 and 190 <= h <= 210


def test_detect_sticker_region_ignores_right_side_sticker():
    """我方表情包靠右，不应被检出（只识别朋友的表情包）。"""
    img = Image.new("RGB", (800, 1200), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([10, 700, 250, 750], 10, fill=(255, 255, 255))
    d.text((20, 707), "给你发个图", fill=(0, 0, 0))
    d.rectangle([600, 850, 790, 1050], fill=(220, 60, 60))  # 靠右 = 我方
    ocr_results = [ocr("给你发个图", 20, 700)]
    assert wechat_ui.detect_sticker_region(img, ocr_results) is None


def test_detect_sticker_region_no_content_below_text():
    """文字下方没有靠左内容（只有文字气泡），返回 None。"""
    img = Image.new("RGB", (800, 1200), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([10, 700, 250, 750], 10, fill=(255, 255, 255))
    d.text((20, 707), "在吗", fill=(0, 0, 0))
    ocr_results = [ocr("在吗", 20, 700)]
    assert wechat_ui.detect_sticker_region(img, ocr_results) is None


def test_detect_sticker_region_no_text_anchor_finds_from_bottom():
    """无文字锚点（纯表情包会话）：从底部找靠左内容。"""
    img = Image.new("RGB", (800, 1200), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 900, 200, 1100], fill=(220, 60, 60))  # 底部靠左表情包
    bbox = wechat_ui.detect_sticker_region(img, [])
    assert bbox is not None
    x, y, w, h = bbox
    assert x == 0 and 880 <= y <= 920


def test_detect_sticker_region_blank_chat():
    img = Image.new("RGB", (800, 1200), (245, 245, 245))
    assert wechat_ui.detect_sticker_region(img) is None


def test_estimate_chat_background_light_theme():
    """浅色主题背景色应估计为浅灰白，而非固定值。"""
    img = Image.new("RGB", (800, 1200), (245, 245, 245))
    r, g, b = wechat_ui.estimate_chat_background(img)
    assert r > 200 and g > 200 and b > 200


def test_estimate_chat_background_dark_theme():
    """深色主题背景色应估计为深色。"""
    img = Image.new("RGB", (800, 1200), (30, 30, 31))
    r, g, b = wechat_ui.estimate_chat_background(img)
    assert r < 60 and g < 60 and b < 60


def test_ocr_bottom_y():
    results = [
        ([[10, 10], [50, 10], [50, 30], [10, 30]], "a", 0.9),
        ([[10, 100], [50, 100], [50, 130], [10, 130]], "b", 0.9),
    ]
    assert wechat_ui.ocr_bottom_y(results) == 130
    assert wechat_ui.ocr_bottom_y([]) == 0
