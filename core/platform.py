"""双平台兼容层：屏蔽 macOS / Windows 在窗口激活、剪贴板、模拟按键、
截图、权限检查上的差异。上层业务只调用这里的函数，不直接 import pyautogui。

Windows 侧窗口激活优先使用 pygetwindow（内部依赖 pywin32），
若未安装则降级为「点击屏幕顶部让微信获得焦点」。
"""

import os
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


def activate_wechat():
    """把微信切到最前面，否则粘贴会落到当前前台 App（比如终端）里。"""
    try:
        if IS_MAC:
            _activate_mac()
        elif IS_WIN:
            _activate_win()
        time.sleep(0.5)
    except Exception as e:
        print(f"激活微信窗口失败: {e}")


def send_message(text):
    """发送一条消息：激活微信 -> 复制 -> 粘贴 -> 回车。"""
    settings = config.get_settings()
    if settings.auto_activate_wechat:
        activate_wechat()
    copy(text)
    time.sleep(0.3)
    pyautogui.hotkey(PASTE_MODIFIER, "v")
    time.sleep(0.3)
    pyautogui.press("enter")


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
