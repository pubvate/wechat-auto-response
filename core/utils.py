"""与业务无关的纯函数工具（图像 / 坐标 / 时间 / 文件名）。

这些函数无任何副作用、不依赖外部配置，便于单元测试。
"""

from datetime import datetime

import cv2
import numpy as np
from PIL import Image


def preprocess_image(image):
    """图像预处理：转灰度 + CLAHE 对比度增强，提升 OCR 识别准确率。"""
    img = np.array(image)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return Image.fromarray(enhanced)


def compare_images(img1, img2, threshold=50):
    """比较两张截图是否发生了变化（纯 numpy 运算，毫秒级）。

    任一为 None 时视为「有变化」。变化像素数超过 threshold 返回 True。
    """
    if img1 is None or img2 is None:
        return True
    img1_cv = cv2.cvtColor(np.array(img1), cv2.COLOR_RGB2GRAY)
    img2_cv = cv2.cvtColor(np.array(img2), cv2.COLOR_RGB2GRAY)
    diff = cv2.absdiff(img1_cv, img2_cv)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    change_pixels = np.sum(thresh) // 255
    return bool(change_pixels > threshold)


def parse_time(s):
    """安全解析 ISO 时间字符串，失败返回 None。"""
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def sanitize_filename(name):
    """联系人名转安全的文件名（去掉路径非法字符）。"""
    safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in name).strip()
    return safe or "unknown"


def clamp_region(x1, y1, x2, y2, screen_size):
    """由两个对角点计算区域，并裁剪到屏幕范围内，避免截图因无效矩形报错。

    返回 (left, top, width, height)。
    """
    screen_w, screen_h = screen_size
    left = max(0, min(min(x1, x2), screen_w - 1))
    top = max(0, min(min(y1, y2), screen_h - 1))
    width = max(1, min(abs(x1 - x2), screen_w - left))
    height = max(1, min(abs(y1 - y2), screen_h - top))
    return left, top, width, height


def to_screen_point(region, img, x_pixel, y_pixel):
    """把截图内像素坐标换算成屏幕逻辑坐标（点击用）。

    Retina 屏截图是 2x 像素，而点击用逻辑点坐标，须按 截图尺寸/区域尺寸 比例换算。
    """
    left, top, width, height = region
    scale_x = img.width / width
    scale_y = img.height / height
    return left + x_pixel / scale_x, top + y_pixel / scale_y
