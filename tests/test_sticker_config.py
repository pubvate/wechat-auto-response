"""表情包配置窗口（界面层）测试：映射列表可滚动 + 行遍历不受容器改动影响。

只在有可用 Tk 显示环境时运行；无界面环境（CI）自动跳过。
核心依赖 pynput / pyautogui 等由 tests/conftest.py 的占位模块兜底。
"""

import time

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import messagebox  # noqa: E402

from core import stickers  # noqa: E402
from core import sticker_config as sc  # noqa: E402
from core.sticker_config import StickerConfigWindow  # noqa: E402

SAMPLE_MAP = {f"情绪{i}": i + 1 for i in range(30)}


def _create_root():
    """创建 Tk 根窗口；本机偶发首次初始化拿不到 Tcl 资源，失败时重试一次。"""
    last_error = None
    for _ in range(2):
        try:
            return tk.Tk()
        except tk.TclError as e:
            last_error = e
            time.sleep(0.2)
    raise last_error


@pytest.fixture
def win(monkeypatch):
    """构造一个不弹出的配置窗口（root 已 withdraw）。"""
    try:
        root = _create_root()
    except Exception as e:  # 无显示环境
        pytest.skip(f"无可用 Tk 显示环境：{e}")
    root.withdraw()
    monkeypatch.setattr(stickers, "get_emotion_map", lambda: dict(SAMPLE_MAP))
    window = StickerConfigWindow(root)
    window.top.geometry("460x480")
    window.top.update()
    yield window
    try:
        root.destroy()
    except tk.TclError:
        pass


def _scrollregion_height(window):
    region = window.scroll.canvas.cget("scrollregion")
    return float(region.split()[3]) if region else 0.0


def test_rows_live_in_scrollable_body(win):
    """行容器是滚动体的内容区，且内容超长时滚动区域确实大于可视区。"""
    assert win.rows_frame is win.scroll.body
    assert len(win.rows_frame.winfo_children()) == len(SAMPLE_MAP)
    assert _scrollregion_height(win) > win.scroll.canvas.winfo_height()


def test_iter_rows_still_parses_all_rows(win):
    """换成滚动容器后，保存逻辑仍能逐行读出全部映射。"""
    assert list(win._iter_rows()) == list(SAMPLE_MAP.items())


def test_adding_row_scrolls_it_into_view(win):
    win._add_row("新情绪", 999)
    win.top.update()
    top, bottom = win.scroll.canvas.yview()
    assert bottom == 1.0  # 新行已经滚到可见的底部
    assert ("新情绪", 999) in list(win._iter_rows())


def test_reload_returns_to_top(win):
    win._scroll_to(1.0)
    win._load_rows()
    assert win.scroll.canvas.yview()[0] == 0.0


def test_action_buttons_stay_outside_scroll_area(win):
    """「添加映射/保存映射」固定在底部，不随列表滚动。"""
    for frame in win.top.winfo_children():
        if frame.winfo_class() != "TFrame":
            continue
        texts = [w.cget("text") for w in frame.winfo_children()
                 if w.winfo_class() == "TButton"]
        if "保存映射" in texts and "＋ 添加映射" in texts:
            has_btns = True
            assert frame not in (win.rows_frame, win.scroll)
    assert has_btns
    # 按钮容器有实际高度，说明它没被滚动区挤掉
    assert win.scroll.canvas.winfo_height() < win.top.winfo_height()


# ---------- 表情面板标定：5 次点击 ----------

def test_calibration_requires_five_clicks():
    """标定步骤明确为 5 步：开面板 → 切自定义 → 第一格 → 重开 → 第二格。"""
    assert StickerConfigWindow.CALIB_CLICKS == 5
    assert len(StickerConfigWindow.CALIB_STEPS) == 5
    assert StickerConfigWindow.CALIB_STEPS == (
        "☺ 表情按钮", "自定义表情分组", "第一行第一格",
        "☺ 表情按钮（重开）", "第一行第二格")


def test_calibration_takes_3rd_and_5th_click_as_cells(win, monkeypatch):
    """第 ③ 下才是第一格、第 ⑤ 下是第二格（① 开面板、② 切页签、④ 重开）。"""
    saved = {}
    monkeypatch.setattr(stickers, "load_config", lambda force=False: {})
    monkeypatch.setattr(stickers, "save_config",
                        lambda data: (saved.update(data), True)[1])
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)

    clicks = [(100, 900),   # ① ☺ 表情按钮
              (140, 700),   # ② 自定义表情分组
              (200, 640),   # ③ 第一行第一格
              (101, 901),   # ④ ☺ 表情按钮（重开）
              (260, 641)]   # ⑤ 第一行第二格
    win._handle_calibration_result(clicks)

    panel = saved.get("panel") or {}
    assert panel.get("smiley") == [100, 900]
    assert panel.get("custom_tab") == [140, 700]
    assert panel.get("cell1") == [200, 640]
    assert panel.get("cell2") == [260, 641]


def test_calibration_rejects_short_sequence(win, monkeypatch):
    """只点了 4 下（漏了切换自定义表情页）不应写入标定。"""
    saved = {}
    warnings = []
    monkeypatch.setattr(stickers, "load_config", lambda force=False: {})
    monkeypatch.setattr(stickers, "save_config",
                        lambda data: (saved.update(data), True)[1])
    monkeypatch.setattr(messagebox, "showwarning",
                        lambda *a, **k: warnings.append(a[1] if len(a) > 1 else ""))

    win._handle_calibration_result([(100, 900), (200, 640),
                                    (101, 901), (260, 641)])

    assert warnings and "5" in warnings[0]
    assert saved == {}
    assert sc.StickerConfigWindow.CALIB_CLICKS == 5
