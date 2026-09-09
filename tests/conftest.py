"""pytest 全局配置：为无 GUI / 缺依赖的环境补充最小占位模块。

正常环境下（已 `pip install -r requirements.txt`）这些库真实存在，本文件**不做任何注入**；
只有在导入失败时才插入最小占位，保证纯逻辑单测能在 CI / 无界面环境下运行。

说明：core/platform.py 会 import pyperclip / pyautogui（GUI 自动化依赖），
而 donate 等纯逻辑模块的单测并不真正需要它们。
"""

import sys
import types


def _install_stub(name, **attrs):
    """仅当模块确实缺失时才注册占位，避免覆盖真实安装的版本。"""
    try:
        __import__(name)
        return False  # 真实模块存在，不注入
    except ImportError:
        pass
    mod = types.ModuleType(name)
    for key, val in attrs.items():
        setattr(mod, key, val)
    sys.modules[name] = mod
    return True


_install_stub("pyperclip", copy=lambda *a, **kw: None, paste=lambda: "")

_install_stub(
    "pyautogui",
    size=lambda: (1920, 1080),
    screenshot=lambda *a, **kw: None,
    click=lambda *a, **kw: None,
    hotkey=lambda *a, **kw: None,
    press=lambda *a, **kw: None,
)

# core/engine.py 顶层 import easyocr / pynput，占位只需让 import 成功
_install_stub("easyocr", Reader=object)

try:
    import pynput  # noqa: F401
    import pynput.mouse  # noqa: F401
except ImportError:
    class _Listener:
        """最小占位：支持 with 语法即可。"""

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    _mouse_mod = types.ModuleType("pynput.mouse")
    _mouse_mod.Listener = _Listener
    _pynput_mod = types.ModuleType("pynput")
    _pynput_mod.mouse = _mouse_mod
    sys.modules["pynput"] = _pynput_mod
    sys.modules["pynput.mouse"] = _mouse_mod
