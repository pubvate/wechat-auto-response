"""utils 模块测试：图像比对、坐标换算、区域裁剪、文件名、时间解析等纯函数。"""

import numpy as np
from PIL import Image, ImageDraw

from core import utils


def test_compare_images_identical():
    a = Image.new("RGB", (100, 100), (255, 255, 255))
    b = a.copy()
    assert utils.compare_images(a, b) is False  # 完全相同 -> 无变化


def test_compare_images_changed():
    a = Image.new("RGB", (200, 100), (255, 255, 255))
    b = a.copy()
    ImageDraw.Draw(b).rectangle([0, 0, 50, 100], fill=(0, 0, 0))
    assert utils.compare_images(a, b) is True


def test_compare_images_none():
    assert utils.compare_images(None, Image.new("RGB", (1, 1))) is True
    assert utils.compare_images(Image.new("RGB", (1, 1)), None) is True


def test_to_screen_point_retina():
    # 2x Retina：截图 800x1600，区域 400x800
    fake_img = Image.new("RGB", (800, 1600), (0, 0, 0))
    x, y = utils.to_screen_point((100, 200, 400, 800), fake_img, 400, 800)
    assert (x, y) == (300.0, 600.0)


def test_clamp_region_inside():
    assert utils.clamp_region(10, 20, 110, 120, (1920, 1080)) == (10, 20, 100, 100)


def test_clamp_region_out_of_bounds():
    left, top, w, h = utils.clamp_region(-50, -50, 2000, 2000, (1920, 1080))
    assert left == 0 and top == 0
    assert w == 1920 and h == 1080


def test_clamp_region_negative_size():
    # 反向对角点也应得到正的宽高
    left, top, w, h = utils.clamp_region(110, 120, 10, 20, (1920, 1080))
    assert w == 100 and h == 100


def test_sanitize_filename():
    assert utils.sanitize_filename("小明") == "小明"
    assert utils.sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert utils.sanitize_filename("   ") == "unknown"


def test_parse_time():
    assert utils.parse_time("2026-09-07T19:13:37.686523") is not None
    assert utils.parse_time("不是时间") is None
    assert utils.parse_time(None) is None


def test_preprocess_image_returns_image():
    img = Image.new("RGB", (64, 64), (200, 200, 200))
    out = utils.preprocess_image(img)
    assert isinstance(out, Image.Image)
    assert out.mode == "L"  # 灰度输出
