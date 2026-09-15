"""双平台兼容层：屏蔽 macOS / Windows 在窗口激活、剪贴板、模拟按键、
截图、权限检查上的差异。上层业务只调用这里的函数，不直接 import pyautogui。

Windows 侧窗口激活优先使用 pygetwindow（内部依赖 pywin32），
若未安装则降级为「点击屏幕顶部让微信获得焦点」。
"""

import os
import re
import subprocess
import sys
import time

import pyperclip
import pyautogui

from . import config

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# 粘贴快捷键：mac 用 command，win 用 ctrl
PASTE_MODIFIER = "command" if IS_MAC else "ctrl"


def screen_size():
    """当前屏幕（主屏）逻辑尺寸 (width, height)。"""
    return pyautogui.size()


def screenshot(region=None):
    """截取指定区域（或全屏）图像，返回 PIL.Image。"""
    return pyautogui.screenshot(region=region)


def copy(text):
    """复制文本到剪贴板。"""
    pyperclip.copy(text)


def clear_clipboard():
    """清空系统剪贴板。

    pyperclip 没有 clear 接口，写入空字符串即等效清空
    （macOS 走 pbcopy / Windows 走剪贴板 API，均会覆盖原有内容）。
    """
    try:
        pyperclip.copy("")
    except Exception as e:
        print(f"清空剪贴板失败: {e}")


def click(x, y):
    """模拟鼠标左键点击（逻辑坐标）。"""
    pyautogui.click(x, y)


def press_key(key):
    """模拟按一个键（如 esc 关闭表情面板）。"""
    pyautogui.press(key)


def _activate_mac():
    settings = config.get_settings()
    subprocess.run(
        ["osascript", "-e",
         f'tell application id "{settings.wechat_bundle_id}" to activate'],
        timeout=5, check=False,
    )


