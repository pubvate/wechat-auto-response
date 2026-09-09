"""「请喝杯咖啡」提示与微信/支付宝付款码展示。

程序**启动**与**退出**时各执行一次：在 console 打印提示文案，并用系统默认
图片查看器打开 img/ 下的微信/支付宝收款码原图。

设计要点：
- 直接打开原图预览（终端真彩字符画在多数终端下显示异常且二维码扫不上，已弃用）。
- 打开动作走 core/platform.open_file，屏蔽 macOS(open) / Windows(startfile) / Linux(xdg-open) 差异。
- 图片按文件名关键字自动识别：含 wechat → 微信，含 alipay → 支付宝（jpg/jpeg/png 均可）。
- 打开失败（无 GUI / 命令缺失）或找不到图片时降级为打印图片路径，绝不抛异常。
- 通过 .env 中 SHOW_DONATE 开关控制（默认开启）。
"""

from __future__ import annotations

from pathlib import Path

from . import config as _cfg
from . import platform as _platform

# 项目根目录与图片目录
BASE_DIR = Path(__file__).resolve().parent.parent
IMG_DIR = BASE_DIR / "img"

# 提示文案（用户给定，不要随意改）
DONATE_MESSAGE = "如果喜欢这个工具，可以请我喝杯咖啡支持我一下吗？你的支持对我很重要。"

_BOX_WIDTH = 60


def _find_image(keyword: str) -> Path | None:
    """在 img/ 下找文件名含 keyword 的图片（不区分大小写，兼容 jpg/jpeg/png）。

    返回首个匹配项；找不到返回 None。
    """
    if not IMG_DIR.is_dir():
        return None
    kw = keyword.lower()
    # glob 大小写敏感，改为遍历目录 + 后缀/文件名小写比较
    try:
        entries = list(IMG_DIR.iterdir())
    except OSError:
        return None
    for p in entries:
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        if kw in p.stem.lower():
            return p
    return None


def _open_image(path: Path) -> bool:
    """用系统默认查看器打开图片；成功返回 True，失败返回 False。"""
    try:
        _platform.open_file(path)
        return True
    except Exception:
        return False


def _show_one(keyword: str, label: str) -> None:
    """打印一张付款码的提示并尝试打开预览；找不到或打不开时降级为打印路径。"""
    path = _find_image(keyword)
    if path is None:
        print(f"  [提示] 未在 {IMG_DIR} 找到{label}付款码（文件名需含 {keyword}）")
        return
    if _open_image(path):
        print(f"  {label}：已打开预览 {path}")
    else:
        print(f"  [提示] 无法自动打开预览，请手动打开扫码：{path}")


def find_qr_images():
    """定位微信/支付宝付款码图片，返回 {"wechat": Path|None, "alipay": Path|None}。"""
    return {"wechat": _find_image("wechat"), "alipay": _find_image("alipay")}


def load_thumbnail(path, size=68):
    """把付款码图片缩放为 size×size 的方形缩略图（PIL Image）。

    供 GUI 在界面角落显示用；打开失败返回 None，绝不抛异常。
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.convert("RGB").resize((size, size))
    except Exception:
        return None


def print_donate_message() -> None:
    """打印请喝杯咖啡文案 + 打开微信/支付宝付款码预览。

    - .env 中 SHOW_DONATE=false 时整个函数空操作（不打印、不打开）。
    - 找不到图片 / 打开失败时只打印提示，绝不抛异常影响主流程。
    """
    try:
        if not _cfg.get_settings().show_donate:
            return
    except Exception:
        # 配置加载异常时不要让 donate 提示把主流程拖崩
        return

    print()
    print("=" * _BOX_WIDTH)
    print(DONATE_MESSAGE)
    print("=" * _BOX_WIDTH)

    _show_one("wechat", "微信支付")
    _show_one("alipay", "支付宝")
    print()
