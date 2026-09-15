"""handle_badges 点击切换会话链路的单元测试。

回归背景：曾出现「OCR 名字识别对了，却激活了紧邻行的会话」——
根因是角标中心贴近行的顶部边缘，直接以它作为点击 y，在框选区域
有轻微偏移时会点到相邻行。修复：点击行垂直中部 + 点击后核验重试。
"""

import time

import pytest
from PIL import Image

from core import engine, platform, wechat_ui

# 列表区域 (0,0,200,400)（逻辑点），截图为 2x Retina → 400x800 像素
LIST_REGION = (0, 0, 200, 400)
ROW_H = 128    # 行高（截图像素）
BY = 100       # 角标中心 y（截图像素，位于目标行上半部）


class FakeReader:
    def readtext(self, img):
        return []


@pytest.fixture
def env(monkeypatch):
    """替换识别与平台层：角标固定在 (100, BY)，白名单匹配「张三」。

    state["selected_results"] 是 detect_selected_row_y 的预设返回队列
    （依次被：handle_badges 开头 1 次 + 每次核验 1 次消费）。
    """
    img = Image.new("RGB", (400, 800), (255, 255, 255))
    state = {"clicks": [], "selected_results": []}

    monkeypatch.setattr(wechat_ui, "detect_red_badges",
                        lambda im: [(100, BY, 20, 20)])
    monkeypatch.setattr(wechat_ui, "estimate_row_height",
                        lambda im, region, band=None: ROW_H)
    monkeypatch.setattr(wechat_ui, "identify_contact_at",
                        lambda reader, im, y, rh: ("张三", ["张三"]))

    def fake_selected(im, threshold=10):
        assert state["selected_results"], "detect_selected_row_y 调用次数超出预期"
        return state["selected_results"].pop(0), ROW_H  # (y, band)

    monkeypatch.setattr(wechat_ui, "detect_selected_row_y", fake_selected)
    monkeypatch.setattr(platform, "screenshot", lambda region=None: img)
    # 点击统一走 click_in_wechat（内部先确认微信在最前面）；这里默认放行。
    monkeypatch.setattr(platform, "click_in_wechat",
                        lambda x, y, allow_activate=None:
                        (state["clicks"].append((x, y)) or True))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    state["img"] = img
    return state


def test_clicks_row_center_not_badge_center(env):
    """点击 y 应为行的垂直中部（角标中心 + 1/4 行高），而非角标中心。

    行中部离上下行边界最远，框选区域轻微偏移也不会点到相邻行。
    换算：x = 400*0.5/2 = 100；y = (BY + 0.25*ROW_H)/2 = (100+32)/2 = 66。
    """
    env["selected_results"] = [None, BY + ROW_H * 0.25]
    result = engine.handle_badges(FakeReader(), env["img"], LIST_REGION)
    assert result == "张三"
    assert env["clicks"] == [(100, 66)]


def test_click_verified_then_returns_contact(env):
    """点击后核验高亮行已在目标行 -> 返回联系人名，只点一次。"""
    env["selected_results"] = [None, BY + 10]
    result = engine.handle_badges(FakeReader(), env["img"], LIST_REGION)
    assert result == "张三"
    assert len(env["clicks"]) == 1


def test_click_verify_fail_retries_once(env):
    """第一次点击后高亮行不在目标行 -> 自动重试第二次点击。"""
    env["selected_results"] = [None, None, BY + 10]
    result = engine.handle_badges(FakeReader(), env["img"], LIST_REGION)
    assert result == "张三"
    assert len(env["clicks"]) == 2


def test_click_verify_fail_twice_gives_up(env):
    """两次点击后高亮行都不在目标行 -> 放弃本轮（返回 None），绝不误发。"""
    env["selected_results"] = [None, None, None]
    result = engine.handle_badges(FakeReader(), env["img"], LIST_REGION)
    assert result is None
    assert len(env["clicks"]) == 2


def test_badge_on_current_selected_row_skips_click(env):
    """角标所在行就是当前高亮会话 -> 不点击，直接跳过。"""
    env["selected_results"] = [BY]
    result = engine.handle_badges(FakeReader(), env["img"], LIST_REGION)
    assert result is None
    assert env["clicks"] == []


def test_click_skipped_when_wechat_not_frontmost(env, monkeypatch):
    """微信不在最前面 -> 一下都不点（否则会点进别人窗口），放弃本轮。"""
    monkeypatch.setattr(platform, "click_in_wechat",
                        lambda x, y, allow_activate=None: False)
    env["selected_results"] = [None, BY + 10]
    result = engine.handle_badges(FakeReader(), env["img"], LIST_REGION)
    assert result is None
    assert env["clicks"] == []