def _activate_win():
    settings = config.get_settings()
    try:
        import pygetwindow as gw
    except ImportError:
        gw = None

    if gw is not None:
        wins = gw.getWindowsWithTitle(settings.wechat_window_title)
        if wins:
            win = wins[0]
            try:
                if win.isMinimized:
                    win.restore()
                win.activate()
                return
            except Exception:
                pass
    # 兜底：点一下屏幕顶部中间，把前台焦点交还给（通常已打开的）微信窗口
    w, _ = screen_size()
    click(w // 2, 10)


def _do_activate():
    """执行一次「把微信切到最前面」的动作（不等待、不校验）。"""
    try:
        if IS_MAC:
            _activate_mac()
        elif IS_WIN:
            _activate_win()
    except Exception as e:
        print(f"激活微信窗口失败: {e}")


def activate_wechat():
    """无条件把微信切到最前面（旧接口，不带校验）。"""
    _do_activate()
    time.sleep(0.5)


# ---------- 前台窗口校验：确保「粘贴 / 点击」落在微信上 ----------
#
# 历史教训：send_message 只负责把微信「叫到前面」，却从不检查有没有叫成功。
# 一旦失败（微信最小化、系统慢了、权限被拒），后面的 ⌘V + 回车就整个落到
# 当时最前面的窗口上——用户正在用的编辑器/聊天工具会把内容收下来。
# 更糟的是发送后 OCR 核验必然失败，程序判定「没发出去」而反复重试，
# 于是变成「窗口被反复抢来抢去 + 内容一遍遍灌进别人的窗口」。
# 因此所有会「往微信里写」的操作，动手前都必须先确认微信真的在最前面。

# macOS：查询当前最前面 App 的 bundle id（经 System Events，需要「辅助功能」权限）
_FRONTMOST_APPLESCRIPT = (
    'tell application "System Events" to get bundle identifier of '
    'first application process whose frontmost is true'
)

WECHAT_FRONT_TIMEOUT = 2.0   # 激活后最多等多久确认微信到了最前面（秒）
WECHAT_FRONT_POLL = 0.15     # 确认过程的轮询间隔（秒）


def frontmost_app_id():
    """当前最上面那个 App 的标识：macOS 返回 bundle id，Windows 返回窗口标题。

    无法获取（不支持的平台 / 命令失败 / 权限不足）一律返回 None，
    调用方按「无法确认」处理——绝不假设「应该没问题」。
    """
    if IS_MAC:
        return _frontmost_mac()
    if IS_WIN:
        return _frontmost_win()
    return None


# lsappinfo 的输出形如： "CFBundleIdentifier"="com.tencent.xinWeChat"
_LSAPPINFO_BUNDLEID_RE = re.compile(r'"CFBundleIdentifier"\s*=\s*"([^"]+)"')


def _frontmost_mac():
    """macOS：当前最前面 App 的 bundle id（拿不到返回 None）。

    优先 `lsappinfo`（系统自带、**不需要任何权限**，秒回）；
    失败才退回 AppleScript —— 后者要「辅助功能」权限，没授权就查不到。
    两条路都走不通时返回 None，由调用方按「无法确认」处理。
    """
    try:
        front = subprocess.run(["lsappinfo", "front"], capture_output=True,
                               text=True, timeout=3, check=False)
        asn = (front.stdout or "").strip()
        if front.returncode == 0 and asn:
            info = subprocess.run(
                ["lsappinfo", "info", "-only", "bundleid", asn],
                capture_output=True, text=True, timeout=3, check=False)
            match = _LSAPPINFO_BUNDLEID_RE.search(info.stdout or "")
            if match:
                return match.group(1)
    except Exception:
        pass

    try:
        out = subprocess.run(["osascript", "-e", _FRONTMOST_APPLESCRIPT],
                             capture_output=True, text=True, timeout=3, check=False)
        if out.returncode == 0:
            return (out.stdout or "").strip() or None
    except Exception:
        pass
    return None


def _frontmost_win():
    """Windows：当前前台窗口标题（拿不到返回 None）。

    用 ctypes 直接调 user32，不依赖 pygetwindow/pywin32。
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or None
    except Exception:
        return None


def is_wechat_frontmost():
    """微信是否已经在最前面：True / False；无法判断返回 None。"""
    front = frontmost_app_id()
    if front is None:
        return None
    settings = config.get_settings()
    if IS_MAC:
        return front == settings.wechat_bundle_id
    return settings.wechat_window_title in front


def ensure_wechat_front(allow_activate=None, timeout=WECHAT_FRONT_TIMEOUT):
    """确保接下来的粘贴/点击会落到微信上。

    返回 True 表示「已确认微信在最前面，可以安全操作」；
    返回 False 表示「无法确认」——调用方**必须放弃**本次操作，
    宁可这条回复不发，也绝不把它写进别的窗口。

    - 已经在前台时直接返回，不再重复激活（减少无谓的窗口跳来跳去）。
    - settings.verify_wechat_foreground=False 时退化为旧行为（只激活不校验）。
    """
    settings = config.get_settings()
    if allow_activate is None:
        allow_activate = settings.auto_activate_wechat

    if not settings.verify_wechat_foreground:
        if allow_activate:
            _do_activate()
            time.sleep(0.5)
        return True

    if is_wechat_frontmost() is True:
        return True
    if not allow_activate:
        return False

    _do_activate()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(WECHAT_FRONT_POLL)
        if is_wechat_frontmost() is True:
            return True
    return False


def send_message(text):
    """发送一条消息：确认微信在最前面 -> 复制 -> 粘贴 -> 回车。

    返回 True 表示按键已发出（不代表对方一定收到）；
    返回 False 表示无法确认微信在前台，**已放弃本次发送**。
    """
    if not ensure_wechat_front():
        print("[安全] 无法确认微信在最前面，已放弃本次发送"
              "（避免内容被粘贴到其它窗口）。检查两件事：\n"
              "       ① 微信窗口是否被最小化或隐藏；\n"
              "       ② 系统设置 > 隐私与安全性 > 辅助功能，"
              "是否已勾选运行本程序的终端 / IDE。")
        return False
    copy(text)
    time.sleep(0.3)
    pyautogui.hotkey(PASTE_MODIFIER, "v")
    time.sleep(0.3)
    pyautogui.press("enter")
    return True


def click_in_wechat(x, y, allow_activate=None):
    """只在确认微信在最前面时才点击（否则点击会落到别的窗口上）。

    返回是否真的点了。切换会话、点表情面板都必须走这里。
    """
    if not ensure_wechat_front(allow_activate=allow_activate):
        print(f"[安全] 无法确认微信在最前面，已跳过本次点击 ({x:.0f}, {y:.0f})")
        return False
    click(x, y)
    return True


def open_file(path):
    """用系统默认程序打开文件/图片（用于打开付款码预览）。

    - macOS   : open <path>
    - Windows : os.startfile
    - 其他    : xdg-open <path>

    打开失败（无 GUI / 命令缺失）时由调用方降级处理，这里只保证不抛异常到主流程之外。
    """
    try:
        if IS_MAC:
            subprocess.run(["open", str(path)], timeout=10, check=False)
        elif IS_WIN:
            os.startfile(str(path))
        else:
            subprocess.run(["xdg-open", str(path)], timeout=10, check=False)
    except Exception as e:
        print(f"打开文件失败: {e}")


def check_screen_permission():
    """启动时自检截图权限。

    - macOS：需「屏幕录制」权限，未授予则给出明确的修复指引。
    - Windows：无此权限模型，直接返回 True。
    """
    if IS_WIN:
        return True
    try:
        screenshot(region=(0, 0, 50, 50))
        return True
    except Exception:
        print("=" * 60)
        print("错误：无法进行屏幕截图（缺少「屏幕录制」权限）！")
        print("解决方法：")
        print("  1. 打开 系统设置 > 隐私与安全性 > 屏幕录制")
        print("  2. 勾选你运行本脚本的 App（终端 / iTerm / VS Code / PyCharm 等）")
        print("  3. 若列表中没有该 App，点 + 号手动添加")
        print("  4. 授权后必须【完全退出并重新打开】该终端/IDE 再运行脚本")
        print("  提示：macOS 14/15 会定期弹窗要求重新确认此权限，留意弹窗。")
        print("=" * 60)
        return False
