"""表情包配置窗口（界面层）测试：映射列表可滚动 + 行遍历不受容器改动影响。

只在有可用 Tk 显示环境时运行；无界面环境（CI）自动跳过。
核心依赖 pynput / pyautogui 等由 tests/conftest.py 的占位模块兜底。
"""

import time

import pytest

tk = pytest.importorskip("tkinter")

from core import stickers  # noqa: E402
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
